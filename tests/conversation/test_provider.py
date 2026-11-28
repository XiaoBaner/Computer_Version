"""Adapter checks with a fake transport: no network, no server, no credentials."""
from io import BytesIO
import json
import pytest
from conversation import service
from conversation.fixture_memory import DictMemoryStore
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


@pytest.mark.parametrize("decision", ['{"allow":false,"allow":true}', '{"allow":true,"allow":false}'])
def test_conflicting_review_fields_are_not_delivered(decision, monkeypatch):
    candidate = {"intent": "listen", "sentences": ["候选标记。"],
                 "voice_style": "neutral", "memory_draft": None}
    raw = json.dumps({"choices": [{"message": {"content": decision}}]}).encode()
    provider = ChatProvider("http://localhost:8000/v1/chat/completions", "fixture",
                            transport=lambda *a, **kw: BytesIO(raw))
    monkeypatch.setattr(service, "_runtime", None)
    store = DictMemoryStore()
    try:
        service.configure(store=store, generator=lambda _ctx: candidate, reviewer=provider.review)
        ctx = dict(user_id="demo", session_id="S1", turn_id="R1", input_id="I1",
                   background={}, history=[])
        reply = service.respond(ctx, "我想聊聊今天的安排。", lambda: False)
        assert reply["sentences"] != candidate["sentences"]
    finally:
        store.close()


def test_plain_http_external_endpoint_is_rejected():
    with pytest.raises(ValueError):
        ChatProvider("http://example.test/v1/chat/completions", "fixture")
