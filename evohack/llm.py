import json
import os
import re
import shutil
import subprocess
from typing import Optional, Tuple, Any
import time

try:
    # Optional: load .env if present
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()  # harmless if no .env
except Exception:
    pass


class OllamaNotAvailable(RuntimeError):
    pass


class BaseLlmClient:
    """
    Common prompt builders and helpers. Subclasses must implement _generate(prompt:str)->str.
    """

    def _generate(self, prompt: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    # ----- Common prompt builders -----
    def _prompt_crossover(self, a: str, b: str, context: Optional[dict]) -> str:
        cats = ", ".join((context or {}).get("categories", [])) if context else ""
        target = (context or {}).get("target", "")
        instruction = (context or {}).get("instruction", "")
        ctx_snip = (context or {}).get("html_js_context", "")
        return (
            f"""
Eres un experto en seguridad ofensiva. Dadas dos cargas A y B, crea UNA variante más potente y discreta que combine ideas de ambas para explotar vulnerabilidades web. Prioriza categorías: {cats}. Target: {target}.
Instrucción/objetivo: {instruction}
Contexto HTML/JS (resumen): {ctx_snip}
Devuelve solo la cadena final sin comillas ni explicación.
A: {a}
B: {b}
""".strip()
        )

    def _prompt_mutate(self, payload: str, context: Optional[dict]) -> str:
        cats = ", ".join((context or {}).get("categories", [])) if context else ""
        target = (context or {}).get("target", "")
        instruction = (context or {}).get("instruction", "")
        ctx_snip = (context or {}).get("html_js_context", "")
        return (
            f"""
Eres un experto en payloads de inyección para webs. Dada la carga:
{payload}
Genera UNA sola variante potencialmente más efectiva para bypass de filtros. Prioriza categorías: {cats}. Target: {target}.
Instrucción/objetivo: {instruction}
Contexto HTML/JS (resumen): {ctx_snip}
Devuelve solo la variante, sin comillas ni explicación.
""".strip()
        )

    def _prompt_score(self, payload: str, status: int, text: str, json_body: Optional[Any]) -> str:
        snippet = text[:1200]
        jbrief = json.dumps(json_body)[:1200] if json_body is not None else "null"
        return (
            f"""
Actúa como analista de seguridad. Evalúa la efectividad del payload en base a la respuesta.
Devuelve JSON con campos: score (0..500) y explain (breve razón).
Reglas guía: errores 5xx y trazas suben score; reflexión del payload sugiere XSS; tokens o datos sensibles suben score; 200 vacío tiene menos score; 4xx bajo.
Payload: {payload}
Status: {status}
BodyText: {snippet}
BodyJSON: {jbrief}
""".strip()
        )

    def _prompt_crossover_many(self, parents: list[str], context: Optional[dict], n_children: int) -> str:
        target = (context or {}).get("target", "")
        cats = ", ".join((context or {}).get("categories", [])) if context else ""
        instruction = (context or {}).get("instruction", "")
        ctx_snip = (context or {}).get("html_js_context", "")
        example = "\n".join([f"P{i+1}: {p}" for i, p in enumerate(parents[:6])])
        return (
            f"""
Eres un generador de payloads ofensivos. Mezcla ideas de los siguientes payloads (no los repitas tal cual) y devuelve {n_children} variantes diversas y de alta efectividad contra categorías: {cats}. Target: {target}.
Instrucción/objetivo: {instruction}
Contexto HTML/JS (resumen): {ctx_snip}
Reglas:
- Sin explicación, solo una variante por línea.
- Evita eco literal del input; introduce pequeñas ofuscaciones o cambios de sintaxis.
{example}
""".strip()
        )

    def _prompt_seed_payloads(self, categories: list[str], target: str, n: int, instruction: Optional[str], html_js_context: Optional[str]) -> str:
        cats = ", ".join(categories or [])
        instr = instruction or ""
        ctx_snip = html_js_context or ""
        return (
            f"""
Eres un hacker web. Genera {n} payloads iniciales DIVERSOS (una por línea), adecuados para categorías: {cats}. Target: {target}.
Instrucción/objetivo: {instr}
Contexto HTML/JS (resumen): {ctx_snip}
Sin explicaciones, sin comillas, solo una cadena por línea.
""".strip()
        )

    def _prompt_scenario_seeds(self, context: Optional[dict], n: int) -> str:
        target = (context or {}).get("target", "")
        cats = ", ".join((context or {}).get("categories", [])) if context else ""
        instruction = (context or {}).get("instruction", "")
        ctx_snip = (context or {}).get("html_js_context", "")
        return (
            f"""
Eres un planificador de pruebas de seguridad para un laboratorio controlado y autorizado (localhost). Tu objetivo es ayudar a evaluar flujos de autenticación y autorización de manera defensiva.
Propon {n} escenarios de 2-3 pasos en formato JSON mínimo para probar el objetivo.
Objetivo: {target}
Categorías de interés: {cats}
Instrucción del test: {instruction}
Contexto HTML/JS (resumen): {ctx_snip}
Requisitos:
- Devuelve SOLO un arreglo JSON. Cada escenario con 'steps' (lista) e 'insertion' ({{"step_index":int, "location":"body|param|path|header:<Name>"}}).
- Cada step: method (GET/POST), url (absoluta o relativa al mismo host), headers (obj opcional), body_template o param_name o path_template.
- Añade 'captures' donde sea lógico (p. ej., extraer tokens): {{"name":"token","type":"json","path":"authentication.token"}} o {{"name":"csrf","type":"regex","pattern":"name=\\\"_csrf\\\" value=\\\"([^\\\"]+)\\\""}}.
- Inserta el payload usando {{payload}} en body_template/param/path según 'insertion'.
- No añadas explicaciones ni texto fuera del JSON.
""".strip()
        )

    # ----- Public high-level ops (shared) -----
    def crossover(self, a: str, b: str, context: Optional[dict] = None) -> Optional[str]:
        out = self._generate(self._prompt_crossover(a, b, context)).strip()
        return self._extract_payload(out)

    def mutate(self, payload: str, context: Optional[dict] = None) -> Optional[str]:
        out = self._generate(self._prompt_mutate(payload, context)).strip()
        return self._extract_payload(out)

    def score_response(self, payload: str, status: int, text: str, json_body: Optional[Any]) -> Tuple[float, str]:
        out = self._generate(self._prompt_score(payload, status, text, json_body))
        m = re.search(r"\{[\s\S]*\}", out)
        if m:
            try:
                obj = json.loads(m.group(0))
                s = float(obj.get("score", 0))
                return max(0.0, min(500.0, s)), str(obj.get("explain", ""))
            except Exception:
                pass
        return 0.0, out.strip()[:300]

    def crossover_many(self, parents: list[str], context: Optional[dict] = None, n_children: int = 5) -> list[str]:
        out = self._generate(self._prompt_crossover_many(parents, context, n_children))
        lines = [self._extract_payload(x) for x in out.splitlines()]
        return [x for x in lines if x]

    def _prompt_select_endpoint(self, endpoints: list[str], instruction: str, html_js_context: str) -> str:
        eps = "\n".join(f"  {i+1}. {ep}" for i, ep in enumerate(endpoints))
        return (
            f"""
You are a web security expert. Given these discovered endpoints and an attack instruction, select the BEST single endpoint to target.

Endpoints:
{eps}

Instruction: {instruction}
Context: {html_js_context}

Reply with ONLY a JSON object (no explanation):
{{"url": "<base_url_without_query>", "method": "GET or POST", "param_name": "<query_param_or_null>", "body_template": "<json_template_with_{{payload}}_or_null>"}}
Rules:
- For GET endpoints with query params (e.g. /search?q=), set method=GET, param_name to the param name, body_template to null, and url WITHOUT the query string.
- For POST endpoints, set body_template with {{{{payload}}}} in the injectable field, param_name to null.
- Pick the endpoint most relevant to the attack instruction (e.g. search/query endpoints for XSS, login endpoints for SQLi).
""".strip()
        )

    def select_endpoint(self, endpoints: list[str], instruction: str, html_js_context: str = "") -> Optional[dict]:
        if not endpoints:
            return None
        out = self._generate(self._prompt_select_endpoint(endpoints, instruction, html_js_context))
        m = re.search(r"\{[\s\S]*?\}", out)
        if m:
            try:
                obj = json.loads(m.group(0))
                if obj.get("url"):
                    # Normalize null strings
                    for k in ("param_name", "body_template"):
                        if obj.get(k) in (None, "null", "None", ""):
                            obj[k] = None
                    return obj
            except Exception:
                pass
        return None

    def generate_seeds(self, categories: list[str], target: str, n: int = 8, instruction: Optional[str] = None, html_js_context: Optional[str] = None) -> list[str]:
        out = self._generate(self._prompt_seed_payloads(categories, target, n, instruction, html_js_context))
        seeds = []
        for line in out.splitlines():
            s = self._extract_payload(line)
            if s:
                seeds.append(s)
        return seeds[: max(0, n)]

    def generate_scenario_seeds(self, context: Optional[dict] = None, n: int = 4) -> list[dict]:
        want_verbose = bool((context or {}).get("verbose"))
        out = self._generate(self._prompt_scenario_seeds(context, n))
        if want_verbose:
            try:
                sn = (out or "")[:600].replace("\n", " ")
                print(f"[SCEN][LLM] raw scenario output len={len(out)} head=\"{sn}\"")
            except Exception:
                pass
        import json as _json
        arr: list[dict] = []
        try:
            # Normalize fences
            txt = out.strip()
            txt = txt.replace("```json", "").replace("```", "").strip()
            # Try to extract a balanced JSON array
            start = txt.find('[')
            if start != -1:
                depth = 0
                end = -1
                for i, ch in enumerate(txt[start:], start):
                    if ch == '[':
                        depth += 1
                    elif ch == ']':
                        depth -= 1
                        if depth == 0:
                            end = i
                            break
                if end != -1:
                    txt = txt[start:end+1]
            arr = _json.loads(txt)
        except Exception:
            return []
        # Normalize: clamp to n and ensure structure
        def _norm_captures(val):
            caps = []
            try:
                if not val:
                    return []
                if isinstance(val, dict):
                    for k, v in val.items():
                        if isinstance(v, dict):
                            caps.append({
                                "name": v.get("name", k),
                                "type": v.get("type"),
                                "path": v.get("path"),
                                "pattern": v.get("pattern"),
                            })
                        else:
                            # treat scalar as json path
                            caps.append({"name": str(k), "type": "json", "path": str(v)})
                    return caps
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict):
                            # ensure name exists
                            name = item.get("name") or item.get("key") or item.get("id")
                            if name:
                                item = {**item, "name": name}
                            caps.append({
                                "name": item.get("name"),
                                "type": item.get("type"),
                                "path": item.get("path"),
                                "pattern": item.get("pattern"),
                            })
                        else:
                            # string path
                            caps.append({"name": None, "type": "json", "path": str(item)})
                    return caps
            except Exception:
                return []
            return []

        out_list: list[dict] = []
        for scen in arr:
            try:
                if not isinstance(scen, dict):
                    continue
                steps = scen.get("steps")
                insertion = scen.get("insertion") or {}
                if not isinstance(steps, list) or not steps:
                    continue
                # Keep only required fields; coerce types
                norm_steps = []
                for s in steps[:3]:
                    if not isinstance(s, dict):
                        continue
                    caps_val = s.get("captures") or []
                    norm_steps.append({
                        "method": str(s.get("method", "GET")).upper(),
                        "url": str(s.get("url", "")),
                        "headers": s.get("headers") or {},
                        "body_template": s.get("body_template"),
                        "param_name": s.get("param_name"),
                        "path_template": s.get("path_template"),
                        "captures": _norm_captures(caps_val),
                    })
                if not norm_steps:
                    continue
                ins_raw = insertion
                if isinstance(ins_raw, list):
                    # pick the first dict-like item if present
                    ins_raw = next((x for x in ins_raw if isinstance(x, dict)), {})
                elif isinstance(ins_raw, (str, int, float)):
                    # treat as step index, auto location
                    try:
                        ins_raw = {"step_index": int(ins_raw), "location": "auto"}
                    except Exception:
                        ins_raw = {}
                if not isinstance(ins_raw, dict):
                    ins_raw = {}
                ins = {
                    "step_index": int((ins_raw or {}).get("step_index", 0)),
                    "location": str((ins_raw or {}).get("location", "auto")),
                }
                out_list.append({"steps": norm_steps, "insertion": ins})
                if len(out_list) >= n:
                    break
            except Exception:
                # Skip malformed scenario entries instead of failing all
                continue
        return out_list

    # ----- Helpers -----
    def _extract_payload(self, text: str) -> Optional[str]:
        cleaned = text.strip()
        cleaned = cleaned.strip("`\n\r ")
        if cleaned.startswith("\"") and cleaned.endswith("\""):
            cleaned = cleaned[1:-1]
        if cleaned.startswith("'") and cleaned.endswith("'"):
            cleaned = cleaned[1:-1]
        cleaned = cleaned.strip()
        return cleaned or None


