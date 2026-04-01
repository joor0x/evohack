import re
import time
from typing import Tuple, Dict, Any, Optional

from .targets import TargetClient
from .llm import OllamaClient
from .scenario import Scenario, ScenarioRunner, ScenarioStep, CaptureRule
from .genes import ScenarioGene


class FitnessEvaluator:
    def __init__(self, target: TargetClient, llm: Optional[OllamaClient] = None, verbose: bool = False) -> None:
        self.target = target
        self.llm = llm
        self.verbose = verbose
        self._consec_status0 = 0
        self._prefix_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._cache_ttl_sec: float = 30.0

    def evaluate(self, payload: str, origin: Optional[str] = None, scenario_gene: Optional[ScenarioGene] = None) -> Tuple[float, Dict[str, Any]]:
        # If a ScenarioGene is provided, run that scenario; else use target directly
        if scenario_gene is not None:
            scen = self._scenario_from_gene(scenario_gene)
            # Prefix cache: reuse captured context (e.g., token) for shared prefixes
            prefix_fpr = self._prefix_fingerprint(scenario_gene)
            pre_ctx = None
            if prefix_fpr:
                now = time.time()
                ent = self._prefix_cache.get(prefix_fpr)
                if ent and (now - ent[0]) <= self._cache_ttl_sec:
                    pre_ctx = dict(ent[1])
                    if self.verbose:
                        print(f"[SCEN] Using cached context for prefix {prefix_fpr}")
            runner = ScenarioRunner(scenario=scen, timeout=self.target.timeout if hasattr(self.target, 'timeout') else 8.0, pre_context=pre_ctx)
            status, text, meta = runner.send(payload)
            # Tiered scoring: gate by prerequisites (all but last 2xx)
            steps = (meta or {}).get('steps') or []
            if isinstance(steps, list) and len(steps) >= 1:
                ok_prefix = all((s or {}).get('status', 0) in range(200, 300) for s in steps[:-1])
                if not ok_prefix:
                    # Hard gate: no fitness if setup failed
                    if self.verbose:
                        print("[SCEN] Tier1 fail: prerequisites not satisfied; fitness=0")
                    return 0.0, {"status": status, "llm": {}, "scenario": {"tier1": False, "steps": steps}}
            # Update prefix cache with new captures
            if prefix_fpr and isinstance(steps, list):
                ctx_vars: Dict[str, Any] = {}
                for s in steps:
                    caps = (s or {}).get('captures') or {}
                    if isinstance(caps, dict):
                        for k, v in caps.items():
                            ctx_vars[k] = v
                if ctx_vars:
                    self._prefix_cache[prefix_fpr] = (time.time(), ctx_vars)
            # Proceed to tier-2
        else:
            status, text, meta = self.target.send(payload)
        base = self._heuristic_score(status, text, payload, meta)

        # Track repeated connectivity errors (status=0) and warn
        if status == 0:
            self._consec_status0 += 1
            # Warn at 5, 10, 20, then every additional 20
            if self._consec_status0 in (5, 10, 20) or (self._consec_status0 > 20 and self._consec_status0 % 20 == 0):
                print(
                    f"[WARN] {self._consec_status0} consecutive status=0 responses; check connectivity or target availability: "
                    f"{getattr(self.target, 'method', 'GET')} {getattr(self.target, 'url', '')}. "
                    f"If using Juice Shop, ensure it is running or use --auto-juice."
                )
        else:
            if self._consec_status0 >= 5 and self.verbose:
                print(f"[INFO] Connectivity recovered after {self._consec_status0} consecutive status=0")
            self._consec_status0 = 0
        llm_boost = 0.0
        explain = None
        if self.llm is not None:
            try:
                llm_score, explain = self.llm.score_response(payload, status, text, meta.get("json"))
                # Combina: base ponderado + aporte LLM, evitando eclipsar señales duras
                # 70% base + 30% LLM
                combined = 0.7 * base + 0.3 * llm_score
                if self.verbose:
                    el = meta.get("elapsed_ms") if isinstance(meta, dict) else None
                    snippet = (text or "")[:160]
                    try:
                        import re as _re
                        snippet = _re.sub(r"\s+", " ", snippet).strip()
                    except Exception:
                        pass
                    origin_txt = f" origin={origin}" if origin else ""
                    print(f"[EVAL]{origin_txt} status={status} heur={base:.1f} llm={llm_score:.1f} total={combined:.1f} elapsed_ms={el} snippet=\"{snippet}\"")
                return combined, {"status": status, "llm": {"score": llm_score, "explain": explain}, "origin": origin, "scenario": {"tier1": True} if scenario_gene is not None else {}}
            except Exception as e:
                explain = f"llm_error: {e}"
        if self.verbose:
            el = meta.get("elapsed_ms") if isinstance(meta, dict) else None
            snippet = (text or "")[:160]
            try:
                import re as _re
                snippet = _re.sub(r"\s+", " ", snippet).strip()
            except Exception:
                pass
            origin_txt = f" origin={origin}" if origin else ""
            print(f"[EVAL]{origin_txt} status={status} heur={base:.1f} elapsed_ms={el} snippet=\"{snippet}\"")
        return base, {"status": status, "llm": {"explain": explain} if explain else {}, "origin": origin, "scenario": {"tier1": True} if scenario_gene is not None else {}}

    def _scenario_from_gene(self, gene: ScenarioGene) -> Scenario:
        steps = []
        for s in gene.steps:
            caps = []
            try:
                for c in (s.captures or []):
                    caps.append(CaptureRule(name=c.name, type=c.type, path=c.path, pattern=c.pattern))
            except Exception:
                pass
            steps.append(ScenarioStep(
                method=s.method,
                url=s.url,
                headers=s.headers,
                body_template=s.body_template,
                param_name=s.param_name,
                path_template=s.path_template,
                captures=caps,
            ))
        return Scenario(steps=steps)

    def _prefix_fingerprint(self, gene: ScenarioGene) -> str:
        try:
            steps = gene.steps or []
            # default prefix: all but last step
            pref_len = max(0, min(len(steps) - 1, gene.prefix_lock_len or (len(steps) - 1)))
            if pref_len <= 0:
                pref_len = max(0, len(steps) - 1)
            tup = tuple((s.method.upper(), s.url) for s in steps[:pref_len])
            return ";".join([f"{m} {u}" for (m, u) in tup])
        except Exception:
            return ""

    def _heuristic_score(self, status: int, text: str, payload: str, meta: Optional[Dict[str, Any]] = None) -> float:
        # Basado en señales típicas
        if status == 0:
            return 5.0
        score = 0.0
        if status >= 500:
            score += 200.0
        elif 300 <= status < 400:
            score += 40.0
        elif status == 401 or status == 403:
            score += 30.0
        elif 200 <= status < 300:
            score += 50.0
        elif 400 <= status < 500:
            score += 10.0

        lower = text.lower()
        # Reflexión
        if payload and payload.lower() in lower:
            score += 60.0
        # Errores comunes
        error_hints = [
            "sql",
            "syntax error",
            "warning",
            "stack",
            "trace",
            "exception",
            "internal server error",
            "error occurred",
            "unhandled",
        ]
        if any(h in lower for h in error_hints):
            score += 80.0

        # Tokens / secretos
        if re.search(r"(jwt|token|authorization)", lower):
            score += 200.0
        # JWT-like structure
        if re.search(r"eyJ[a-zA-Z0-9_\-]+=*\.[a-zA-Z0-9_\-]+=*\.[a-zA-Z0-9_\-\.=]*", text):
            score += 160.0
        # Emails leaked
        if re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
            score += 80.0
        # Private key markers
        if '-----BEGIN RSA PRIVATE KEY-----' in text or '-----BEGIN OPENSSH PRIVATE KEY-----' in text:
            score += 400.0
        # Password/hash patterns
        if re.search(r"\$2[aby]\$[0-9]{2}\$[A-Za-z0-9./]{53}", text):  # bcrypt
            score += 220.0
        if re.search(r"\b[0-9a-fA-F]{32}\b", text):  # md5-ish
            score += 50.0
        if re.search(r"\b[0-9a-fA-F]{40}\b", text):  # sha1-ish
            score += 70.0
        # /etc/passwd like
        if re.search(r"root:x:0:0:", text) or re.search(r"/etc/passwd", lower):
            score += 350.0

        # Indicadores XSS
        if "<script" in lower or "onerror=" in lower or "alert(1)" in lower:
            score += 100.0
        # DOM XSS signals from BrowserTarget
        if meta and meta.get("dialog_fired"):
            score += 500.0  # confirmed XSS execution
        if meta and meta.get("dom_reflected"):
            score += 80.0

        # Cookies set
        if meta and isinstance(meta.get("headers"), dict):
            set_cookie = meta["headers"].get("Set-Cookie") or meta["headers"].get("set-cookie")
            if set_cookie:
                score += 60.0
                # auth cookies
                if re.search(r"(auth|session|token)=", set_cookie.lower()):
                    score += 120.0

        # Redirecciones potencialmente peligrosas
        if meta and 300 <= status < 400:
            headers = meta.get("headers", {}) or {}
            loc = headers.get("Location") or headers.get("location")
            if isinstance(loc, str):
                l = loc.lower()
                if l.startswith("http") and ("//" in l) and ("localhost" not in l and "127.0.0.1" not in l):
                    score += 120.0
                if payload and payload.lower() in l:
                    score += 80.0

        # Latencia (DoS/NoSQL sleep)
        if meta and isinstance(meta.get("elapsed_ms"), int):
            ms = meta["elapsed_ms"]
            if ms >= 4000:
                score += 140.0
            elif ms >= 1500:
                score += 60.0

        return score
