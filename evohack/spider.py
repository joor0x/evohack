from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Iterable, List, Set, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, urlunparse

import requests

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


ASSET_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".ico", ".woff", ".woff2")


@dataclass
class SpiderConfig:
    base_url: str
    same_host_only: bool = True
    max_pages: int = 20
    max_depth: int = 1
    timeout: float = 6.0


def is_same_host(u1: str, u2: str) -> bool:
    p1, p2 = urlparse(u1), urlparse(u2)
    return (p1.scheme, p1.hostname, p1.port) == (p2.scheme, p2.hostname, p2.port)


def clean_url(url: str) -> str:
    p = urlparse(url)
    # normaliza removiendo fragment
    return urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))


def crawl(cfg: SpiderConfig) -> List[str]:
    if BeautifulSoup is None:
        return [cfg.base_url]
    seen: Set[str] = set()
    queue: List[Tuple[str, int]] = [(cfg.base_url, 0)]
    out: List[str] = []
    base = cfg.base_url
    while queue and len(out) < cfg.max_pages:
        url, depth = queue.pop(0)
        url = clean_url(url)
        if url in seen:
            continue
        seen.add(url)
        try:
            r = requests.get(url, timeout=cfg.timeout, headers={"Connection": "close", "User-Agent": "evohack-spider/0.1"})
            ct = r.headers.get("Content-Type", "")
            if r.status_code >= 400:
                continue
            if "text/html" not in ct:
                continue
            out.append(url)
            if depth >= cfg.max_depth:
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a"):
                href = (a.get("href") or "").strip()
                if not href or href.startswith("javascript:"):
                    continue
                child = urljoin(url, href)
                if child.lower().endswith(ASSET_EXT):
                    continue
                if cfg.same_host_only and not is_same_host(base, child):
                    continue
                if child not in seen:
                    queue.append((child, depth + 1))
        except requests.RequestException:
            continue
    return out


def derive_targets_from_urls(urls: Iterable[str]) -> List[dict]:
    # Devuelve definiciones simples de objetivos para GET con parámetros
    targets: List[dict] = []
    seen: Set[Tuple[str, str]] = set()
    for u in urls:
        p = urlparse(u)
        qs = parse_qs(p.query)
        for name in qs.keys():
            key = (clean_url(u), name)
            if key in seen:
                continue
            seen.add(key)
            targets.append({
                "url": clean_url(u),
                "method": "GET",
                "param_name": name,
            })
    return targets

