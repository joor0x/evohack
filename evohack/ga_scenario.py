from __future__ import annotations
import random
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .llm import OllamaClient
from .fitness import FitnessEvaluator
from .genes import ScenarioGene, PayloadGene, StepGene, InsertionPoint
from .mutators import mutate_for_categories


@dataclass
class IndividualScenario:
    scenario: ScenarioGene
    payload: PayloadGene
    fitness: float = float("-inf")
    origin: str = "seed"
    meta: dict | None = None


class GeneticAlgorithmScenario:
    def __init__(
        self,
        fitness: FitnessEvaluator,
        seed_scenarios: List[ScenarioGene],
        seed_payloads: List[str],
        population_size: int = 24,
        mutation_rate: float = 0.35,
        crossover_rate: float = 0.6,
        tournament_k: int = 3,
        use_llm: bool = False,
        llm: Optional[OllamaClient] = None,
        verbose: bool = False,
        prefix_lock_gen: int = 5,
        cats: Optional[List[str]] = None,
        context: Optional[dict] = None,
    ) -> None:
        self.fitness = fitness
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.tournament_k = tournament_k
        self.use_llm = use_llm
        self.llm = llm
        self.verbose = verbose
        self.prefix_lock_gen = max(0, prefix_lock_gen)
        self.context = context or {}
        self.cats = cats or (self.context.get("categories") if isinstance(self.context, dict) else []) or []
        # Build initial population by pairing scenarios and payloads
        base: List[IndividualScenario] = []
        for i in range(population_size):
            s = random.choice(seed_scenarios)
            p = random.choice(seed_payloads) if seed_payloads else "'"
            base.append(IndividualScenario(scenario=s, payload=PayloadGene(p)))
        self.population: List[IndividualScenario] = base
        # Elite archive of scenario shapes
        self._archive: dict[str, IndividualScenario] = {}
        self._archive_k: int = 3
        # Derive endpoint candidates from context for step growth
        self._endpoint_candidates: List[str] = self._extract_endpoints_from_context()

    def run(self, generations: int = 12) -> IndividualScenario:
        self._evaluate_population(self.population)
        best = max(self.population, key=lambda i: i.fitness)
        if self.verbose:
            print(f"[GEN 0] best={best.fitness:.2f} payload={best.payload.payload}")
        for g in range(1, generations + 1):
            new_pop: List[IndividualScenario] = []
            while len(new_pop) < self.population_size:
                p1 = self._tournament_select()
                p2 = self._tournament_select()
                c1, c2 = self._crossover(p1, p2, g)
                if random.random() < self.mutation_rate:
                    c1 = self._mutate(c1, g)
                if random.random() < self.mutation_rate:
                    c2 = self._mutate(c2, g)
                new_pop.append(c1)
                if len(new_pop) < self.population_size:
                    new_pop.append(c2)
            self._evaluate_population(new_pop)
            self.population = self._elitism(self.population, new_pop)
            cb = max(self.population, key=lambda i: i.fitness)
            if cb.fitness > best.fitness:
                best = cb
            if self.verbose:
                print(f"[GEN {g}] best={cb.fitness:.2f} payload={cb.payload.payload}")
        return best

    def _elitism(self, old: List[IndividualScenario], new: List[IndividualScenario], keep: int = 2) -> List[IndividualScenario]:
        elites = sorted(old, key=lambda i: i.fitness, reverse=True)[:keep]
        # Bring top archive shapes back into the population
        arch = sorted(self._archive.values(), key=lambda i: i.fitness, reverse=True)[: self._archive_k]
        # Deduplicate by fingerprint
        seen = set()
        out: List[IndividualScenario] = []
        for ind in arch + elites:
            fp = self._fingerprint(ind.scenario)
            if fp in seen:
                continue
            seen.add(fp)
            out.append(ind)
        # Fill rest with best of new
        for ind in sorted(new, key=lambda i: i.fitness, reverse=True):
            if len(out) >= self.population_size:
                break
            fp = self._fingerprint(ind.scenario)
            if fp in seen:
                continue
            seen.add(fp)
            out.append(ind)
        return out

    def _evaluate_population(self, pop: List[IndividualScenario]) -> None:
        for ind in pop:
            score, meta = self.fitness.evaluate(ind.payload.payload, origin="scenario", scenario_gene=ind.scenario)
            ind.fitness = score
            ind.meta = meta or {}
            # Update archive with tier1 survivors and good fitness
            try:
                scen_meta = (meta or {}).get("scenario") or {}
                if scen_meta.get("tier1") and score > float("-inf"):
                    fp = self._fingerprint(ind.scenario)
                    cur = self._archive.get(fp)
                    if cur is None or score > cur.fitness:
                        # store a copy to avoid unintended mutation
                        self._archive[fp] = self._copy_ind(ind)
            except Exception:
                pass

    def _tournament_select(self) -> IndividualScenario:
        k = min(self.tournament_k, len(self.population))
        return max(random.sample(self.population, k), key=lambda i: i.fitness)

    def _copy_ind(self, ind: IndividualScenario) -> IndividualScenario:
        # Shallow copy of gene fields
        s = ScenarioGene(
            steps=[StepGene(method=x.method, url=x.url, headers=dict(x.headers), body_template=x.body_template, param_name=x.param_name, path_template=x.path_template) for x in ind.scenario.steps],
            insertion=InsertionPoint(step_index=ind.scenario.insertion.step_index, location=ind.scenario.insertion.location),
            max_steps=ind.scenario.max_steps,
            prefix_lock_len=ind.scenario.prefix_lock_len,
        )
        return IndividualScenario(scenario=s, payload=PayloadGene(ind.payload.payload), origin=ind.origin)

    def _crossover(self, a: IndividualScenario, b: IndividualScenario, gen: int) -> Tuple[IndividualScenario, IndividualScenario]:
        c1 = self._copy_ind(a)
        c2 = self._copy_ind(b)
        if random.random() >= self.crossover_rate:
            return c1, c2
        # Swap suffix of steps beyond locked prefix
        lock_len = a.scenario.prefix_lock_len if gen <= self.prefix_lock_gen else 0
        cut = max(lock_len, min(len(a.scenario.steps), len(b.scenario.steps)) // 2)
        c1.scenario.steps = a.scenario.steps[:cut] + b.scenario.steps[cut:]
        c2.scenario.steps = b.scenario.steps[:cut] + a.scenario.steps[cut:]
        # Insertion: keep from parent a and b respectively, fix bounds
        for c in (c1, c2):
            if c.scenario.insertion.step_index >= len(c.scenario.steps):
                c.scenario.insertion.step_index = len(c.scenario.steps) - 1
        # Payload crossover: simple single-point
        pa, pb = a.payload.payload, b.payload.payload
        if pa and pb:
            ia = random.randint(0, len(pa))
            ib = random.randint(0, len(pb))
            c1.payload.payload = pa[:ia] + pb[ib:]
            c2.payload.payload = pb[:ib] + pa[ia:]
        return c1, c2

    def _mutate(self, ind: IndividualScenario, gen: int) -> IndividualScenario:
        out = self._copy_ind(ind)
        # Payload mutation via LLM or fallback category mutators
        if self.use_llm and self.llm:
            try:
                m = self.llm.mutate(out.payload.payload, context=self.context)
                if m:
                    out.payload.payload = m
            except Exception:
                pass
        else:
            # Category-aware mutation if categories present
            if self.cats:
                m2 = mutate_for_categories(out.payload.payload, self.cats)
                if m2:
                    out.payload.payload = m2
            # Simple fallback
            out.payload.payload = self._mutate_simple(out.payload.payload)

        # Scenario mutation (constrained), avoid touching prefix if locked
        lock_len = out.scenario.prefix_lock_len if gen <= self.prefix_lock_gen else 0
        # Add/remove step occasionally to explore multi-step flows
        if len(out.scenario.steps) < out.scenario.max_steps and random.random() < 0.25:
            self._add_step(out, lock_len)
            return out
        if len(out.scenario.steps) > 1 and random.random() < 0.15:
            self._del_step(out, lock_len)
            return out

        # Choose a valid step index respecting lock; if lock covers all steps, pick last
        last_ix = len(out.scenario.steps) - 1
        start_ix = lock_len if lock_len <= last_ix else last_ix
        end_ix = last_ix
        idx = random.randint(start_ix, end_ix)
        step = out.scenario.steps[idx]
        op = random.choice(["method", "param_body", "ctype", "path", "insertion", "auth_header"])
        if op == "method":
            step.method = "POST" if step.method.upper() == "GET" else "GET"
        elif op == "param_body":
            # toggle between param and body JSON
            if step.param_name:
                # move to body JSON
                step.body_template = '{"q": "{payload}"}'
                step.param_name = None
                step.headers.setdefault("Content-Type", "application/json")
                out.scenario.insertion.location = "body"
            else:
                step.param_name = "q"
                step.body_template = None
                out.scenario.insertion.location = "param"
        elif op == "ctype":
            ct = step.headers.get("Content-Type")
            step.headers["Content-Type"] = "application/json" if ct != "application/json" else "application/x-www-form-urlencoded"
        elif op == "path":
            # inject payload into path template
            step.path_template = step.path_template or "{payload}"
            out.scenario.insertion.location = "path"
        elif op == "insertion":
            out.scenario.insertion.location = random.choice(["body", "param", "path"])
            out.scenario.insertion.step_index = idx
        elif op == "auth_header":
            # try to place payload into an auth header to probe protected endpoints
            step.headers["Authorization"] = "Bearer {payload}"
            out.scenario.insertion.location = "body" if (step.body_template and "{payload}" in step.body_template) else out.scenario.insertion.location
        return out

    def _mutate_simple(self, s: str) -> str:
        if not s:
            s = "'"
        op = random.choice(["insert", "delete", "replace"])
        arr = list(s)
        if op == "insert" and len(arr) < 256:
            pos = random.randint(0, len(arr))
            arr.insert(pos, random.choice(list("abcdefghijklmnopqrstuvwxyz0123456789'\"<>") ))
        elif op == "delete" and len(arr) > 1:
            pos = random.randint(0, len(arr) - 1)
            del arr[pos]
        elif op == "replace":
            pos = random.randint(0, len(arr) - 1)
            arr[pos] = random.choice(list("abcdefghijklmnopqrstuvwxyz0123456789'\"<>") )
        return "".join(arr)

    # --- helpers for step growth ---
    def _extract_endpoints_from_context(self) -> List[str]:
        ctx = self.context or {}
        snip = ctx.get("html_js_context") if isinstance(ctx, dict) else None
        if not snip:
            return []
        import re
        cands: List[str] = []
        # URLs or root-relative paths pointing to likely endpoints
        for m in re.finditer(r"(https?://[^\s'\"]+)|(/(?:api|rest|auth|admin|users|login|register)[^\s'\"]*)", snip, re.IGNORECASE):
            g = m.group(0)
            if g and g not in cands:
                cands.append(g)
            if len(cands) >= 20:
                break
        return cands

    def _base_root(self, url: str) -> str:
        try:
            from urllib.parse import urlparse, urlunparse
            p = urlparse(url)
            return urlunparse((p.scheme, p.netloc, "", "", "", ""))
        except Exception:
            return url

    def _fingerprint(self, scen: ScenarioGene) -> str:
        try:
            steps = scen.steps or []
            return "|".join([f"{s.method.upper()} {s.url}" for s in steps]) + f"|ins:{scen.insertion.step_index}:{scen.insertion.location}"
        except Exception:
            return ""

    def _add_step(self, ind: IndividualScenario, lock_len: int) -> None:
        # Add a new step at the end using a candidate endpoint (if available), else clone last
        base = self._base_root(ind.scenario.steps[0].url)
        url = None
        for c in self._endpoint_candidates:
            if c.startswith("/"):
                url = base.rstrip("/") + c
                break
            if c.startswith("http") and c.startswith(base):
                url = c
                break
        if url is None:
            url = ind.scenario.steps[-1].url
        new = StepGene(method="GET", url=url, headers={}, body_template=None, param_name=None, path_template=None)
        ind.scenario.steps.append(new)
        # If insertion is beyond bounds, clamp
        if ind.scenario.insertion.step_index >= len(ind.scenario.steps):
            ind.scenario.insertion.step_index = len(ind.scenario.steps) - 1

    def _del_step(self, ind: IndividualScenario, lock_len: int) -> None:
        if len(ind.scenario.steps) <= 1:
            return
        # remove a step beyond locked prefix
        idx = random.randint(lock_len, len(ind.scenario.steps) - 1)
        del ind.scenario.steps[idx]
        if ind.scenario.insertion.step_index >= len(ind.scenario.steps):
            ind.scenario.insertion.step_index = len(ind.scenario.steps) - 1
