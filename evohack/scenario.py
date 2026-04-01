from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
import time
import random
from typing import Any, Dict, List, Optional, Tuple

import requests


def _render_template(s: Optional[str], ctx: Dict[str, Any]) -> Optional[str]:
    if s is None:
        return None
    out = s
    for k, v in ctx.items():
        try:
            out = out.replace("{" + str(k) + "}", str(v))
        except Exception:
            continue
    return out


@dataclass
class CaptureRule:
    name: str
    type: str  # 'json' or 'regex'
    path: Optional[str] = None  # for json: dot.path
    pattern: Optional[str] = None  # for regex: pattern with group 1


@dataclass
class ScenarioStep:
    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body_template: Optional[str] = None
    param_name: Optional[str] = None
    path_template: Optional[str] = None
    captures: List[CaptureRule] = field(default_factory=list)


@dataclass
class Scenario:
    steps: List[ScenarioStep]

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> 'Scenario':
        steps = []
        for s in d.get('steps', []):
            caps = []
            for c in s.get('captures', []) or []:
                caps.append(CaptureRule(name=c.get('name'), type=c.get('type'), path=c.get('path'), pattern=c.get('pattern')))
            steps.append(ScenarioStep(
                method=s.get('method', 'GET'),
                url=s['url'],
                headers=s.get('headers') or {},
                body_template=s.get('body_template'),
                param_name=s.get('param_name'),
                path_template=s.get('path_template'),
                captures=caps,
            ))
        return Scenario(steps=steps)

    @staticmethod
    def from_file(path: str) -> 'Scenario':
        with open(path, 'r', encoding='utf-8') as fh:
            d = json.load(fh)
        return Scenario.from_dict(d)


class ScenarioRunner:
    def __init__(self, scenario: Scenario, timeout: float = 8.0, pre_context: Optional[Dict[str, Any]] = None):
        self.scenario = scenario
        self.timeout = timeout
        self.pre_context = dict(pre_context or {})

    def send(self, payload: str) -> Tuple[int, str, Dict[str, Any]]:
        base_ctx: Dict[str, Any] = {'ts': int(time.time()), 'rand': random.randint(1000, 999999)}
        ctx: Dict[str, Any] = {**self.pre_context, **base_ctx, 'payload': payload}
        # Optional directive to modify steps: [[SCENARIO:{json}]] on first line
        mods = None
        if isinstance(payload, str) and payload.startswith("[[SCENARIO:") and "]]" in payload:
            try:
                first_line = payload.splitlines()[0]
                jtext = first_line[len("[[SCENARIO:") : first_line.index("]]", len("[[SCENARIO:"))]
                mods = json.loads(jtext)
                # base payload is rest
                rest = "\n".join(payload.splitlines()[1:])
                ctx['payload'] = rest
            except Exception:
                mods = None
        sess = requests.Session()
        sess.headers.update({'User-Agent': 'evohack-scenario/0.1'})
        step_metas: List[Dict[str, Any]] = []
        last_status = 0
        last_text = ''
        for idx, step in enumerate(self.scenario.steps):
            # Derive per-step overrides from mods
            step_override = (mods or {}).get('steps', {}).get(str(idx)) if isinstance(mods, dict) else None
            url = _render_template(step.url, ctx) or ''
            method = step.method.upper()
            headers_src = dict(step.headers or {})
            if step_override and isinstance(step_override.get('headers'), dict):
                headers_src.update(step_override.get('headers'))
            headers = {k: _render_template(v, ctx) or '' for k, v in headers_src.items()}
            params = None
            data = None
            body = None
            effective_url = url
            # Allow override of url/method/param_name/path_template
            if step_override and step_override.get('url'):
                effective_url = _render_template(step_override.get('url'), ctx) or url
            pt_src = step.path_template
            if step_override and step_override.get('path_template'):
                pt_src = step_override.get('path_template')
            if pt_src:
                pt = _render_template(pt_src, ctx) or ''
                if '{payload}' in (pt_src or ''):
                    effective_url = pt
                else:
                    # append rendered
                    effective_url = (url.rstrip('/') + '/' + pt.lstrip('/'))
            param_name = step.param_name
            if step_override and step_override.get('param_name'):
                param_name = step_override.get('param_name')
            body_template = step.body_template
            if step_override and step_override.get('body_template'):
                body_template = step_override.get('body_template')
            method_src = (step_override.get('method') or method) if step_override else method
            method = (method_src or method).upper()

            if body_template:
                bt = _render_template(body_template, ctx) or ''
                try:
                    body = json.loads(bt)
                except Exception:
                    data = bt
            elif param_name:
                key = param_name
                if method == 'GET':
                    params = {key: payload}
                else:
                    data = {key: payload}

            repeat = 1
            if step_override and isinstance(step_override.get('repeat'), int):
                repeat = max(1, min(10, int(step_override.get('repeat'))))
            for _ in range(repeat):
                try:
                    resp = sess.request(method, effective_url, headers=headers, json=body, data=data, params=params, timeout=self.timeout)
                    text = resp.text or ''
                    last_status, last_text = resp.status_code, text
                    meta_step: Dict[str, Any] = {'status': resp.status_code, 'url': effective_url}
                    # captures
                    for cap in (step.captures or []):
                        if cap.type == 'json':
                            try:
                                j = resp.json()
                                val = j
                                for part in (cap.path or '').split('.'):
                                    if not part:
                                        continue
                                    if isinstance(val, dict) and part in val:
                                        val = val[part]
                                    else:
                                        val = None
                                        break
                                if val is not None:
                                    ctx[cap.name] = val
                                    meta_step.setdefault('captures', {})[cap.name] = val
                            except Exception:
                                pass
                        elif cap.type == 'regex' and cap.pattern:
                            m = re.search(cap.pattern, text)
                            if m and m.groups():
                                ctx[cap.name] = m.group(1)
                                meta_step.setdefault('captures', {})[cap.name] = m.group(1)
                    step_metas.append(meta_step)
                except requests.RequestException as e:
                    step_metas.append({'error': str(e), 'url': effective_url})
                    last_status = 0
                    last_text = str(e)
                    break

        return last_status, last_text, {'steps': step_metas}
