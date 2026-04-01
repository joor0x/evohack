import json
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Any
from urllib.parse import urljoin, urlparse, urlunparse
import io

import requests


def _base_root(u: str) -> str:
    try:
        p = urlparse(u)
        return urlunparse((p.scheme, p.netloc, "", "", "", "")) or u
    except Exception:
        return u


@dataclass
class JuiceShopLoginProfile:
    base_url: str = "http://localhost:3000"

    @property
    def url(self) -> str:
        b = _base_root(self.base_url)
        return f"{b.rstrip('/')}/rest/user/login"

    @property
    def headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    @property
    def method(self) -> str:
        return "POST"

    @property
    def body_template(self) -> str:
        # Insertamos {payload} en el email, password fijo
        return json.dumps({"email": "{payload}", "password": "12345678"})


@dataclass
class JuiceShopSearchProfile:
    base_url: str = "http://localhost:3000"

    @property
    def url(self) -> str:
        b = _base_root(self.base_url)
        return f"{b.rstrip('/')}/rest/products/search"

    @property
    def method(self) -> str:
        return "GET"

    @property
    def headers(self) -> Dict[str, str]:
        return {"Accept": "application/json"}

    @property
    def param_name(self) -> str:
        return "q"


@dataclass
class JuiceShopFeedbackProfile:
    base_url: str = "http://localhost:3000"

    @property
    def url(self) -> str:
        b = _base_root(self.base_url)
        return f"{b.rstrip('/')}/api/Feedbacks/"

    @property
    def method(self) -> str:
        return "POST"

    @property
    def headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    @property
    def body_template(self) -> str:
        return json.dumps({"comment": "{payload}", "rating": 1})


@dataclass
class JuiceShopUploadProfile:
    base_url: str = "http://localhost:3000"

    @property
    def url(self) -> str:
        b = _base_root(self.base_url)
        return f"{b.rstrip('/')}/file-upload"

    @property
    def method(self) -> str:
        return "POST"

    @property
    def headers(self) -> Dict[str, str]:
        # requests gestiona multipart, no forzamos Content-Type
        return {}


class TargetClient:
    def __init__(
        self,
        url: str,
        method: str = "POST",
        headers: Optional[Dict[str, str]] = None,
        body_template: Optional[str] = None,
        param_name: Optional[str] = None,
        path_template: Optional[str] = None,
        timeout: float = 8.0,
    ) -> None:
        self.url = url
        self.method = method.upper()
        self.headers = headers.copy() if headers else {}
        # Mitigar resets de algunas apps/proxies con keep-alive
        self.headers.setdefault("Connection", "close")
        self.headers.setdefault("User-Agent", "evohack-llm/0.1")
        self.body_template = body_template
        self.param_name = param_name
        self.path_template = path_template
        self.timeout = timeout
        self.header_name: Optional[str] = None
        self.file_field: Optional[str] = None
        self.file_name_template: str = "payload.txt"

    @classmethod
    def from_profile(cls, profile: JuiceShopLoginProfile, timeout: float = 8.0) -> "TargetClient":
        return cls(
            url=profile.url,
            method=profile.method,
            headers=getattr(profile, "headers", None),
            body_template=getattr(profile, "body_template", None),
            param_name=getattr(profile, "param_name", None),
            timeout=timeout,
        )

    def with_header_injection(self, header_name: Optional[str]) -> "TargetClient":
        self.header_name = header_name
        return self

    def with_file_upload(self, field_name: Optional[str], file_name_template: str = "payload.txt") -> "TargetClient":
        self.file_field = field_name
        self.file_name_template = file_name_template
        return self

    def send(self, payload: str) -> Tuple[int, str, Dict[str, Any]]:
        # Construye petición con el payload
        body = None
        data = None
        params = None
        extra_meta: Dict[str, Any] = {}

        # Construye URL efectiva (path injection opcional)
        effective_url = self.url
        if self.path_template:
            if "{payload}" in self.path_template:
                effective_url = self.path_template.replace("{payload}", payload)
            else:
                effective_url = urljoin(self.url.rstrip("/") + "/", payload.lstrip("/"))

        if self.body_template:
            body_text = self.body_template.replace("{payload}", payload)
            try:
                body = json.loads(body_text)
            except json.JSONDecodeError:
                # si es JSON inválido, envía como texto
                data = body_text
        elif self.param_name:
            if self.method == "GET":
                params = {self.param_name: payload}
            else:
                data = {self.param_name: payload}

        # Inyección en cabecera opcional
        headers = dict(self.headers)
        if self.header_name:
            headers[self.header_name] = payload

        try:
            files = None
            if self.file_field:
                # admite directivas: [[FILENAME=foo.ext;SIZE=12345]]\n<content>
                fname = self.file_name_template
                fcontent = payload
                fsize: Optional[int] = None
                if payload.startswith("[[") and "]]" in payload.splitlines()[0]:
                    first_line = payload.splitlines()[0]
                    inner = first_line.strip()[2:-2]
                    for part in inner.split(";"):
                        part = part.strip()
                        if not part:
                            continue
                        if part.upper().startswith("FILENAME="):
                            fname = part.split("=",1)[1].strip() or fname
                        elif part.upper().startswith("SIZE="):
                            try:
                                fsize = int(part.split("=",1)[1].strip())
                            except Exception:
                                fsize = None
                    # content is remaining after first line
                    rest = "\n".join(payload.splitlines()[1:])
                    fcontent = rest if rest else ""
                data_bytes = (fcontent or "").encode()
                if fsize and fsize > len(data_bytes):
                    pad = fsize - len(data_bytes)
                    data_bytes = data_bytes + (b"A" * pad)
                files = {self.file_field: (fname, io.BytesIO(data_bytes))}
                # evitar forzar content-type a json en multipart
                body_to_send = None
                data_to_send = None
            else:
                body_to_send = body if body is not None else None
                data_to_send = data
            resp = requests.request(
                self.method,
                effective_url,
                headers=headers,
                json=body_to_send,
                data=data_to_send,
                files=files,
                params=params,
                timeout=self.timeout,
            )
            text = resp.text or ""
            try:
                extra_meta["json"] = resp.json()
            except Exception:
                pass
            try:
                extra_meta["headers"] = dict(resp.headers)
            except Exception:
                pass
            try:
                extra_meta["elapsed_ms"] = int(getattr(resp, "elapsed").total_seconds() * 1000)
            except Exception:
                pass
            try:
                # capture cookies
                extra_meta["cookies"] = {c.name: c.value for c in resp.cookies}
            except Exception:
                pass
            return resp.status_code, text, extra_meta
        except requests.RequestException as e:
            return 0, str(e), extra_meta


