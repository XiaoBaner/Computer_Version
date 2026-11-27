"""Client for the CyberBench desktop app's loopback API (Python 3.11).

CyberBench (`F:\\CyberNeuro`) serves a local HTTP API on 127.0.0.1 while it runs.
Two model transports are available, and both feed the same contract here:

``relay``
    ``POST /api/account/ai/{pool}/v1/chat/completions`` — the OpenAI-compatible
    surface CyberBench's own OpenCode runtime calls (``shared_ai.py``). Strict JSON
    in, strict JSON out, temperature fixed at 0. It requires the per-process local
    key that CyberBench injects into its child runtime as
    ``OPENCODE_CONFIG_CONTENT`` (``Authorization: Bearer <apiKey>``). Requests that
    carry ``Origin`` or ``Sec-Fetch-Site`` are refused, so this client never sets
    them.

``agent``
    ``POST /api/agent-chat`` — the app's own agent turn. No key, usable whenever the
    desktop app is signed in, but it is an agent: slower, may use tools and may
    answer in prose. A candidate from this path is therefore still checked by our
    structure gate, hard-boundary gate and independent reviewer, and a bad parse
    falls back to the pre-approved short sentence.

Everything here is server-side only. No request is made on import; nothing is
written to the memory store.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROMPTS = Path(__file__).with_name("prompts")

#: Pools the relay forwards (``shared_ai.POOLS``; ``claude`` is disabled in the app).
RELAY_POOLS = ("local", "go", "paid")
DEFAULT_BASE_URL = "http://127.0.0.1:8765"
RELAY_MARKER = "/api/account/ai/"
MAX_RESPONSE = 1024 * 1024


class CyberBenchError(RuntimeError):
    """The app is not running, not signed in, or answered something unusable."""

    def __init__(self, message, *, status=None):
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _json_object(text):
    """First JSON object in a model reply, tolerating fences and prose around it."""
    if not isinstance(text, str):
        raise CyberBenchError("回复不是文本。")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for candidate in ([fenced.group(1)] if fenced else []) + re.findall(r"\{.*?\}", text, re.S):
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise CyberBenchError("回复中没有 JSON 对象。")


def runtime_secret(environ=None):
    """The relay capability CyberBench hands to its own child runtime, if any.

    Returns ``(local_key, relay_base_url, model_ids)`` or ``None``. Outside the
    app's runtime this is None by design; the agent transport is then used.
    """
    environ = os.environ if environ is None else environ
    raw = environ.get("OPENCODE_CONFIG_CONTENT")
    if not raw:
        return None
    try:
        config = json.loads(raw)
    except ValueError:
        return None
    for provider in (config.get("provider") or {}).values():
        if not isinstance(provider, dict):
            continue
        options = provider.get("options") or {}
        base = options.get("baseURL")
        key = options.get("apiKey")
        if isinstance(base, str) and isinstance(key, str) and key and RELAY_MARKER in base:
            return key, base.rstrip("/"), list((provider.get("models") or {}))
    return None


class CyberBench:
    """One adapter for both transports. Pass ``transport`` to test without network."""

    def __init__(self, base_url=DEFAULT_BASE_URL, *, mode="auto", local_key=None,
                 relay_base_url=None, pool="go", model=None, timeout=None,
                 transport=None):
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("CyberBench 只在本机回环地址提供接口。")
        if url.path and url.path != "/":
            raise ValueError("base_url 只写协议、主机和端口。")
        if mode not in ("auto", "relay", "agent"):
            raise ValueError("mode 只能是 auto、relay 或 agent。")
        if pool not in RELAY_POOLS:
            raise ValueError("pool 只能是 local、go 或 paid。")
        self.base_url = f"http://{url.hostname}:{url.port or 80}"
        self.pool = pool
        self.local_key = local_key
        self.relay_base_url = (relay_base_url or f"{self.base_url}{RELAY_MARKER}{pool}/v1").rstrip("/")
        self.model = model
        self.timeout = float(timeout or (20 if local_key else 120))
        self.transport = transport or build_opener(_NoRedirect()).open
        if mode == "auto":
            mode = "relay" if local_key else "agent"
        if mode == "relay" and not local_key:
            raise ValueError("relay 模式需要 CyberBench 的本地密钥。")
        self.mode = mode

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        secret = runtime_secret(environ)
        key = environ.get("CYBERBENCH_LOCAL_KEY") or (secret[0] if secret else None)
        return cls(environ.get("CYBERBENCH_BASE_URL", DEFAULT_BASE_URL),
                   mode=environ.get("CYBERBENCH_MODE", "auto"),
                   local_key=key,
                   relay_base_url=secret[1] if secret else None,
                   pool=environ.get("CYBERBENCH_POOL", "go"),
                   model=environ.get("CYBERBENCH_MODEL") or None,
                   timeout=environ.get("CYBERBENCH_TIMEOUT") or None)

    # -- transport ---------------------------------------------------------
    def _open(self, req, timeout):
        try:
            response = self.transport(req, timeout=timeout)
        except OSError as exc:
            raise CyberBenchError(f"连不上 CyberBench（{self.base_url}）：{exc}") from exc
        with response:
            body = response.read(MAX_RESPONSE + 1)
            status = getattr(response, "status", 200)
        if len(body) > MAX_RESPONSE:
            raise CyberBenchError("CyberBench 响应过大。", status=status)
        text = body.decode("utf-8", "replace")
        if status >= 400:
            raise CyberBenchError(_error_text(text) or f"HTTP {status}", status=status)
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise CyberBenchError("CyberBench 返回的不是 JSON。", status=status) from exc
        if not isinstance(value, dict):
            raise CyberBenchError("CyberBench 返回的不是对象。", status=status)
        return value

    def _post(self, path, payload, timeout=None):
        req = Request(self.base_url + path,
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8",
                               "Accept": "application/json"},
                      method="POST")
        return self._open(req, timeout or self.timeout)

    def _get(self, path, timeout=5):
        return self._open(Request(self.base_url + path, method="GET",
                                  headers={"Accept": "application/json"}), timeout)

    # -- discovery ---------------------------------------------------------
    def probe(self):
        """What this machine can do right now; never raises for a missing app."""
        result = {"base_url": self.base_url, "mode": self.mode, "running": False,
                  "signed_in": False, "pid": None, "protocol_version": None, "error": None}
        try:
            ping = self._get("/api/ping", timeout=3)
            result.update(running=bool(ping.get("ok")), pid=ping.get("pid"),
                          protocol_version=ping.get("protocolVersion"))
        except CyberBenchError as exc:
            result["error"] = str(exc)
            return result
        try:
            status = self._get("/api/account/status", timeout=3)
            result["signed_in"] = bool(status.get("authenticated"))
            result["plan"] = ((status.get("account") or {}).get("plan"))
        except CyberBenchError as exc:
            result["error"] = str(exc)
        return result

    # -- model calls -------------------------------------------------------
    def _relay(self, messages, max_tokens):
        payload = {"model": self.model or "default", "temperature": 0,
                   "max_tokens": max_tokens, "stream": False, "messages": messages}
        req = Request(self.relay_base_url + "/chat/completions",
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8",
                               "Accept": "application/json",
                               "Authorization": "Bearer " + self.local_key},
                      method="POST")
        answer = self._open(req, self.timeout)
        try:
            return answer["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise CyberBenchError("中转返回缺少 choices[0].message.content。") from exc

    def _agent(self, system, user, max_tokens):
        # The agent has one free-form message field, so the contract goes first.
        message = (system.strip() + "\n\n" + user.strip()
                   + "\n\n只输出一个 JSON 对象，不要解释、不要代码块、不要调用工具。")
        answer = self._post("/api/agent-chat", {"message": message,
                                                "preferredLanguage": "zh"}, self.timeout)
        reply = answer.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            raise CyberBenchError("agent-chat 没有返回文字。")
        return reply

    def complete(self, system, user, *, max_tokens=320):
        if self.mode == "relay":
            return self._relay([{"role": "system", "content": system},
                                {"role": "user", "content": user}], max_tokens)
        return self._agent(system, user, max_tokens)

    # -- the two callables service.configure() accepts ---------------------
    def generate(self, context):
        system = (PROMPTS / "generate.txt").read_text(encoding="utf-8")
        user = json.dumps(context, ensure_ascii=False)
        return _json_object(self.complete(system, user, max_tokens=320))

    def review(self, context, candidate):
        system = (PROMPTS / "review.txt").read_text(encoding="utf-8")
        user = json.dumps({"context": context, "candidate": candidate}, ensure_ascii=False)
        decision = _json_object(self.complete(system, user, max_tokens=40))
        return set(decision) == {"allow"} and decision["allow"] is True

    def close(self):
        return None


def _error_text(raw):
    """The app's three error shapes: {"error": "…"}, {"error": {"message"…}}, {"type": "error"…}."""
    try:
        value = json.loads(raw)
    except ValueError:
        return raw.strip()[:300]
    if isinstance(value, dict):
        error = value.get("error")
        if isinstance(error, dict):
            error = error.get("message")
        if isinstance(error, str) and error:
            return error
    return raw.strip()[:300]