class OllamaClient(BaseLlmClient):
    def __init__(self, model: str = "llama3.2", host: str = "http://127.0.0.1:11434", timeout_s: Optional[int] = None) -> None:
        self.model = model
        self.host = host
        # Timeout for ollama invocations to avoid indefinite hangs (e.g., model pull)
        if timeout_s is None:
            # Allow override via env
            try:
                timeout_env = os.environ.get("EVOHACK_OLLAMA_TIMEOUT") or os.environ.get("OLLAMA_TIMEOUT")
                self.timeout_s = int(timeout_env) if timeout_env else 60
            except Exception:
                self.timeout_s = 60
        else:
            self.timeout_s = int(timeout_s)
        # Prefer python lib si existe, sino CLI
        self._py = self._load_python_client()
        if not self._py and not shutil.which("ollama"):
            raise OllamaNotAvailable("No se encontró cliente Python ni binario 'ollama'.")

    def _load_python_client(self):
        try:
            import ollama  # type: ignore

            return ollama
        except Exception:
            return None

    def _run_cli(self, prompt: str) -> str:
        env = os.environ.copy()
        # Propagate host for the Ollama CLI
        if self.host:
            env["OLLAMA_HOST"] = self.host
        cmd = ["ollama", "run", self.model]
        try:
            p = subprocess.run(
                cmd,
                input=prompt.encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"ollama run timed out after {self.timeout_s}s (model may be pulling). Consider pre-pulling with 'ollama pull {self.model}' or increase OLLAMA_TIMEOUT.")
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode(errors="ignore") or "ollama run failed")
        return p.stdout.decode()

    def _generate(self, prompt: str) -> str:
        if self._py:
            try:
                # Prefer explicit client with host if available
                if hasattr(self._py, "Client"):
                    client = self._py.Client(host=self.host)  # type: ignore[attr-defined]
                    res = client.generate(model=self.model, prompt=prompt)
                else:
                    # Fallback to module-level API (uses default host)
                    res = self._py.generate(model=self.model, prompt=prompt)
                # python client returns dict with 'response'
                if isinstance(res, dict) and 'response' in res:
                    return str(res['response'])
                return str(res)
            except Exception as e:
                # fallback to CLI
                pass
        return self._run_cli(prompt)


