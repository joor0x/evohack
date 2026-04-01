from __future__ import annotations
import re
from typing import Dict, List, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


def _same_origin(a: str, b: str) -> bool:
    try:
        pa, pb = urlparse(a), urlparse(b)
        return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)
    except Exception:
        return False


def _summarize_forms(soup: BeautifulSoup, base_url: str, limit: int = 5) -> List[str]:
    lines: List[str] = []
    for i, f in enumerate(soup.find_all("form")[:limit], 1):
        method = (f.get("method") or "GET").upper()
        action_raw = f.get("action")
        action = action_raw or ""
        action_abs = urljoin(base_url, action)
        action_tag = action_abs if action_raw else f"{action_abs} (current)"
        fields = []
        for inp in f.find_all(["input", "select", "textarea"]):
            name = inp.get("name") or inp.get("id") or ""
            if not name:
                continue
            itype = inp.get("type") or inp.name
            fields.append(f"{name}:{itype}")
        fields_str = ",".join(fields[:12])
        lines.append(f"FORM[{i}]: {method} {action_tag} fields=[{fields_str}]")
    return lines


def _extract_query_params(soup: BeautifulSoup, base_url: str, limit: int = 10) -> List[str]:
    params: List[str] = []
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(base_url, href)
        if "?" in full:
            qs = full.split("?", 1)[1]
            for kv in qs.split("&"):
                k = kv.split("=", 1)[0]
                if k and k not in params:
                    params.append(k)
        if len(params) >= limit:
            break
    if params:
        return ["QUERY_PARAMS: " + ",".join(params[:limit])]
    return []


def _summarize_inputs(soup: BeautifulSoup, limit: int = 15) -> List[str]:
    targets = []
    for inp in soup.find_all("input"):
        name = inp.get("name") or inp.get("id")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in {"text", "password", "email", "search", "tel", "url", "number"}:
            targets.append(f"{name}:{itype}")
        if len(targets) >= limit:
            break
    if targets:
        return ["INPUTS: " + ",".join(targets[:limit])]
    return []


def _summarize_buttons(soup: BeautifulSoup, base_url: str, limit: int = 10) -> List[str]:
    lines: List[str] = []
    btns: List[str] = []
    # <button> and input[type=submit]
    for b in soup.find_all("button"):
        bid = b.get("id") or b.get("name")
        label = (b.get_text(" ") or b.get("value") or "").strip()
        role = (b.get("role") or "").strip()
        role_tag = f" (role={role})" if role else ""
        if bid or label:
            btns.append((bid or "-", (label[:40] if label else "").strip() + role_tag))
        if len(btns) >= limit:
            break
    if len(btns) < limit:
        for b in soup.find_all("input", {"type": "submit"}):
            bid = b.get("id") or b.get("name")
            label = b.get("value") or ""
            role = (b.get("role") or "").strip()
            role_tag = f" (role={role})" if role else ""
            if bid or label:
                btns.append((bid or "-", (label[:40] if label else "").strip() + role_tag))
            if len(btns) >= limit:
                break
    if btns:
        parts = [f"{i}:{t}" for (i, t) in btns[:limit]]
        lines.append("BUTTONS: " + "; ".join(parts))
    # Buttons with formaction or onclick URLs
    actions: List[str] = []
    for b in soup.find_all(["button", "input"]):
        fa = b.get("formaction") or b.get("data-action") or b.get("onclick")
        if not fa:
            continue
        m = re.search(r"https?://[^'\"\s)]+|/(?:[A-Za-z0-9_./-]+)", str(fa))
        if m:
            url = m.group(0)
            if url and url not in actions:
                actions.append(urljoin(base_url, url))
        if len(actions) >= 6:
            break
    if actions:
        lines.append("BUTTON_ACTIONS: " + "; ".join(actions[:6]))
    return lines


def _summarize_link_buttons(soup: BeautifulSoup, base_url: str, limit: int = 12) -> List[str]:
    lines: List[str] = []
    entries: List[tuple[str, str]] = []
    keywords = [
        "login",
        "logout",
        "register",
        "signup",
        "signin",
        "admin",
        "panel",
        "dashboard",
        "profile",
        "settings",
        "account",
        "reset",
        "forgot",
    ]
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        label = (a.get_text(" ") or a.get("aria-label") or "").strip()
        rid = a.get("id") or a.get("name")
        role = (a.get("role") or "").strip()
        cls = " ".join((a.get("class") or []))
        looks_button = (role == "button") or ("btn" in cls.lower())
        hit_kw = any(k in href.lower() or (label and k in label.lower()) for k in keywords)
        if looks_button or hit_kw:
            url = urljoin(base_url, href)
            tag = label[:40] if label else (rid or "-")
            role_tag = f" (role={role})" if role else ""
            entries.append((tag + role_tag, url))
        if len(entries) >= limit:
            break
    if entries:
        parts = [f"{t}: {u}" for (t, u) in entries[:limit]]
        lines.append("LINK_BUTTONS: " + "; ".join(parts))
    return lines


