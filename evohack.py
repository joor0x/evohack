#!/usr/bin/env python3
import argparse
import json
import os
import random
import signal
import sys
import time
from typing import List, Optional

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    pass

from evohack.ga import GeneticAlgorithm
from evohack.targets import (
    TargetClient,
    BrowserTarget,
    JuiceShopLoginProfile,
    JuiceShopSearchProfile,
    JuiceShopFeedbackProfile,
    JuiceShopUploadProfile,
)
from evohack.spider import SpiderConfig, crawl, derive_targets_from_urls
from evohack.spider_dynamic import dynamic_crawl, derive_targets_from_requests
from evohack.js_static import extract_endpoints_from_js, derive_get_targets_from_endpoints
from evohack.fitness import FitnessEvaluator
from evohack.llm import OllamaClient, OpenAIClient, AnthropicClient, AisuiteClient, OllamaNotAvailable
from evohack.memory import EvoMemory
from evohack.classifier import classify_categories
from evohack.scenario import Scenario, ScenarioRunner


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="EvoHack-LLM: Evolutionary Algorithms + LLM for pentesting",
    )
    p.add_argument("--url", help="Target URL (endpoint)")
    p.add_argument("--method", default="POST", help="HTTP method (GET/POST)")
    p.add_argument(
        "--profile",
        choices=["juice-login", "juice-search", "juice-feedback", "juice-upload", "custom"],
        default="juice-login",
        help="Preconfigured target profile",
    )
    p.add_argument(
        "--headers",
        default=None,
        help='JSON headers (e.g. {"Content-Type":"application/json"})',
    )
    p.add_argument(
        "--body-template",
        default=None,
        help="JSON body template with {payload} in the field to inject",
    )
    p.add_argument(
        "--param-name",
        default=None,
        help="Parameter name (for GET or form) where to insert payload",
    )
    p.add_argument(
        "--path-template",
        default=None,
        help="URL template with {payload} for path injection (e.g. http://host/base/{payload})",
    )
    p.add_argument(
        "--header-name",
        default=None,
        help="Inject payload into a specific header (e.g. X-Forwarded-Host)",
    )
    p.add_argument("--pop", type=int, default=24, help="Population size")
    p.add_argument("--gen", type=int, default=15, help="Number of generations")
    p.add_argument("--seed", type=int, default=42, help="Random seed")
    p.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="HTTP request timeout in seconds",
    )
    p.add_argument(
        "--llm-fitness",
        action="store_true",
        help="Use LLM (Ollama) for contextual fitness scoring",
    )
    p.add_argument(
        "--llm-provider",
        choices=["ollama", "openai", "anthropic"],
        default=os.environ.get("LLM_PROVIDER", "ollama"),
        help="Proveedor LLM: ollama, openai o anthropic",
    )
    p.add_argument(
        "--model",
        default="llama3.2",
        help="Nombre de modelo para el proveedor elegido (p.ej., llama3.2 | gpt-4o-mini | claude-3-haiku-20240307)",
    )
    p.add_argument(
        "--openai-base-url",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        help="OpenAI base URL (p.ej. https://api.openai.com/v1 o endpoint de Azure/compatibles)",
    )
    p.add_argument(
        "--anthropic-base-url",
        default=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        help="Anthropic base URL",
    )
    p.add_argument(
        "--seed-model",
        default=None,
        help="Ollama model to use for initial seeds (optional, defaults to --model if active)",
    )
    p.add_argument(
        "--seed-provider",
        choices=["ollama", "openai", "anthropic"],
        default=None,
        help="Proveedor LLM para semillas (por defecto usa --llm-provider)",
    )
    p.add_argument(
        "--ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        help="Ollama host (e.g. http://localhost:11434)",
    )
    p.add_argument(
        "--seed-ollama-host",
        default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        help="Ollama host for seeds (defaults to same as --ollama-host)",
    )
    p.add_argument(
        "--use-llm-mutation",
        action="store_true",
        help="Use LLM for mutation and crossover (if available)",
    )
    p.add_argument(
        "--llm-seed-ratio",
        type=float,
        default=0.0,
        help="Portion of initial population to generate with LLM (0.0..1.0)",
    )
    p.add_argument(
        "--llm-seed-count",
        type=int,
        default=None,
        help="Exact number of initial seeds to generate with LLM (takes priority over ratio)",
    )
    p.add_argument(
        "--mutation-rate",
        type=float,
        default=None,
        help="Override mutation rate per child (0.0..1.0, default 0.35)",
    )
    p.add_argument(
        "--crossover-rate",
        type=float,
        default=None,
        help="Override crossover rate per pair (0.0..1.0, default 0.60)",
    )
    p.add_argument(
        "--llm-offspring-per-gen",
        type=int,
        default=None,
        help="Override number of LLM-guided children per generation (default 4; spider/path default 3)",
    )
    p.add_argument(
        "--silent",
        action="store_true",
        help="Suppress verbose output (verbose is on by default)",
    )
    p.add_argument(
        "--auto-juice",
        action="store_true",
        help="Attempt to start OWASP Juice Shop with Docker on localhost:3000",
    )
    p.add_argument("--spider", action="store_true", help="Crawl links and test GET parameters")
    p.add_argument("--spider-depth", type=int, default=1, help="Crawl depth")
    p.add_argument("--spider-max-pages", type=int, default=20, help="Max pages to visit")
    p.add_argument("--spider-render", action="store_true", help="Use Playwright to render SPA and capture XHR/fetch")
    p.add_argument("--spider-static-js", action="store_true", help="Analyze JS bundles to extract /api and /rest endpoints")
    p.add_argument("--top", type=int, default=5, help="Number of top fitness targets to show/save in spider mode")
    p.add_argument("--min-fitness", type=float, default=None, help="Minimum fitness threshold to include targets in spider results")
    p.add_argument("--path-bruteforce", action="store_true", help="Test sensitive paths using {payload} in base URL")
    p.add_argument(
        "--path-prefixes",
        default=None,
        help="Comma-separated path prefixes (e.g. assets,public,static,uploads) to combine with {payload}",
    )
    p.add_argument(
        "--seed-categories",
        default="all",
        help="Comma-separated seed categories (inj_sql,xss,ssti,ssrf,xxe,lfi,redir,jwt,headers,inj_nosql,all)",
    )
    p.add_argument(
        "--seeds-file",
        default=None,
        help="Path to JSON with category->seed list to extend or replace",
    )
    p.add_argument(
        "--out",
        default=None,
        help="JSON output path to save results (spider or single run)",
    )
    p.add_argument(
        "--scenario",
        default=None,
        help="Path to multi-step (JSON) scenario with steps and captures; payload inserts as {payload}",
    )
    p.add_argument(
        "--instruction",
        default=None,
        help="High-level instruction/goal to guide seeds and LLM ops (e.g. 'Login as an admin')",
    )
    p.add_argument(
        "--use-render-endpoints",
        action="store_true",
        help="When --llm-context-scrape finds RENDER_ENDPOINTS, also run short GAs against those endpoints",
    )
    p.add_argument(
        "--render-endpoints-max",
        type=int,
        default=6,
        help="Max endpoints derived from render context to test (default 6)",
    )
    # Memory / ChromaDB
    p.add_argument("--memory-enable", action="store_true", help="Enable ChromaDB memory to store/reuse high-fitness payloads")
    p.add_argument("--memory-dir", default=os.environ.get("EVOHACK_MEMORY_DIR", ".evohack_chroma"), help="ChromaDB persistence directory")
    p.add_argument("--memory-top-n", type=int, default=8, help="Number of memory seeds to add for similar context")
    p.add_argument("--store-min-fitness", type=float, default=150.0, help="Minimum fitness to store payloads in memory")
    p.add_argument(
        "--llm-context-scrape",
        action="store_true",
        help="Fetch target HTML and same-origin JS to build a short context summary for LLM prompts",
    )
    p.add_argument(
        "--auto-scenario",
        action="store_true",
        help="Evolve multi-step scenarios alongside payloads (Phase 1 experimental)",
    )
    p.add_argument("--max-steps", type=int, default=3, help="Max steps for evolved scenarios (default 3)")
    p.add_argument("--prefix-lock-len", type=int, default=1, help="Lock first K steps during early generations (default 1)")
    p.add_argument("--prefix-lock-gen", type=int, default=5, help="Generations to enforce prefix lock (default 5)")
    p.add_argument(
        "--scenario-model",
        default=None,
        help="Optional LLM model to use specifically for scenario seeding (defaults to --model)",
    )
    p.add_argument(
        "--scenario-ollama-host",
        default=None,
        help="Optional Ollama host for scenario seeding (defaults to --ollama-host)",
    )
    p.add_argument(
        "--scenario-provider",
        choices=["ollama", "openai", "anthropic"],
        default=None,
        help="Proveedor LLM para escenarios (por defecto usa --llm-provider)",
    )
    return p


def setup_target(args: argparse.Namespace) -> TargetClient:
    headers = {}
    if args.headers:
        try:
            headers = json.loads(args.headers)
        except Exception:
            print("[WARN] Invalid --headers format, ignoring.")

    if args.profile == "juice-login":
        profile = JuiceShopLoginProfile(base_url=args.url or "http://localhost:3000")
        client = TargetClient.from_profile(profile, timeout=args.timeout)
        if args.header_name:
            client.with_header_injection(args.header_name)
        return client
    if args.profile == "juice-search":
        profile = JuiceShopSearchProfile(base_url=args.url or "http://localhost:3000")
        client = TargetClient.from_profile(profile, timeout=args.timeout)
        if args.header_name:
            client.with_header_injection(args.header_name)
        return client
    if args.profile == "juice-feedback":
        profile = JuiceShopFeedbackProfile(base_url=args.url or "http://localhost:3000")
        client = TargetClient.from_profile(profile, timeout=args.timeout)
        if args.header_name:
            client.with_header_injection(args.header_name)
        return client
    if args.profile == "juice-upload":
        profile = JuiceShopUploadProfile(base_url=args.url or "http://localhost:3000")
        client = TargetClient.from_profile(profile, timeout=args.timeout)
        client.with_file_upload("file", file_name_template="payload.txt")
        if args.header_name:
            client.with_header_injection(args.header_name)
        return client
    else:
        if not args.url:
            raise SystemExit("--url is required for profile=custom")
        client = TargetClient(
            url=args.url,
            method=args.method.upper(),
            headers=headers,
            body_template=args.body_template,
            param_name=args.param_name,
            path_template=args.path_template,
            timeout=args.timeout,
        )
        if args.header_name:
            client.with_header_injection(args.header_name)
        return client


def maybe_start_juice_shop(auto: bool) -> None:
    if not auto:
        return
    import shutil
    import subprocess
    if not shutil.which("docker"):
        print("[INFO] Docker not found in PATH; skipping --auto-juice.")
        return
    try:
        print("[INFO] Launching Juice Shop in docker (may take a while first time)...")
        subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "-p",
                "3000:3000",
                "--name",
                "evohack-juice",
                "bkimminich/juice-shop",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            try:
                import socket
                with socket.create_connection(("127.0.0.1", 3000), timeout=1.5):
                    print("[INFO] Juice Shop is up at http://localhost:3000")
                    return
            except Exception:
                time.sleep(1)
        print("[WARN] Could not verify Juice Shop on 3000 after 60s.")
    except Exception as e:
        print(f"[WARN] Could not start Juice Shop automatically: {e}")


def build_seed_payloads(args: argparse.Namespace) -> List[str]:
    from evohack.seeds import build_seeds_from_categories
    cats = [c.strip() for c in (args.seed_categories or "").split(",") if c.strip()]
    seeds = build_seeds_from_categories(cats or ["all"])
    if args.seeds_file:
        try:
            with open(args.seeds_file, "r", encoding="utf-8") as fh:
                import json as _json
                custom = _json.load(fh)
                for k, arr in (custom or {}).items():
                    if isinstance(arr, list):
                        seeds.extend([str(x) for x in arr])
            seeds = list(dict.fromkeys(seeds))
        except Exception as e:
            print(f"[WARN] Could not load --seeds-file: {e}")
    return seeds


def main(argv: Optional[List[str]] = None) -> int:
    args = build_argparser().parse_args(argv)
    args.verbose = not args.silent
    random.seed(args.seed)

    if args.auto_juice and (args.profile == "juice-login"):
        maybe_start_juice_shop(True)

    memory = EvoMemory(args.memory_dir) if args.memory_enable else None

    def _make_llm(provider: str, model: str, *, ollama_host: Optional[str] = None, openai_base: Optional[str] = None, anthropic_base: Optional[str] = None):
        prov = (provider or "ollama").lower()
        try:
            if prov == "openai":
                return AisuiteClient(
                    provider="openai",
                    model=model,
                    base_url=openai_base or args.openai_base_url,
                )
            if prov == "anthropic":
                return AisuiteClient(
                    provider="anthropic",
                    model=model,
                    base_url=anthropic_base or args.anthropic_base_url,
                )
            if prov == "ollama":
                return AisuiteClient(provider="ollama", model=model, api_url=ollama_host or args.ollama_host)
        except Exception as e:
            print(f"[WARN] aisuite init failed for provider {prov}: {e}")
        # fallback to native clients if aisuite fails
        if prov == "openai":
            return OpenAIClient(model=model, base_url=openai_base or args.openai_base_url)
        if prov == "anthropic":
            return AnthropicClient(model=model, base_url=anthropic_base or args.anthropic_base_url)
        return OllamaClient(model=model, host=ollama_host or args.ollama_host)

    llm = None
    if args.llm_fitness or args.use_llm_mutation:
        try:
            llm = _make_llm(args.llm_provider, args.model)
        except Exception as e:
            print(f"[WARN] LLM init failed: {e}. Continuing without LLM.")

    # Multi-step scenario
    if args.scenario:
        scen = Scenario.from_file(args.scenario)
        target = ScenarioRunner(scenario=scen, timeout=args.timeout)
    else:
        target = setup_target(args)
    fitness = FitnessEvaluator(target=target, llm=llm if args.llm_fitness else None, verbose=args.verbose)

    seeds = build_seed_payloads(args)

    # Spider mode: crawl and run GA for each GET target with query
    if args.spider:
        base = args.url or "http://localhost:3000"
        cfg = SpiderConfig(
            base_url=base,
            max_depth=args.spider_depth,
            max_pages=args.spider_max_pages,
        )
        urls = crawl(cfg)
        candidates = derive_targets_from_urls(urls)
        results = []
        for i, c in enumerate(candidates, 1):
            t = TargetClient(
                url=c["url"],
                method=c["method"],
                param_name=c.get("param_name"),
                timeout=args.timeout,
            )
            fit = FitnessEvaluator(target=t, llm=llm if args.llm_fitness else None, verbose=args.verbose)
            ctx = {
                "target": f"{t.method} {t.url} param={getattr(t,'param_name',None)} header={getattr(t,'header_name',None)}",
                "categories": classify_categories(t.url, t.method, getattr(t, 'header_name', None), getattr(t, 'param_name', None), getattr(t, 'body_template', None)),
                "url": t.url,
                "method": t.method,
            }
            if args.instruction:
                ctx["instruction"] = args.instruction
            _mr = args.mutation_rate if args.mutation_rate is not None else 0.35
            _cr = args.crossover_rate if args.crossover_rate is not None else 0.60
            _llm_off = args.llm_offspring_per_gen if args.llm_offspring_per_gen is not None else 3
            local_seeds = list(seeds)
            if memory is not None:
                try:
                    mem_pairs = memory.top_for_context(ctx, limit=max(1, args.memory_top_n))
                    mem_payloads = [p for (p, _m) in mem_pairs]
                    if mem_payloads:
                        local_seeds = list(dict.fromkeys(mem_payloads + local_seeds))
                        if args.verbose:
                            print(f"[MEM] (+spider) Added {len(mem_payloads)} seeds from memory for target {t.url}")
                except Exception as e:
                    if args.verbose:
                        print(f"[WARN] Memory seeding failed: {e}")
            ga = GeneticAlgorithm(
                fitness=fit,
                seed_population=local_seeds,
                population_size=max(8, min(24, args.pop)),
                use_llm=bool(llm and args.use_llm_mutation),
                llm=llm,
                verbose=args.verbose,
                mutation_rate=_mr,
                crossover_rate=_cr,
                llm_offspring_per_gen=_llm_off,
                context=ctx,
            )
            best = ga.run(generations=max(2, min(6, args.gen)))
            results.append((c, best))
            if memory is not None and best and (best.fitness is not None) and best.fitness >= args.store_min_fitness:
                try:
                    memory.add(best.payload, float(best.fitness), ctx)
                    if args.verbose:
                        print(f"[MEM] Stored spider best payload fitness={best.fitness}")
                except Exception as e:
                    if args.verbose:
                        print(f"[WARN] Memory store failed: {e}")
        # Sort by fitness and show top 5
        results.sort(key=lambda x: x[1].fitness, reverse=True)
        print("\n=== Spider Results (Top 5) ===")
        for (c, best) in results[:5]:
            print(f"Target: {c['method']} {c['url']}?{c.get('param_name','')}")
            print(f"  Best: {best.payload} | Fitness: {best.fitness}")
        return 0

    # Context: scenario vs. single target
    if isinstance(target, ScenarioRunner):
        ctx = {
            "target": f"SCENARIO steps={len(target.scenario.steps)}",
            "categories": ["inj_sql", "jwt", "headers", "xss"],
            "scenario": True,
            "scenario_steps": len(target.scenario.steps),
        }
    else:
        ctx = {
            "target": f"{target.method} {target.url} param={getattr(target,'param_name',None)} header={getattr(target,'header_name',None)}",
            "categories": classify_categories(target.url, target.method, getattr(target, 'header_name', None), getattr(target, 'param_name', None), getattr(target, 'body_template', None)),
        }
    if args.instruction:
        ctx["instruction"] = args.instruction
    if args.verbose:
        ctx["verbose"] = True

    # Optional HTML/JS scrape to enrich prompts
    if args.llm_context_scrape:
        try:
            from evohack.context import scrape_html_js_context, render_html_js_context
            # Try root of the host for richer UI context
            from urllib.parse import urlparse, urlunparse
            try:
                purl = urlparse(target.url)
                root = urlunparse((purl.scheme, purl.netloc, "/", "", "", ""))
            except Exception:
                root = target.url
            if args.verbose:
                print(f"[SCRAPE] Fetching HTML/JS context from {root}")
            ctx_scrape = scrape_html_js_context(root, timeout=args.timeout)
            snippet = (ctx_scrape or {}).get("summary_text") or ""
            if snippet:
                ctx["html_js_context"] = snippet
                if args.verbose:
                    print(f"[SCRAPE] Context length={len(snippet)}")
                    print("[SCRAPE] Context:\n" + snippet)
            # If context looks too sparse, try rendering the exact URL (SPA)
            if len(snippet or "") < 200:
                try:
                    if args.verbose:
                        print(f"[SCRAPE] Rendering DOM for {args.url or target.url}")
                    rctx = render_html_js_context(args.url or target.url, timeout_ms=int(args.timeout * 1000))
                    rs = (rctx or {}).get("summary_text") or ""
                    if rs:
                        ctx["html_js_context"] = rs
                        if args.verbose:
                            print(f"[SCRAPE] Rendered context length={len(rs)}")
                            print("[SCRAPE] Rendered Context:\n" + rs)
                except Exception as e2:
                    if args.verbose:
                        print(f"[WARN] Rendered context failed: {e2}")
        except Exception as e:
            if args.verbose:
                print(f"[WARN] Context scrape failed: {e}")
    # Auto-retarget: use LLM to pick the best endpoint for the instruction
    _browser_target_active = False
    if args.llm_context_scrape and args.instruction and llm and ctx.get("html_js_context"):
        _render_eps = []
        _hash_routes = []
        for _ln in (ctx.get("html_js_context") or "").splitlines():
            _ln = _ln.strip()
            if _ln.startswith("RENDER_ENDPOINTS:") or _ln.startswith("JS_ENDPOINTS:") or _ln.startswith("INLINE_JS_ENDPOINTS:"):
                _part = _ln.split(":", 1)[1]
                for _u in _part.split(";"):
                    _u = _u.strip()
                    if _u and _u not in _render_eps:
                        _render_eps.append(_u)
            elif _ln.startswith("HASH_ROUTES:"):
                _part = _ln.split(":", 1)[1]
                for _u in _part.split(";"):
                    _u = _u.strip()
                    if _u and _u not in _hash_routes:
                        _hash_routes.append(_u)

        # For DOM XSS instructions, prefer hash routes with {payload}
        _instr_lower = (args.instruction or "").lower()
        _is_xss = any(kw in _instr_lower for kw in ["xss", "cross-site", "script injection", "dom injection"])
        _xss_hash_candidates = [r for r in _hash_routes if "{payload}" in r] if _is_xss else []

        if _xss_hash_candidates:
            # Use BrowserTarget for DOM XSS via hash routes
            if args.verbose:
                print(f"[RETARGET] DOM XSS mode: found {len(_xss_hash_candidates)} hash route(s) with query params")
            # Pick best candidate: ask LLM or use first match
            _all_candidates = _xss_hash_candidates + _render_eps
            try:
                _selected = llm.select_endpoint(_all_candidates, args.instruction, ctx.get("html_js_context", ""))
                _best_url = (_selected or {}).get("url", "")
            except Exception:
                _best_url = ""
            # If LLM picked a hash route, use BrowserTarget; otherwise default to first
            _chosen = None
            for _c in _xss_hash_candidates:
                if _best_url and _best_url.rstrip("/") in _c:
                    _chosen = _c
                    break
            if not _chosen:
                _chosen = _xss_hash_candidates[0]
            if args.verbose:
                print(f"[RETARGET] Using BrowserTarget for DOM XSS: {_chosen}")
            target = BrowserTarget(url_template=_chosen, timeout=args.timeout)
            fitness = FitnessEvaluator(target=target, llm=llm if args.llm_fitness else None, verbose=args.verbose)
            ctx["target"] = f"BROWSER GET {_chosen}"
            ctx["categories"] = ["xss", "xss_polyglot"]
            _browser_target_active = True
            if args.verbose:
                print(f"[RETARGET] New target: {ctx['target']}")
                print(f"[RETARGET] New categories: {ctx['categories']}")
        elif _render_eps:
            # Fall back to REST endpoint retargeting (previous behavior)
            if args.verbose:
                print(f"[RETARGET] Found {len(_render_eps)} endpoints, asking LLM to select best for: {args.instruction}")
            try:
                _selected = llm.select_endpoint(_render_eps, args.instruction, ctx.get("html_js_context", ""))
                if _selected and _selected.get("url"):
                    _sel_url = _selected["url"]
                    _sel_method = (_selected.get("method") or "GET").upper()
                    _sel_param = _selected.get("param_name")
                    _sel_body = _selected.get("body_template")
                    # Strip leftover query string from URL if param_name is set
                    if _sel_param and "?" in _sel_url:
                        _sel_url = _sel_url.split("?", 1)[0]
                    if args.verbose:
                        print(f"[RETARGET] LLM selected: {_sel_method} {_sel_url} param={_sel_param} body={_sel_body}")
                    target = TargetClient(
                        url=_sel_url,
                        method=_sel_method,
                        param_name=_sel_param,
                        body_template=_sel_body,
                        timeout=args.timeout,
                    )
                    fitness = FitnessEvaluator(target=target, llm=llm if args.llm_fitness else None, verbose=args.verbose)
                    ctx["target"] = f"{target.method} {target.url} param={getattr(target, 'param_name', None)} header={getattr(target, 'header_name', None)}"
                    ctx["categories"] = classify_categories(target.url, target.method, getattr(target, 'header_name', None), getattr(target, 'param_name', None), getattr(target, 'body_template', None))
                    if args.verbose:
                        print(f"[RETARGET] New target: {ctx['target']}")
                        print(f"[RETARGET] New categories: {ctx['categories']}")
                elif args.verbose:
                    print("[RETARGET] LLM did not select a different endpoint, keeping original target")
            except Exception as _e:
                if args.verbose:
                    print(f"[WARN] Auto-retarget failed: {_e}")

    # Optional: derive endpoints from rendered context and test them too
    if args.use_render_endpoints and ctx.get("html_js_context"):
        try:
            import re as _re
            from urllib.parse import urlparse as _urlparse
            # Find the RENDER_ENDPOINTS line and split URLs
            lines = [ln.strip() for ln in (ctx.get("html_js_context") or "").splitlines()]
            urls = []
            for ln in lines:
                if ln.startswith("RENDER_ENDPOINTS:"):
                    part = ln.split(":", 1)[1]
                    for u in part.split(";"):
                        u = u.strip()
                        if u and u not in urls:
                            urls.append(u)
            urls = urls[: max(1, args.render_endpoints_max)]
            if urls and args.verbose:
                print(f"[RENDER] Using {len(urls)} endpoints from render context")
            render_results = []
            for u in urls:
                try:
                    # Derive GET target with query param if present
                    param_name = None
                    base_url = u
                    if "?" in u:
                        base_url, qs = u.split("?", 1)
                        if "=" in qs:
                            param_name = qs.split("=", 1)[0]
                    if not param_name:
                        # Skip endpoints without obvious query param for now
                        if args.verbose:
                            print(f"[RENDER] Skip (no query param): {u}")
                        continue
                    t = TargetClient(
                        url=base_url,
                        method="GET",
                        param_name=param_name,
                        timeout=args.timeout,
                    )
                    fit = FitnessEvaluator(target=t, llm=llm if args.llm_fitness else None, verbose=args.verbose)
                    rctx = {
                        "target": f"GET {base_url} param={param_name}",
                        "categories": classify_categories(base_url, "GET", None, param_name, None),
                        "url": base_url,
                        "method": "GET",
                    }
                    if args.instruction:
                        rctx["instruction"] = args.instruction
                    # Memory seeds per endpoint
                    local_seeds = list(seeds)
                    if memory is not None:
                        try:
                            mem_pairs = memory.top_for_context(rctx, limit=max(1, args.memory_top_n))
                            mem_payloads = [p for (p, _m) in mem_pairs]
                            if mem_payloads:
                                local_seeds = list(dict.fromkeys(mem_payloads + local_seeds))
                                if args.verbose:
                                    print(f"[MEM] (+render) Added {len(mem_payloads)} seeds for {base_url}")
                        except Exception as e:
                            if args.verbose:
                                print(f"[WARN] Memory seeding failed: {e}")
                    _mr = args.mutation_rate if args.mutation_rate is not None else 0.35
                    _cr = args.crossover_rate if args.crossover_rate is not None else 0.60
                    ga_r = GeneticAlgorithm(
                        fitness=fit,
                        seed_population=local_seeds,
                        population_size=max(8, min(20, args.pop)),
                        use_llm=bool(llm and args.use_llm_mutation),
                        llm=llm,
                        verbose=args.verbose,
                        mutation_rate=_mr,
                        crossover_rate=_cr,
                        llm_offspring_per_gen=3,
                        llm_crossover_k=3,
                        context=rctx,
                    )
                    rbest = ga_r.run(generations=max(2, min(6, args.gen)))
                    render_results.append((u, rbest))
                    if memory is not None and rbest and (rbest.fitness is not None) and rbest.fitness >= args.store_min_fitness:
                        try:
                            memory.add(rbest.payload, float(rbest.fitness), rctx)
                            if args.verbose:
                                print(f"[MEM] Stored render best payload fitness={rbest.fitness}")
                        except Exception as e:
                            if args.verbose:
                                print(f"[WARN] Memory store failed: {e}")
                except Exception as e:
                    if args.verbose:
                        print(f"[WARN] Render endpoint failed: {e}")
            if render_results:
                render_results.sort(key=lambda x: x[1].fitness, reverse=True)
                top_n = min(5, len(render_results))
                print(f"\n=== Render Endpoints (Top {top_n}) ===")
                for (u, best_r) in render_results[:top_n]:
                    print(f"Endpoint: {u}")
                    print(f"  Best: {best_r.payload} | Fitness: {best_r.fitness}")
        except Exception as e:
            if args.verbose:
                print(f"[WARN] Could not use render endpoints: {e}")
    # LLM seeding (optional): generate a portion of initial individuals with context
    if (args.llm_seed_count and args.llm_seed_count > 0) or (args.llm_seed_ratio and args.llm_seed_ratio > 0):
        try:
            # choose model: seed-model if given, else reuse main model (if available)
            seed_model = args.seed_model or args.model
            seed_provider = args.seed_provider or args.llm_provider
            seed_llm = _make_llm(seed_provider, seed_model, ollama_host=args.seed_ollama_host or args.ollama_host)
            if args.llm_seed_count and args.llm_seed_count > 0:
                want = max(1, min(args.pop, int(args.llm_seed_count)))
            else:
                want = max(1, int(args.pop * min(1.0, max(0.0, args.llm_seed_ratio))))
            cats = ctx.get("categories", []) if 'ctx' in locals() else []
            target_str = ctx.get("target", "") if 'ctx' in locals() else f"{getattr(target,'method', '')} {getattr(target, 'url', '')}"
            if args.verbose:
                print(f"[SEED] Using LLM for initial seeds: provider={seed_provider} model={seed_model} n={want}")
                if args.instruction:
                    print(f"[SEED] Instruction: {args.instruction}")
            seed_payloads = seed_llm.generate_seeds(categories=cats, target=target_str, n=want, instruction=args.instruction, html_js_context=ctx.get("html_js_context"))
            # Prepend LLM seeds to bias initial population
            seeds = list(dict.fromkeys(seed_payloads + seeds))
        except Exception as e:
            if args.verbose:
                print(f"[WARN] LLM seeding failed: {e}")

    # Memory seeding (optional): add stored high-fitness payloads for similar context
    if 'ctx' in locals() and memory is not None:
        try:
            mem_pairs = memory.top_for_context(ctx, limit=max(1, args.memory_top_n))
            mem_payloads = [p for (p, _m) in mem_pairs]
            if mem_payloads:
                seeds = list(dict.fromkeys(mem_payloads + seeds))
                if args.verbose:
                    print(f"[MEM] Added {len(mem_payloads)} seeds from memory")
        except Exception as e:
            if args.verbose:
                print(f"[WARN] Memory seeding failed: {e}")

    if args.verbose:
        _mr = args.mutation_rate if args.mutation_rate is not None else 0.35
        _cr = args.crossover_rate if args.crossover_rate is not None else 0.60
        _llm_off = args.llm_offspring_per_gen if args.llm_offspring_per_gen is not None else 4
        print("[RUN] Context:")
        print(f"  target: {ctx.get('target')}")
        print(f"  categories: {ctx.get('categories')}")
        if args.instruction:
            print(f"  instruction: {args.instruction}")
        print(f"  population: {args.pop} generations: {args.gen}")
        print(f"  rates: crossover={_cr:.2f} mutation={_mr:.2f} llm_offspring/gen={_llm_off}")
        print(f"  llm_mutation: {bool(llm and args.use_llm_mutation)} llm_fitness: {bool(llm and args.llm_fitness)} model: {getattr(llm, 'model', None) if llm else None}")

    # Scenario-evolution mode (Phase 1): split genome and run dedicated GA
    if args.auto_scenario:
        from evohack.genes import derive_scenario_from_target, derive_scenario_seeds_from_requests, scenario_gene_from_dict
        from evohack.ga_scenario import GeneticAlgorithmScenario
        # Seed scenarios from the current single target
        seed_gene = derive_scenario_from_target(
            url=target.url,
            method=target.method,
            headers=getattr(target, 'headers', None),
            body_template=getattr(target, 'body_template', None),
            param_name=getattr(target, 'param_name', None),
            path_template=getattr(target, 'path_template', None),
        )
        seed_gene.max_steps = max(1, args.max_steps)
        seed_gene.prefix_lock_len = max(0, args.prefix_lock_len)
        scenario_seeds = [seed_gene]
        # Try Playwright dynamic crawl to derive 2-step seeds from request log
        try:
            if args.verbose:
                print("[SCEN] Dynamic crawl: starting Playwright capture for scenario seeds...")
            dr = dynamic_crawl(base_url=args.url or target.url, max_pages=min(12, args.spider_max_pages if hasattr(args, 'spider_max_pages') else 12))
            if args.verbose:
                try:
                    rq_n = len(getattr(dr, 'requests', []) or [])
                    pg_n = len(getattr(dr, 'pages', []) or [])
                    print(f"[SCEN] Dynamic crawl: done. requests={rq_n} pages={pg_n}")
                except Exception:
                    print("[SCEN] Dynamic crawl: done.")
            if getattr(dr, 'requests', None):
                derived = derive_scenario_seeds_from_requests(dr.requests, args.url or target.url, max_seeds=6)
                # Apply user caps to derived seeds
                for g in derived:
                    g.max_steps = max(1, args.max_steps)
                    g.prefix_lock_len = max(0, args.prefix_lock_len)
                # Deduplicate by shape (methods+paths)
                seen = set()
                uniq: list = []
                for g in derived:
                    key = tuple((s.method, s.url) for s in g.steps)
                    if key in seen:
                        continue
                    seen.add(key)
                    uniq.append(g)
                scenario_seeds.extend(uniq)
                if args.verbose:
                    print(f"[SCEN] Derived scenario seeds from requests: {len(uniq)}")
        except Exception as e:
            if args.verbose:
                print(f"[WARN] Could not derive scenarios from dynamic crawl: {e}")
        if args.verbose:
            print(f"[SCEN] Auto-scenario seeds: {len(scenario_seeds)} (base steps={len(seed_gene.steps)}) max_steps={seed_gene.max_steps} lock_len={seed_gene.prefix_lock_len} lock_gen={args.prefix_lock_gen}")
        # LLM-based scenario seeding from context
        if args.use_llm_mutation:
            try:
                want = 4
                scen_model = args.scenario_model or args.model
                scen_provider = args.scenario_provider or args.llm_provider
                scen_llm = _make_llm(scen_provider, scen_model, ollama_host=args.scenario_ollama_host or args.ollama_host)
                if args.verbose:
                    print(f"[SCEN] LLM scenario seeding: requesting {want} candidates from model={scen_model}")
                scen_dicts = scen_llm.generate_scenario_seeds(context=ctx, n=want)
                llm_seeds = []
                for sd in scen_dicts:
                    gene = scenario_gene_from_dict(sd)
                    if not gene:
                        continue
                    gene.max_steps = max(1, args.max_steps)
                    gene.prefix_lock_len = max(0, args.prefix_lock_len)
                    llm_seeds.append(gene)
                # de-dup vs existing
                seen = set(tuple((s.method, s.url) for s in g.steps) for g in scenario_seeds)
                add = []
                for g in llm_seeds:
                    key = tuple((s.method, s.url) for s in g.steps)
                    if key not in seen:
                        seen.add(key)
                        add.append(g)
                if add:
                    scenario_seeds.extend(add)
                if args.verbose:
                    print(f"[SCEN] LLM scenario seeding: {len(add)} added (received {len(llm_seeds)})")
            except Exception as e:
                if args.verbose:
                    print(f"[WARN] LLM scenario seeding failed: {e}")
        # Build scenario GA and run
        _mr = args.mutation_rate if args.mutation_rate is not None else 0.35
        _cr = args.crossover_rate if args.crossover_rate is not None else 0.60
        if args.verbose:
            print(f"[SCEN] Starting Scenario GA: pop={args.pop} gen={args.gen} seeds={len(scenario_seeds)}")
        ga_s = GeneticAlgorithmScenario(
            fitness=fitness,
            seed_scenarios=scenario_seeds,
            seed_payloads=seeds,
            population_size=args.pop,
            mutation_rate=_mr,
            crossover_rate=_cr,
            tournament_k=3,
            use_llm=bool(llm and args.use_llm_mutation),
            llm=llm,
            verbose=args.verbose,
            prefix_lock_gen=max(0, args.prefix_lock_gen),
            cats=ctx.get("categories"),
            context=ctx,
        )
        best = ga_s.run(generations=args.gen)
        print("\n=== Best Individual (Scenario) ===")
        # Print scenario shape and payload
        print(f"Steps: {len(best.scenario.steps)} | Insertion: step={best.scenario.insertion.step_index} loc={best.scenario.insertion.location}")
        for i, s in enumerate(best.scenario.steps):
            print(f"  [{i}] {s.method} {s.url} param={s.param_name} body={'yes' if s.body_template else 'no'} path={'yes' if s.path_template else 'no'}")
        print(f"Payload: {best.payload.payload}")
        print(f"Fitness: {best.fitness}")
        # Store best scenario payload in memory
        if memory is not None and best and (best.fitness is not None) and best.fitness >= args.store_min_fitness:
            try:
                memory.add(best.payload.payload, float(best.fitness), ctx)
                if args.verbose:
                    print(f"[MEM] Stored scenario best payload fitness={best.fitness}")
            except Exception as e:
                if args.verbose:
                    print(f"[WARN] Memory store failed: {e}")
        return 0

    # Keep track of any LLM-generated seeds to tag origins
    _llm_seed_set = set(seed_payloads) if 'seed_payloads' in locals() else None

    _mr = args.mutation_rate if args.mutation_rate is not None else 0.35
    _cr = args.crossover_rate if args.crossover_rate is not None else 0.60
    _llm_off = args.llm_offspring_per_gen if args.llm_offspring_per_gen is not None else 4
    ga = GeneticAlgorithm(
        fitness=fitness,
        seed_population=seeds,
        population_size=args.pop,
        use_llm=bool(llm and args.use_llm_mutation),
        llm=llm,
        verbose=args.verbose,
        mutation_rate=_mr,
        crossover_rate=_cr,
        llm_offspring_per_gen=_llm_off,
        llm_crossover_k=3,
        context=ctx,
        llm_seed_set=_llm_seed_set,
    )

    def handle_sigint(signum, frame):
        print("\n[INFO] Interrupted by user. Exiting...")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    # Path brute-force: uses base URL and injects payload as path
    if args.path_bruteforce:
        base = args.url or "http://localhost:3000"
        # Build path templates (base + optional prefixes)
        templates = []
        base_tpl = (args.path_template or (base.rstrip("/") + "/{payload}"))
        templates.append((None, base_tpl))
        if args.path_prefixes:
            for raw in args.path_prefixes.split(","):
                pref = raw.strip().strip("/")
                if not pref:
                    continue
                tpl = base.rstrip("/") + "/" + pref + "/{payload}"
                templates.append((pref, tpl))

        results = []
        for (pref, tpl) in templates:
            t = TargetClient(
                url=base,
                method="GET",
                path_template=tpl,
                timeout=args.timeout,
            )
            fit = FitnessEvaluator(target=t, llm=llm if args.llm_fitness else None, verbose=args.verbose)
            ctx = {
                "target": f"GET {tpl}",
                "categories": ["osint", "backups", "logs", "secrets", "lfi"],
            }
            if args.instruction:
                ctx["instruction"] = args.instruction
            _mr = args.mutation_rate if args.mutation_rate is not None else 0.35
            _cr = args.crossover_rate if args.crossover_rate is not None else 0.60
            _llm_off = args.llm_offspring_per_gen if args.llm_offspring_per_gen is not None else 3
            ga = GeneticAlgorithm(
                fitness=fit,
                seed_population=seeds,
                population_size=max(8, min(24, args.pop)),
                use_llm=bool(llm and args.use_llm_mutation),
                llm=llm,
                verbose=args.verbose,
                mutation_rate=_mr,
                crossover_rate=_cr,
                llm_offspring_per_gen=_llm_off,
                llm_crossover_k=3,
                context=ctx,
            )
            best = ga.run(generations=max(3, args.gen))
            results.append({"prefix": pref, "template": tpl, "best": {"payload": best.payload, "fitness": best.fitness, "meta": best.meta}})
            if memory is not None and best and (best.fitness is not None) and best.fitness >= args.store_min_fitness:
                try:
                    memory.add(best.payload, float(best.fitness), ctx)
                    if args.verbose:
                        print(f"[MEM] Stored path best payload fitness={best.fitness}")
                except Exception as e:
                    if args.verbose:
                        print(f"[WARN] Memory store failed: {e}")

        # Filter by min-fitness and sort
        if args.min_fitness is not None:
            results = [r for r in results if (r["best"]["fitness"] or 0) >= args.min_fitness]
        results.sort(key=lambda r: r["best"]["fitness"], reverse=True)
        top_n = max(1, args.top)
        print(f"\n=== Path Bruteforce Results (Top {top_n}) ===")
        for r in results[:top_n]:
            print(f"Template: {r['template']} | BestPath: {r['best']['payload']} | Fitness: {r['best']['fitness']}")

        if args.out:
            try:
                import json as _json
                with open(args.out, "w", encoding="utf-8") as fh:
                    _json.dump({
                        "mode": "path_bruteforce",
                        "base_url": base,
                        "prefixes": [p for (p, _) in templates if p],
                        "results": results[:top_n],
                    }, fh, ensure_ascii=False, indent=2)
                print(f"[INFO] Result saved to {args.out}")
            except Exception as e:
                print(f"[WARN] Could not write --out: {e}")
        return 0

    # Advanced spider: traditional, render, and static JS
    if args.spider or args.spider_render or args.spider_static_js:
        base = args.url or "http://localhost:3000"
        candidates = []
        if args.spider:
            cfg = SpiderConfig(
                base_url=base,
                max_depth=args.spider_depth,
                max_pages=args.spider_max_pages,
            )
            urls = crawl(cfg)
            candidates.extend(derive_targets_from_urls(urls))
        if args.spider_render:
            dr = dynamic_crawl(base_url=base, max_pages=args.spider_max_pages)
            candidates.extend(derive_targets_from_requests(dr.requests))
        if args.spider_static_js:
            endpoints = extract_endpoints_from_js(base)
            candidates.extend(derive_get_targets_from_endpoints(endpoints))

        # de-dup candidates
        unique = []
        seen = set()
        for c in candidates:
            key = (c.get("method","GET"), c.get("url"), c.get("param_name"), c.get("body_template"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(c)
        if args.verbose:
            print(f"[SPIDER] Derived targets: {len(unique)}")

        results = []
        for i, c in enumerate(unique, 1):
            t = TargetClient(
                url=c["url"],
                method=c.get("method", "GET"),
                headers=c.get("headers"),
                body_template=c.get("body_template"),
                param_name=c.get("param_name"),
                timeout=args.timeout,
            )
            fit = FitnessEvaluator(target=t, llm=llm if args.llm_fitness else None, verbose=args.verbose)
            ctx = {
                "target": f"{t.method} {t.url} param={getattr(t,'param_name',None)} header={getattr(t,'header_name',None)}",
                "categories": classify_categories(t.url, t.method, getattr(t, 'header_name', None), getattr(t, 'param_name', None), getattr(t, 'body_template', None)),
            }
            if args.instruction:
                ctx["instruction"] = args.instruction
            ga = GeneticAlgorithm(
                fitness=fit,
                seed_population=seeds,
                population_size=max(8, min(24, args.pop)),
                use_llm=bool(llm and args.use_llm_mutation),
                llm=llm,
                verbose=args.verbose,
                llm_offspring_per_gen=3,
                llm_crossover_k=3,
                context=ctx,
            )
            best = ga.run(generations=max(2, min(6, args.gen)))
            results.append((c, best))
        # Filtrado por fitness mínimo si aplica
        if args.min_fitness is not None:
            results = [rb for rb in results if rb[1].fitness >= args.min_fitness]
        results.sort(key=lambda x: x[1].fitness, reverse=True)
        top_n = max(1, args.top)
        print(f"\n=== Spider Results (Top {top_n}) ===")
        for (c, best) in results[:top_n]:
            print(f"Target: {c.get('method','GET')} {c.get('url')} {('param='+c.get('param_name')) if c.get('param_name') else ''}")
            print(f"  Best: {best.payload} | Fitness: {best.fitness}")

        # Optional persistence
        if args.out:
            try:
                serial = []
                for (c, best) in results[:top_n]:
                    serial.append({
                        "target": c,
                        "best": {
                            "payload": best.payload,
                            "fitness": best.fitness,
                            "meta": best.meta,
                        },
                    })
                with open(args.out, "w", encoding="utf-8") as fh:
                    import json as _json
                    _json.dump({
                        "mode": "spider",
                        "base_url": args.url,
                        "results": serial,
                    }, fh, ensure_ascii=False, indent=2)
                print(f"[SPIDER] Results saved to {args.out}")
            except Exception as e:
                print(f"[WARN] Could not write --out: {e}")
        return 0

    best = ga.run(generations=args.gen)
    print("\n=== Best Individual ===")
    print(f"Payload: {best.payload}")
    print(f"Fitness: {best.fitness}")
    if best.meta:
        try:
            import json as _json
            print(f"Meta: {_json.dumps(best.meta, ensure_ascii=False)}")
        except Exception:
            pass
    # Optional persistence in single run
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                import json as _json
                _json.dump({
                    "mode": "single",
                    "target": {
                        "url": target.url,
                        "method": target.method,
                        "param_name": getattr(target, 'param_name', None),
                        "header_name": getattr(target, 'header_name', None),
                    },
                    "best": {
                        "payload": best.payload,
                        "fitness": best.fitness,
                        "meta": best.meta,
                    },
                }, fh, ensure_ascii=False, indent=2)
            print(f"[INFO] Result saved to {args.out}")
        except Exception as e:
            print(f"[WARN] Could not write --out: {e}")
    # Store in memory if enabled and above threshold
    if memory is not None and best and (best.fitness is not None) and best.fitness >= args.store_min_fitness:
        try:
            memory.add(best.payload, float(best.fitness), ctx)
            if args.verbose:
                print(f"[MEM] Stored best payload fitness={best.fitness}")
        except Exception as e:
            if args.verbose:
                print(f"[WARN] Memory store failed: {e}")
    # Clean up BrowserTarget if used
    if _browser_target_active and hasattr(target, 'close'):
        try:
            target.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