class OpenAIClient(BaseLlmClient):
    """Minimal OpenAI Chat Completions client using requests.

    Respects env vars: OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_ORG.
    """

    def __init__(self, model: str = "gpt-4o-mini", base_url: Optional[str] = None, api_key: Optional[str] = None, timeout_s: int = 60) -> None:
        self.model = model
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.org = os.environ.get("OPENAI_ORG")
        self.timeout_s = int(os.environ.get("EVOHACK_OPENAI_TIMEOUT", timeout_s))
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY no configurada")

    def _generate(self, prompt: str) -> str:
        import requests  # lazy import

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.org:
            headers["OpenAI-Organization"] = self.org
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        }
        r = requests.post(url, headers=headers, json=data, timeout=self.timeout_s)
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI error {r.status_code}: {r.text[:200]}")
        j = r.json()
        try:
            return j["choices"][0]["message"]["content"]
        except Exception:
            return json.dumps(j)


class AnthropicClient(BaseLlmClient):
    """Minimal Anthropic Messages client using requests.

    Respects env vars: ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL.
    """

    def __init__(self, model: str = "claude-3-haiku-20240307", base_url: Optional[str] = None, api_key: Optional[str] = None, timeout_s: int = 60) -> None:
        self.model = model
        self.base_url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.timeout_s = int(os.environ.get("EVOHACK_ANTHROPIC_TIMEOUT", timeout_s))
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY no configurada")

    def _generate(self, prompt: str) -> str:
        import requests  # lazy import

        url = f"{self.base_url.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        }
        r = requests.post(url, headers=headers, json=data, timeout=self.timeout_s)
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic error {r.status_code}: {r.text[:200]}")
        j = r.json()
        try:
            parts = j.get("content") or []
            texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
            return "\n".join([t for t in texts if t]) or json.dumps(j)
        except Exception:
            return json.dumps(j)


