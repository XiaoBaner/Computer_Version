"""Adapter checks with a fake transport: no app, no network, no credits spent."""
from io import BytesIO
import json

import pytest

from conversation.cyberbench import CyberBench, CyberBenchError, runtime_secret

KEY = "test-local-key"


class Response:
    """What urllib's opener returns: a context manager with .status and .read()."""

    def __init__(self, payload, status=200):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self._buffer = BytesIO(raw)
        self.status = status

    def read(self, size=-1):
        return self._buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def recorder(answer, status=200):
    seen = []

    def transport(req, *, timeout):
        seen.append({"url": req.full_url, "headers": dict(req.header_items()),
                     "body": json.loads(req.data) if req.data else None, "timeout": timeout})
        return Response(answer, status)

    return seen, transport


def chat_reply(content):
    return {"choices": [{"message": {"content": content}}]}


def relay(**extra):
    return CyberBench("http://127.0.0.1:8765", mode="relay", local_key=KEY,
                      model="qwen3-4b", **extra)


def test_relay_uses_the_account_ai_route_and_never_sends_an_origin():
    seen, transport = recorder(chat_reply('{"ok":true}'))
    client = relay(transport=transport)
    assert client.complete("系统", "用户", max_tokens=7) == '{"ok":true}'
    call = seen[0]
    assert call["url"] == "http://127.0.0.1:8765/api/account/ai/go/v1/chat/completions"
    assert call["body"] == {"model": "qwen3-4b", "temperature": 0, "max_tokens": 7,
                            "stream": False,
                            "messages": [{"role": "system", "content": "系统"},
                                         {"role": "user", "content": "用户"}]}
    headers = {name.lower(): value for name, value in call["headers"].items()}
    assert headers["authorization"] == "Bearer " + KEY
    assert "origin" not in headers and "sec-fetch-site" not in headers
    assert headers["content-type"] == "application/json; charset=utf-8"


def test_relay_needs_the_local_key():
    with pytest.raises(ValueError):
        CyberBench("http://127.0.0.1:8765", mode="relay")


def test_generate_parses_fenced_json_and_returns_only_the_object():
    seen, transport = recorder(chat_reply('说明文字\n```json\n{"intent":"listen",'
                                          '"sentences":["我在听。"],'
                                          '"voice_style":"gentle","memory_draft":null}\n```'))
    reply = relay(transport=transport).generate({"text": "今天有点累"})
    assert reply["sentences"] == ["我在听。"]
    assert json.loads(seen[0]["body"]["messages"][1]["content"]) == {"text": "今天有点累"}
    assert seen[0]["body"]["messages"][1]["content"].startswith('{"text"')


@pytest.mark.parametrize("content", ['{"allow": true}', '```json\n{"allow": true}\n```'])
def test_review_accepts_only_an_explicit_true(content):
    _, transport = recorder(chat_reply(content))
    assert relay(transport=transport).review({"text": "x"}, {"sentences": ["a"]}) is True


@pytest.mark.parametrize("content", ['{"allow": false}', '{"allow": "true"}',
                                     '{"allow": true, "note": 1}', '可以放行'])
def test_review_fails_closed_on_anything_else(content):
    _, transport = recorder(chat_reply(content))
    client = relay(transport=transport)
    if content == "可以放行":
        with pytest.raises(CyberBenchError):
            client.review({}, {})
    else:
        assert client.review({}, {}) is False


def test_agent_mode_has_no_key_and_states_the_json_contract():
    seen, transport = recorder({"reply": '{"intent":"listen","sentences":["我在听。"],'
                                         '"voice_style":"gentle","memory_draft":null}',
                                "engine": "opencode"})
    client = CyberBench("http://127.0.0.1:8765", transport=transport)
    assert client.mode == "agent"
    reply = client.generate({"text": "今天有点累"})
    assert reply["intent"] == "listen"
    call = seen[0]
    assert call["url"] == "http://127.0.0.1:8765/api/agent-chat"
    headers = {name.lower(): value for name, value in call["headers"].items()}
    assert "authorization" not in headers
    assert call["body"]["preferredLanguage"] == "zh"
    message = call["body"]["message"]
    assert "只输出一个 JSON 对象" in message and "今天有点累" in message


def test_agent_mode_prose_without_json_raises_so_the_service_falls_back():
    _, transport = recorder({"reply": "好的，我明白了。"})
    with pytest.raises(CyberBenchError):
        CyberBench("http://127.0.0.1:8765", transport=transport).generate({"text": "x"})


def test_error_envelope_is_surfaced_with_its_status():
    _, transport = recorder({"error": {"message": "Sign in to CyberBench first"}}, status=401)
    with pytest.raises(CyberBenchError) as exc:
        relay(transport=transport).generate({"text": "x"})
    assert exc.value.status == 401 and "Sign in" in str(exc.value)


def test_missing_app_is_reported_not_raised():
    def offline(req, *, timeout):
        raise OSError("connection refused")

    probe = CyberBench("http://127.0.0.1:8765", transport=offline).probe()
    assert probe["running"] is False and "连不上" in probe["error"]


def test_probe_reads_ping_and_sign_in_state():
    def transport(req, *, timeout):
        if req.full_url.endswith("/api/ping"):
            return Response({"ok": True, "pid": 31256, "protocolVersion": 2})
        return Response({"authenticated": True, "account": {"plan": "free"}})

    probe = CyberBench("http://127.0.0.1:8765", transport=transport).probe()
    assert probe == {"base_url": "http://127.0.0.1:8765", "mode": "agent", "running": True,
                     "signed_in": True, "pid": 31256, "protocol_version": 2,
                     "error": None, "plan": "free"}


def test_runtime_secret_reads_the_injected_opencode_config():
    config = {"provider": {"wanda-go": {
        "options": {"baseURL": "http://127.0.0.1:8765/api/account/ai/go/v1",
                    "apiKey": "runtime-key"},
        "models": {"m1": {}, "m2": {}}}}}
    key, base, models = runtime_secret({"OPENCODE_CONFIG_CONTENT": json.dumps(config)})
    assert key == "runtime-key" and base.endswith("/api/account/ai/go/v1")
    assert models == ["m1", "m2"]
    assert runtime_secret({}) is None


def test_from_environment_prefers_the_relay_when_a_key_exists():
    config = {"provider": {"wanda-go": {"options": {
        "baseURL": "http://127.0.0.1:9000/api/account/ai/go/v1", "apiKey": "runtime-key"}}}}
    client = CyberBench.from_environment({"OPENCODE_CONFIG_CONTENT": json.dumps(config)})
    assert client.mode == "relay" and client.local_key == "runtime-key"
    assert client.relay_base_url == "http://127.0.0.1:9000/api/account/ai/go/v1"
    assert CyberBench.from_environment({"CYBERBENCH_MODE": "agent"}).mode == "agent"


@pytest.mark.parametrize("base", ["https://127.0.0.1:8765", "http://example.test:8765",
                                  "http://127.0.0.1:8765/api"])
def test_only_the_apps_loopback_origin_is_accepted(base):
    with pytest.raises(ValueError):
        CyberBench(base)