class BrowserTarget:
    """Playwright-based target for DOM XSS evaluation via hash-fragment URLs."""

    def __init__(
        self,
        url_template: str,
        timeout: float = 8.0,
    ) -> None:
        # url_template must contain {payload}, e.g. http://host/#/search?q={payload}
        self.url_template = url_template
        self.timeout = timeout
        self.method = "GET"
        # Expose attributes for compatibility with TargetClient consumers
        self.url = url_template.split("{payload}")[0].rstrip("?=&")
        self.param_name = None
        self.body_template = None
        self.header_name = None
        self.headers: Dict[str, str] = {}
        self._pw = None
        self._browser = None

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright  # type: ignore
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)

    def close(self):
        if self._browser:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def send(self, payload: str) -> Tuple[int, str, Dict[str, Any]]:
        import time as _time
        meta: Dict[str, Any] = {}
        t0 = _time.monotonic()
        try:
            self._ensure_browser()
        except Exception as e:
            return 0, f"BROWSER_INIT_ERROR: {e}", meta

        effective_url = self.url_template.replace("{payload}", payload)
        context = self._browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        dialog_fired = False
        dialog_message = ""

        def on_dialog(dialog):
            nonlocal dialog_fired, dialog_message
            dialog_fired = True
            dialog_message = dialog.message
            try:
                dialog.dismiss()
            except Exception:
                pass

        page.on("dialog", on_dialog)

        try:
            page.goto(effective_url, wait_until="domcontentloaded", timeout=int(self.timeout * 1000))
            # Wait a bit for JS to execute and potentially trigger XSS
            page.wait_for_timeout(800)
            html = page.content() or ""
            page_url = page.url
        except Exception as e:
            context.close()
            elapsed = int((_time.monotonic() - t0) * 1000)
            meta["elapsed_ms"] = elapsed
            return 0, f"BROWSER_NAV_ERROR: {e}", meta

        context.close()
        elapsed = int((_time.monotonic() - t0) * 1000)
        meta["elapsed_ms"] = elapsed
        meta["dialog_fired"] = dialog_fired
        meta["dialog_message"] = dialog_message
        meta["page_url"] = page_url

        # Check DOM reflection — search for payload in rendered HTML
        payload_lower = payload.lower()
        html_lower = html.lower()
        meta["dom_reflected"] = payload_lower in html_lower if payload_lower else False

        # Synthetic status: 200 for success, add markers for fitness heuristics
        status = 200
        # Build text with signals the fitness evaluator can pick up
        signals = []
        if dialog_fired:
            signals.append(f"XSS_DIALOG_FIRED: {dialog_message}")
            signals.append("alert(1)")  # trigger XSS heuristic in fitness
        if meta["dom_reflected"]:
            signals.append("XSS_DOM_REFLECTED: payload found in rendered DOM")
        # Include a snippet of the page for LLM scoring
        snippet = html[:1200]
        text = "\n".join(signals + [snippet]) if signals else snippet

        return status, text, meta