def _collect_keyword_ids(soup: BeautifulSoup, limit: int = 20) -> List[str]:
    ids: List[str] = []
    keywords = [
        "admin",
        "login",
        "signin",
        "signup",
        "register",
        "logout",
        "user",
        "username",
        "email",
        "password",
        "pass",
        "token",
        "auth",
        "csrf",
        "otp",
        "2fa",
        "mfa",
        "captcha",
        "code",
        "apikey",
        "api_key",
        "secret",
        "key",
        "session",
    ]
    for el in soup.find_all(attrs={"id": True}):
        vid = str(el.get("id"))
        if any(k in vid.lower() for k in keywords):
            ids.append(vid)
        if len(ids) >= limit:
            break
    if ids:
        return ["IDS: " + ",".join(ids[:limit])]
    return []


_ENDPOINT_RE = re.compile(
    r"(?:['\"][^'\"\n]*(?:/api|/rest|/auth|/admin)[^'\"\n]*['\"])|(?:fetch\s*\(\s*['\"][^'\"\n]+['\"])",
    re.IGNORECASE,
)


def _scan_js(text: str, limit: int = 12) -> List[str]:
    found: List[str] = []
    for m in _ENDPOINT_RE.finditer(text or ""):
        s = m.group(0)
        if s and s not in found:
            found.append(s.strip("'\""))
        if len(found) >= limit:
            break
    return found


def scrape_html_js_context(url: str, timeout: float = 8.0, max_js_fetch: int = 2) -> Dict[str, str]:
    summary_lines: List[str] = []
    try:
        r = requests.get(url, timeout=timeout)
        ct = r.headers.get("Content-Type", "")
        text = r.text or ""
    except Exception as e:
        return {"summary_text": f"SCRAPE_ERROR: {e}"}

    if "html" not in (ct or "").lower() and "<html" not in text.lower():
        # Not HTML, still scan for endpoints
        eps = _scan_js(text)
        if eps:
            summary_lines.append("JS_ENDPOINTS: " + "; ".join(eps[:8]))
        snippet = text[:400].replace("\n", " ").replace("\r", " ")
        summary_lines.append(f"BODY_SNIPPET: {snippet}")
        return {"summary_text": "\n".join(summary_lines)[:1600]}

    soup = BeautifulSoup(text, "html.parser")
    summary_lines.extend(_summarize_forms(soup, url))
    summary_lines.extend(_summarize_inputs(soup))
    summary_lines.extend(_summarize_buttons(soup, url))
    summary_lines.extend(_summarize_link_buttons(soup, url))
    summary_lines.extend(_collect_keyword_ids(soup))
    summary_lines.extend(_extract_query_params(soup, url))

    # Inline scripts scan
    inline_js = "\n".join(s.get_text("\n") for s in soup.find_all("script") if not s.get("src"))
    eps_inline = _scan_js(inline_js)
    if eps_inline:
        summary_lines.append("INLINE_JS_ENDPOINTS: " + "; ".join(eps_inline[:8]))

    # External scripts (same-origin only)
    js_srcs = []
    for s in soup.find_all("script"):
        src = s.get("src")
        if not src:
            continue
        full = urljoin(url, src)
        if _same_origin(url, full):
            js_srcs.append(full)
    js_srcs = js_srcs[:max_js_fetch]
    for jsurl in js_srcs:
        try:
            r2 = requests.get(jsurl, timeout=timeout)
            eps = _scan_js(r2.text or "")
            if eps:
                summary_lines.append(f"JS[{jsurl}]: " + "; ".join(eps[:8]))
        except Exception:
            continue

    # Build compact summary
    summary = "\n".join(summary_lines)
    return {"summary_text": summary[:1600]}


_HASH_ROUTE_RE = re.compile(
    r"""(?:path\s*:\s*['"]([^'"]+)['"])|(?:href\s*=\s*['"]#/([^'"?]+))""",
    re.IGNORECASE,
)

# Common SPA routes that accept query params (search, filter, etc.)
_QUERY_ROUTE_HINTS = {"search", "query", "find", "filter", "q", "lookup", "browse"}


