from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Any, Iterable
from urllib.parse import urlparse, urljoin, parse_qs


@dataclass
class DynamicSpiderResult:
    requests: List[Dict[str, Any]]
    pages: List[str]


COMMON_BODY_FIELDS = [
    "q",
    "query",
    "search",
    "email",
    "username",
    "comment",
    "message",
]


def dynamic_crawl(base_url: str, max_pages: int = 15, timeout_ms: int = 10000) -> DynamicSpiderResult:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:  # pragma: no cover
        # Playwright no disponible
        return DynamicSpiderResult(requests=[], pages=[base_url])

    reqs: List[Dict[str, Any]] = []
    pages_seen: List[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        def on_request(request):
            try:
                url = request.url
                method = request.method
                post_data = request.post_data or ""
                reqs.append({"url": url, "method": method, "body": post_data})
            except Exception:
                pass

        page.on("request", on_request)

        def visit(u: str):
            try:
                page.goto(u, wait_until="domcontentloaded", timeout=timeout_ms)
                pages_seen.append(u)
                page.wait_for_timeout(500)
                # intenta hacer click en algunos enlaces visibles
                links = page.locator('a[href]')
                n = min(links.count(), 8)
                for i in range(n):
                    try:
                        href = links.nth(i).get_attribute("href") or ""
                        if href.startswith("javascript:"):
                            continue
                        absu = urljoin(u, href)
                        page.goto(absu, wait_until="domcontentloaded", timeout=timeout_ms)
                        pages_seen.append(absu)
                        page.wait_for_timeout(250)
                        if len(pages_seen) >= max_pages:
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        visit(base_url)
        browser.close()
    # de-dup pages
    pages_seen = list(dict.fromkeys(pages_seen))
    return DynamicSpiderResult(requests=reqs, pages=pages_seen)


def derive_targets_from_requests(reqs: Iterable[Dict[str, Any]]) -> List[dict]:
    from urllib.parse import urlunparse
    targets: List[dict] = []
    seen = set()
    for r in reqs:
        url = r.get("url", "")
        method = (r.get("method", "GET") or "GET").upper()
        body = r.get("body", "") or ""
        p = urlparse(url)
        # Solo mismo host/paths API típicos
        if not ("/api/" in p.path or "/rest/" in p.path):
            continue
        clean = urlunparse((p.scheme, p.netloc, p.path, "", p.query, ""))
        if method == "GET":
            qs = parse_qs(p.query)
            for name in qs.keys() or ["q"]:
                key = (clean, method, name)
                if key in seen:
                    continue
                seen.add(key)
                targets.append({"url": clean, "method": method, "param_name": name})
        else:
            # Heurística: intenta elegir un campo verosímil si el body es JSON
            field = None
            for cand in COMMON_BODY_FIELDS:
                if cand in body:
                    field = cand
                    break
            targets.append({
                "url": clean,
                "method": method,
                "headers": {"Content-Type": "application/json"},
                "body_template": ("{" + f'"{field or "q"}": "' + "{payload}" + '"}')
            })
    return targets

