import random
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from .llm import OllamaClient
from .fitness import FitnessEvaluator
from .mutators import mutate_for_categories, mutate_scenario_payload, crossover_scenario_payload


@dataclass
class Individual:
    payload: str
    fitness: float = float("-inf")
    meta: dict = field(default_factory=dict)
    origin: str = "seed"


class GeneticAlgorithm:
    def __init__(
        self,
        fitness: FitnessEvaluator,
        seed_population: List[str],
        population_size: int = 24,
        mutation_rate: float = 0.35,
        crossover_rate: float = 0.6,
        tournament_k: int = 3,
        use_llm: bool = False,
        llm: Optional[OllamaClient] = None,
        verbose: bool = False,
        llm_offspring_per_gen: int = 4,
        llm_crossover_k: int = 3,
        context: Optional[dict] = None,
        llm_seed_set: Optional[set[str]] = None,
    ) -> None:
        self.fitness = fitness
        self.population_size = population_size
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.tournament_k = tournament_k
        self.use_llm = use_llm
        self.llm = llm
        self.verbose = verbose
        self.context = context or {}
        self.llm_offspring_per_gen = max(0, llm_offspring_k if (llm_offspring_k := llm_offspring_per_gen) is not None else 0)
        self.llm_crossover_k = max(2, llm_crossover_k)
        self.alphabet = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'\"<>/()=;:-_#*{}$| &`!+[],.%@\\")
        # Inicializa población con seeds y variantes simples
        base = seed_population[:]
        while len(base) < population_size:
            s = random.choice(seed_population)
            base.append(self._mutate_simple(s))
        llm_seed_set = llm_seed_set or set()
        pop: List[Individual] = []
        for p in base[:population_size]:
            if p in llm_seed_set:
                pop.append(Individual(p, origin="seed_llm"))
            elif p not in seed_population:
                pop.append(Individual(p, origin="seed_variation"))
            else:
                pop.append(Individual(p, origin="seed"))
        self.population = pop
        self._last_mutation_kind: Optional[str] = None
        self._last_crossover_kind: Optional[str] = None

    def run(self, generations: int = 15) -> Individual:
        self._evaluate_population(self.population)
        best = max(self.population, key=lambda i: i.fitness)
        if self.verbose:
            print(f"[GEN 0] best={best.fitness:.2f} payload={best.payload}")

        for g in range(1, generations + 1):
            new_pop: List[Individual] = []
            # LLM-guided crossover across top-K parents to inject diverse children
            if self.use_llm and self.llm and self.llm_offspring_per_gen > 0:
                parents = sorted(self.population, key=lambda i: i.fitness, reverse=True)[: self.llm_crossover_k]
                parent_payloads = [p.payload for p in parents]
                try:
                    children = self.llm.crossover_many(parent_payloads, context=self.context, n_children=self.llm_offspring_per_gen)
                    for c in children:
                        new_pop.append(Individual(c, origin="llm_crossover"))
                    if self.verbose:
                        print(f"[GEN {g}] LLM crossover children={len(children)} from topK={self.llm_crossover_k}")
                except Exception:
                    pass
            while len(new_pop) < self.population_size:
                parent1 = self._tournament_select()
                parent2 = self._tournament_select()
                child1_payload, child2_payload = parent1.payload, parent2.payload
                child1_origin, child2_origin = "repro", "repro"

                if random.random() < self.crossover_rate:
                    child1_payload, child2_payload = self._crossover(parent1.payload, parent2.payload)
                    if self._last_crossover_kind and self._last_crossover_kind != "none":
                        child1_origin = f"xover_{self._last_crossover_kind}"
                        child2_origin = f"xover_{self._last_crossover_kind}"

                if random.random() < self.mutation_rate:
                    child1_payload = self._mutate(child1_payload)
                    if self._last_mutation_kind:
                        child1_origin = child1_origin + "+mut_" + self._last_mutation_kind
                if random.random() < self.mutation_rate:
                    child2_payload = self._mutate(child2_payload)
                    if self._last_mutation_kind:
                        child2_origin = child2_origin + "+mut_" + self._last_mutation_kind

                new_pop.append(Individual(child1_payload, origin=child1_origin))
                if len(new_pop) < self.population_size:
                    new_pop.append(Individual(child2_payload, origin=child2_origin))

            self._evaluate_population(new_pop)
            self.population = self._elitism(self.population, new_pop)
            current_best = max(self.population, key=lambda i: i.fitness)
            if current_best.fitness > best.fitness:
                best = current_best
            if self.verbose:
                print(f"[GEN {g}] best={current_best.fitness:.2f} payload={current_best.payload}")
        return best

    def _elitism(self, old: List[Individual], new: List[Individual], keep: int = 2) -> List[Individual]:
        elites = sorted(old, key=lambda i: i.fitness, reverse=True)[:keep]
        rest = sorted(new, key=lambda i: i.fitness, reverse=True)[: self.population_size - keep]
        return elites + rest

    def _evaluate_population(self, pop: List[Individual]) -> None:
        for ind in pop:
            score, meta = self.fitness.evaluate(ind.payload, origin=ind.origin)
            ind.fitness = score
            ind.meta = meta or {}

    def _tournament_select(self) -> Individual:
        k = min(self.tournament_k, len(self.population))
        contenders = random.sample(self.population, k)
        return max(contenders, key=lambda i: i.fitness)

    def _crossover(self, a: str, b: str) -> Tuple[str, str]:
        # Try scenario-level crossover first (if scenario context)
        if isinstance(self.context, dict) and self.context.get("scenario"):
            mixed = crossover_scenario_payload(a, b)
            if mixed:
                self._last_crossover_kind = "scenario"
                return mixed, b
        if self.use_llm and self.llm:
            try:
                mixed = self.llm.crossover(a, b, context=self.context)
                if mixed:
                    self._last_crossover_kind = "llm"
                    return mixed, b
            except Exception:
                pass
        # fallback: single-point crossover
        if not a or not b:
            self._last_crossover_kind = "none"
            return a, b
        cut_a = random.randint(0, len(a))
        cut_b = random.randint(0, len(b))
        self._last_crossover_kind = "simple"
        return a[:cut_a] + b[cut_b:], b[:cut_b] + a[cut_a:]

    def _mutate(self, payload: str) -> str:
        # Scenario-level mutation wrapper
        if isinstance(self.context, dict) and self.context.get("scenario"):
            try:
                out = mutate_scenario_payload(payload, self.context.get("scenario_steps"))
                self._last_mutation_kind = "scenario"
                return out
            except Exception:
                pass
        if self.use_llm and self.llm:
            try:
                out = self.llm.mutate(payload, context=self.context)
                if out:
                    self._last_mutation_kind = "llm"
                    return out
            except Exception:
                pass
        # Category-aware fallback mutators
        cats = self.context.get("categories") if isinstance(self.context, dict) else None
        if isinstance(cats, list) and cats:
            out2 = mutate_for_categories(payload, cats)
            if out2:
                self._last_mutation_kind = "category"
                return out2
        self._last_mutation_kind = "simple"
        return self._mutate_simple(payload)

    def _mutate_simple(self, payload: str) -> str:
        if not payload:
            payload = "'"
        op = random.choice(["insert", "delete", "replace", "dup"])
        s = list(payload)
        if op == "insert" and len(s) < 256:
            pos = random.randint(0, len(s))
            s.insert(pos, random.choice(self.alphabet))
        elif op == "delete" and len(s) > 1:
            pos = random.randint(0, len(s) - 1)
            del s[pos]
        elif op == "replace":
            pos = random.randint(0, len(s) - 1)
            s[pos] = random.choice(self.alphabet)
        elif op == "dup" and len(s) < 256:
            pos = random.randint(0, len(s) - 1)
            s.insert(pos, s[pos])
        return "".join(s)
