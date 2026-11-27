"""Adapter checks with a fake transport: no network, no server, no credentials."""
from io import BytesIO
import json
import pytest
from conversation.provider import ChatProvider


def envelope(payload):
    return BytesIO(json.dumps({"choices": [{"message": {
        "content": json.dumps(payload)}}]}).encode())


def test_provider_uses_distinct_requests_and_review_model():
    requests = []
    reply = {"intent": "listen", "sentences": ["我在听。"], "voice_style": "gentle", "memory_draft": None}

    def fake(req, *, timeout):
        body = json.loads(req.data)
        requests.append(body)
        assert timeout > 0
        return envelope(reply if len(requests) == 1 else {"allow": True})

    p = ChatProvider("http://localhost:8000/v1/chat/completions", "generator",
                     reviewer_model="reviewer", transport=fake)
    ctx = {"text": "想练开场", "history": [], "memories": []}
    assert p.generate(ctx) == reply
    assert p.review(ctx, reply)
    assert [r["model"] for r in requests] == ["generator", "reviewer"]
    assert json.loads(requests[1]["messages"][1]["content"])["candidate"] == reply


@pytest.mark.parametrize("result", [{"allow": "true"}, {}, {"allow": False}, {"allow": True, "other": 1}])
def test_review_requires_an_unambiguous_boolean(result):
    p = ChatProvider("https://example.test/v1/chat/completions", "fixture",
                     transport=lambda *a, **kw: envelope(result))
    assert not p.review({}, {})


def test_plain_http_external_endpoint_is_rejected():
    with pytest.raises(ValueError):
        ChatProvider("http://example.test/v1/chat/completions", "fixture")