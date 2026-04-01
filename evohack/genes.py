from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse, urljoin, urlunparse


@dataclass
class CaptureRuleGene:
    name: str
    type: str  # 'json' or 'regex'
    path: Optional[str] = None
    pattern: Optional[str] = None


@dataclass
class StepGene:
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None
    param_name: Optional[str] = None
    path_template: Optional[str] = None
    captures: List[CaptureRuleGene] = field(default_factory=list)


@dataclass
class InsertionPoint:
    step_index: int = 0
    location: str = "auto"  # one of: body, param, header:<Name>, path, auto


@dataclass
class ScenarioGene:
    steps: List[StepGene]
    insertion: InsertionPoint = field(default_factory=InsertionPoint)
    max_steps: int = 3
    prefix_lock_len: int = 1  # first K steps considered locked when active


@dataclass
class PayloadGene:
    payload: str


def derive_scenario_from_target(url: str, method: str, headers: Optional[Dict[str, str]] = None,
                                body_template: Optional[str] = None, param_name: Optional[str] = None,
                                path_template: Optional[str] = None) -> ScenarioGene:
    step = StepGene(
        method=method.upper(),
        url=url,
        headers=headers or {},
        body_template=body_template,
        param_name=param_name,
        path_template=path_template,
    )
    # Choose insertion automatically based on available templates
    if body_template and "{payload}" in body_template:
        ins = InsertionPoint(step_index=0, location="body")
    elif param_name:
        ins = InsertionPoint(step_index=0, location="param")
    elif path_template and "{payload}" in path_template:
        ins = InsertionPoint(step_index=0, location="path")
    else:
        ins = InsertionPoint(step_index=0, location="auto")
    return ScenarioGene(steps=[step], insertion=ins)


def _same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlparse(a), urlparse(b)
        return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)
    except Exception:
        return False


def _base_root(u: str) -> str:
    try:
        p = urlparse(u)
        return urlunparse((p.scheme, p.netloc, "", "", "", "")) or u
    except Exception:
        return u


def derive_scenario_seeds_from_requests(reqs: List[Dict], base_url: str, max_seeds: int = 6) -> List[ScenarioGene]:
    seeds: List[ScenarioGene] = []
    base_root = _base_root(base_url)
    auth_posts: List[Dict] = []
    gets: List[str] = []
    # Collect candidates
    for r in reqs:
        try:
            url = r.get("url", "")
            method = (r.get("method", "GET") or "GET").upper()
        except Exception:
            continue
        if not url or not _same_origin(base_root, url):
            continue
        path = urlparse(url).path.lower()
        if method == "POST" and any(k in path for k in ["/login", "/register", "/users", "/user/login", "/session", "/auth"]):
            auth_posts.append(r)
        if method == "GET" and any(k in path for k in ["/api", "/rest", "/me", "/profile", "/admin", "/account"]):
            gets.append(url)

    # Build scenarios: auth + get
    for a in auth_posts:
        try:
            url = a.get("url")
            body = a.get("body") or ""
            # decide template
            bt = None
            headers: Dict[str, str] = {}
            if body and body.strip().startswith("{"):
                # JSON-like: choose common fields
                if any(k in body for k in ["email", "password"]):
                    bt = '{"email": "{payload}", "password": "{payload}"}'
                elif any(k in body for k in ["username", "login"]):
                    bt = '{"username": "{payload}", "password": "{payload}"}'
                else:
                    bt = '{"q": "{payload}"}'
                headers["Content-Type"] = "application/json"
            else:
                # Fallback: urlencoded param
                bt = None
            caps: List[CaptureRuleGene] = [
                CaptureRuleGene(name="token", type="json", path="authentication.token"),
                CaptureRuleGene(name="token", type="json", path="token"),
            ]
            s0 = StepGene(method="POST", url=url, headers=headers, body_template=bt, param_name=None if bt else "q", path_template=None, captures=caps)
            # choose a follow-up GET
            g = next((x for x in gets if x), None)
            if not g:
                continue
            s1 = StepGene(method="GET", url=g, headers={}, body_template=None, param_name=None, path_template=None)
            ins = InsertionPoint(step_index=0, location="body" if bt else "param")
            gene = ScenarioGene(steps=[s0, s1], insertion=ins, max_steps=3, prefix_lock_len=1)
            seeds.append(gene)
            if len(seeds) >= max_seeds:
                break
        except Exception:
            continue
    # If nothing, return empty list
    return seeds


def scenario_gene_from_dict(d: Dict) -> Optional[ScenarioGene]:
    try:
        steps_raw = d.get("steps") or []
        steps: List[StepGene] = []
        for s in steps_raw:
            caps = []
            for c in (s.get("captures") or []):
                try:
                    caps.append(CaptureRuleGene(name=c.get("name"), type=c.get("type"), path=c.get("path"), pattern=c.get("pattern")))
                except Exception:
                    continue
            steps.append(StepGene(
                method=str(s.get("method", "GET")),
                url=str(s.get("url", "")),
                headers=(s.get("headers") or {}),
                body_template=s.get("body_template"),
                param_name=s.get("param_name"),
                path_template=s.get("path_template"),
                captures=caps,
            ))
        ins_raw = d.get("insertion") or {}
        ins = InsertionPoint(step_index=int(ins_raw.get("step_index", 0)), location=str(ins_raw.get("location", "auto")))
        return ScenarioGene(steps=steps, insertion=ins)
    except Exception:
        return None
