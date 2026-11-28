"""Optional server-side adapter for an approved chat/completions service.

Only the server reads this configuration. Generation and review are two separate
requests; the review call is never merged into the generation call, so a single
model failure cannot silently approve itself.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PROMPTS = Path(__file__).with_name("prompts")


def _unique_fields(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("供应商的 JSON 包含重复字段。")
        obj[key] = value
    return obj


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatProvider:
    def __init__(self, endpoint, model, *, reviewer_model=None, token=None,
                 timeout=5.0, transport=None):
        url = urlsplit(endpoint)
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if (url.scheme != "https" and not (local and url.scheme == "http")) or not url.hostname:
            raise ValueError("远程服务使用 HTTPS，本机回环端口可使用 HTTP。")
        if url.username or url.password or url.fragment or url.query or not model.strip():
            raise ValueError("请单独配置凭据，并指定模型别名与完整接口。")
        if not 0 < timeout <= 60:
            raise ValueError("每次请求的超时须大于 0 且最多 60 秒。")
        self.endpoint, self.model, self.reviewer_model = endpoint, model, reviewer_model or model
        self.token, self.timeout = token, timeout
        self.transport = transport or build_opener(NoRedirect()).open
        self.generate_prompt = (PROMPTS / "generate.txt").read_text(encoding="utf-8")
        self.review_prompt = (PROMPTS / "review.txt").read_text(encoding="utf-8")

    @classmethod
    def from_environment(cls):
        return cls(os.environ["CONVERSATION_ENDPOINT"], os.environ["CONVERSATION_MODEL"],
                   reviewer_model=os.getenv("CONVERSATION_REVIEW_MODEL"),
                   token=os.getenv("CONVERSATION_TOKEN"),
                   timeout=float(os.getenv("CONVERSATION_TIMEOUT", "5")))

    def _chat(self, model, prompt, data, max_tokens):
        payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                   "stream": False, "messages": [
                       {"role": "system", "content": prompt},
                       {"role": "user", "content": json.dumps(data, ensure_ascii=False)}]}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = Request(self.endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST")
        with self.transport(req, timeout=self.timeout) as response:
            body = response.read(262145)
        if len(body) > 262144:
            raise ValueError("供应商响应过大。")
        envelope = json.loads(body, object_pairs_hook=_unique_fields)
        text = envelope["choices"][0]["message"]["content"]
        result = json.loads(text, object_pairs_hook=_unique_fields)
        if not isinstance(result, dict):
            raise ValueError("供应商需要返回 JSON 对象。")
        return result

    def generate(self, context):
        return self._chat(self.model, self.generate_prompt, context, 320)

    def review(self, context, candidate):
        decision = self._chat(self.reviewer_model, self.review_prompt,
                              {"context": context, "candidate": candidate}, 40)
        return set(decision) == {"allow"} and decision["allow"] is True