def _extract_hash_routes_from_js(js_text: str, base_url: str, limit: int = 12) -> List[str]:
    """Extract SPA hash routes from JS bundles (Angular/React/Vue route defs)."""
    found: List[str] = []
    for m in _HASH_ROUTE_RE.finditer(js_text or ""):
        route = (m.group(1) or m.group(2) or "").strip("/")
        if not route or len(route) > 80 or route.startswith("http"):
            continue
        if route not in found:
            found.append(route)
        if len(found) >= limit:
            break
    return found


def render_html_js_context(url: str, timeout_ms: int = 10000) -> Dict[str, str]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception as e:  # pragma: no cover
        return {"summary_text": "RENDER_UNAVAILABLE: Playwright not installed"}

    summary_lines: List[str] = []
    hash_routes_seen: List[str] = []
    js_texts: List[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        # Collect request URLs to infer endpoints
        req_urls: List[str] = []
        def on_request(request):
            try:
                req_urls.append(request.url)
            except Exception:
                pass
        page.on("request", on_request)

        try:
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            html = page.content()
        except Exception:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(500)
                html = page.content()
            except Exception as e:
                browser.close()
                return {"summary_text": f"RENDER_ERROR: {e}"}

        # Discover hash routes by clicking nav elements and observing URL changes
        try:
            initial_url = page.url
            clickables = page.locator(
                'a[href*="#/"], [routerlink], [ng-click], button[aria-label], '
                'mat-icon, .mat-icon, [role="button"], nav a, .nav-link'
            )
            n_click = min(clickables.count(), 10)
            for i in range(n_click):
                try:
                    el = clickables.nth(i)
                    href = el.get_attribute("href") or ""
                    if "#/" in href:
                        route = href.split("#/", 1)[1].split("?")[0].strip("/")
                        if route and route not in hash_routes_seen:
                            hash_routes_seen.append(route)
                    # Click and check if URL hash changed
                    el.click(timeout=1500)
                    page.wait_for_timeout(300)
                    new_url = page.url
                    if "#/" in new_url:
                        route = new_url.split("#/", 1)[1].split("?")[0].strip("/")
                        if route and route not in hash_routes_seen:
                            hash_routes_seen.append(route)
                except Exception:
                    continue
            # Return to initial page
            try:
                page.goto(initial_url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass
        except Exception:
            pass

        # Fetch same-origin JS bundles for route extraction
        try:
            parsed = urlparse(url)
            for ru in req_urls:
                if ru.endswith(".js") and _same_origin(url, ru):
                    try:
                        resp = page.request.get(ru, timeout=3000)
                        if resp.ok:
                            js_texts.append(resp.text())
                    except Exception:
                        pass
                    if len(js_texts) >= 3:
                        break
        except Exception:
            pass

        browser.close()
    # Parse the rendered HTML
    soup = BeautifulSoup(html or "", "html.parser")
    summary_lines.extend(_summarize_forms(soup, url))
    summary_lines.extend(_summarize_inputs(soup))
    summary_lines.extend(_summarize_buttons(soup, url))
    summary_lines.extend(_summarize_link_buttons(soup, url))
    summary_lines.extend(_collect_keyword_ids(soup))

    # Also extract hash routes from href="#/..." in HTML
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if "#/" in href:
            route = href.split("#/", 1)[1].split("?")[0].strip("/")
            if route and route not in hash_routes_seen:
                hash_routes_seen.append(route)

    # Extract routes from JS bundles
    for jt in js_texts:
        for route in _extract_hash_routes_from_js(jt, url):
            if route not in hash_routes_seen:
                hash_routes_seen.append(route)

    # Extract endpoints seen during render
    eps: List[str] = []
    for u in req_urls:
        if any(tok in u for tok in ["/api/", "/rest/", "/auth", "/login", "/register", "/admin"]):
            eps.append(u)
    if eps:
        summary_lines.append("RENDER_ENDPOINTS: " + "; ".join(list(dict.fromkeys(eps))[:8]))

    # Report discovered hash routes
    if hash_routes_seen:
        # Annotate routes that likely accept query params
        annotated: List[str] = []
        parsed_base = urlparse(url)
        base = f"{parsed_base.scheme}://{parsed_base.netloc}"
        for route in hash_routes_seen[:12]:
            route_lower = route.lower()
            # Check if any segment hints at a query-accepting route
            if any(h in route_lower for h in _QUERY_ROUTE_HINTS):
                annotated.append(f"{base}/#/{route}?q={{payload}}")
            else:
                annotated.append(f"{base}/#/{route}")
        summary_lines.append("HASH_ROUTES: " + "; ".join(annotated))

    summary = "\n".join(summary_lines)
    return {"summary_text": summary[:2400]}
