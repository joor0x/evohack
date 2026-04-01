from __future__ import annotations
import re
from typing import List, Set
from urllib.parse import urljoin, urlparse, urlunparse

import requests
try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


API_PATTERNS = [
    re.compile(r"['\"](\/api\/[a-zA-Z0-9_\/-]+)['\"]"),
    re.compile(r"['\"](\/rest\/[a-zA-Z0-9_\/-]+)['\"]"),
]


def extract_endpoints_from_js(base_url: str, timeout: float = 8.0, max_scripts: int = 20) -> List[str]:
    if BeautifulSoup is None:
        return []
    try:
        r = requests.get(base_url, timeout=timeout, headers={"Connection": "close", "User-Agent": "evohack-js/0.1"})
        if r.status_code >= 400:
            return []
    except requests.RequestException:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    scripts = []
    for s in soup.find_all("script"):
        src = (s.get("src") or "").strip()
        if not src:
            continue
        scripts.append(urljoin(base_url, src))
        if len(scripts) >= max_scripts:
            break
    endpoints: Set[str] = set()
    for js in scripts:
        try:
            r = requests.get(js, timeout=timeout, headers={"Connection": "close", "User-Agent": "evohack-js/0.1"})
            if r.status_code >= 400:
                continue
            text = r.text
            for pat in API_PATTERNS:
                for m in pat.finditer(text):
                    endpoints.add(m.group(1))
        except requests.RequestException:
            continue
    # absolutiza
    abs_urls: Set[str] = set()
    for ep in endpoints:
        abs_urls.add(urljoin(base_url, ep))
    return list(abs_urls)


def derive_get_targets_from_endpoints(urls: List[str]) -> List[dict]:
    outs: List[dict] = []
    seen = set()
    for u in urls:
        p = urlparse(u)
        clean = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
        if clean in seen:
            continue
        seen.add(clean)
        outs.append({"url": clean, "method": "GET", "param_name": "q"})
    return outs

