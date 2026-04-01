from __future__ import annotations
from typing import List, Optional


def classify_categories(url: str, method: str, header_name: Optional[str], param_name: Optional[str], body_template: Optional[str]) -> List[str]:
    url_l = (url or "").lower()
    cats: List[str] = []
    if any(k in url_l for k in ["/login", "/user/login"]):
        cats += ["inj_sql", "inj_nosql", "jwt"]
    if any(k in url_l for k in ["/search", "/query", "/products/search"]):
        cats += ["xss", "inj_nosql"]
    if any(k in url_l for k in ["/feedback", "/comments", "/review"]):
        cats += ["xss", "inj_nosql"]
    if any(k in url_l for k in ["/file", "/upload"]):
        cats += ["upload", "lfi", "ssti"]
    if any(k in url_l for k in ["/chat", "/bot", "chatbot", "assistant"]):
        cats += ["chatbot"]
    if any(k in url_l for k in ["/redirect", "redirect?"]):
        cats += ["redir", "headers"]
    if header_name:
        cats += ["headers"]
        if header_name.lower() in ["authorization", "x-auth-token"]:
            cats += ["jwt"]
        if header_name.lower() in ["user-agent", "referer"]:
            cats += ["xss"]
    if method.upper() == "GET" and param_name:
        cats += ["xss", "redir"]
    # de-dup while preserving order
    seen = set()
    out: List[str] = []
    for c in cats:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out or ["inj_sql", "xss", "inj_nosql"]