class AisuiteClient(BaseLlmClient):
    """Unified client using aisuite for providers: openai, anthropic, ollama.

    The provided `provider` should be one of: 'openai', 'anthropic', 'ollama'.
    Model should be the raw model name; this class will prefix provider as required by aisuite.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        api_url: Optional[str] = None,
        timeout_s: int = 60,
    ) -> None:
        from aisuite import Client as AIClient  # lazy import

        self.provider = provider.lower().strip()
        self.model = model
        self.timeout_s = int(timeout_s)

        # Build provider-specific config
        cfg: dict = {}
        if self.provider == "openai":
            cfg = {
                "api_key": api_key or os.environ.get("OPENAI_API_KEY"),
            }
            if base_url or os.environ.get("OPENAI_BASE_URL"):
                cfg["base_url"] = base_url or os.environ.get("OPENAI_BASE_URL")
        elif self.provider == "anthropic":
            cfg = {
                "api_key": api_key or os.environ.get("ANTHROPIC_API_KEY"),
            }
            if base_url or os.environ.get("ANTHROPIC_BASE_URL"):
                cfg["base_url"] = base_url or os.environ.get("ANTHROPIC_BASE_URL")
        elif self.provider == "ollama":
            # aisuite uses api_url for ollama
            cfg = {
                "api_url": api_url
                or os.environ.get("OLLAMA_API_URL")
                or os.environ.get("OLLAMA_HOST")
                or "http://127.0.0.1:11434",
                "timeout": int(os.environ.get("EVOHACK_OLLAMA_TIMEOUT", 60)),
            }
        else:
            raise RuntimeError(f"Proveedor aisuite no soportado: {self.provider}")

        self._ai = AIClient(provider_configs={self.provider: cfg})

    def _generate(self, prompt: str) -> str:
        # aisuite expects model as 'provider:model'
        model_id = f"{self.provider}:{self.model}"
        try:
            resp = self._ai.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            # Normalized or provider-native both have choices[0].message.content
            content = getattr(resp.choices[0].message, "content", None)
            return str(content) if content is not None else str(resp)
        except Exception as e:
            raise RuntimeError(f"aisuite error: {e}")
