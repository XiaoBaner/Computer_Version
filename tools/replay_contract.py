"""Replay the shared WebSocket v1 examples without a server, camera or model."""

import argparse
import base64
import binascii
import json
import math
import sys
from pathlib import Path

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"


def check_fields(values, required, optional, types):
    if not isinstance(values, dict):
        raise ValueError("expected an object")
    missing = required.keys() - values.keys()
    if missing:
        raise ValueError("missing: " + ", ".join(sorted(missing)))
    for name, type_name in (required | optional).items():
        if name in values:
            try:
                check_value(values[name], types[type_name], types)
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from exc


def check_value(value, spec, types):
    if "const" in spec or "enum" in spec:
        choices = spec.get("enum", [spec.get("const")])
        if not any(type(value) is type(option) and value == option for option in choices):
            raise ValueError(f"expected one of {choices!r}")
        return
    kind = spec["type"]
    if kind == "array":
        if not isinstance(value, list):
            raise ValueError("expected an array")
        for item in value:
            check_fields(item, spec["items"], {}, types)
    elif kind == "string":
        if not isinstance(value, str) or len(value) < spec.get("minLength", 0):
            raise ValueError("invalid string")
        if spec.get("contentEncoding") == "base64":
            try:
                raw = base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("invalid base64") from exc
            if not raw or len(raw) % 2:
                raise ValueError("expected complete 16-bit PCM samples")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise ValueError("expected a boolean")
    elif kind in ("integer", "number"):
        allowed = (int,) if kind == "integer" else (int, float)
        if type(value) not in allowed or not math.isfinite(value):
            raise ValueError(f"expected a finite {kind}")
        if value < spec.get("minimum", -math.inf) or value > spec.get("maximum", math.inf):
            raise ValueError("out of range")
    else:
        raise ValueError(f"unsupported field type {kind}")


def validate_message(source, message, catalog):
    check_fields(message, catalog["common"], {}, catalog["types"])
    event = catalog["events"].get(message["type"])
    if not event or source not in event["from"]:
        raise ValueError(f"unsupported event or sender: {source} / {message['type']}")
    check_fields(message, event["required"], event.get("optional", {}), catalog["types"])
    if message["type"] == "input.text" and not message["text"].strip():
        raise ValueError("input.text must contain non-whitespace text")
    if message["type"] == "vision.event":
        if message["event"] == "unavailable" and (
            message["quality"] != "unavailable" or message["confidence"] != 0
        ):
            raise ValueError("unavailable vision must report unavailable quality and zero confidence")
        if message["event"] == "model_result" and not (message.get("model_version") and message.get("labels")):
            raise ValueError("model_result requires model_version and labels")


def expected_handling(message, cancelled):
    kind = message["type"]
    turn = (message["session_id"], message.get("turn_id"))
    if kind == "turn.cancel":
        if turn in cancelled:
            return "already_cancelled"
        cancelled.add(turn)
    if turn in cancelled and kind in {"reply.text", "reply.audio", "scene.set"}:
        return "drop_cancelled"
    if turn in cancelled and kind == "turn.state" and message["state"] != "interrupted":
        return "drop_cancelled"
    if turn in cancelled and kind == "reply.playback" and message["state"] != "cancelled":
        return "drop_cancelled"
    return "handle"


def replay(case, catalog, emit):
    cancelled = set()
    frames = case["frames"]
    if not frames:
        raise ValueError(f"{case['id']}: empty example")
    for number, frame in enumerate(frames, 1):
        try:
            message = frame["message"]
            validate_message(frame["from"], message, catalog)
            result = expected_handling(message, cancelled)
            if result != frame["expect"]:
                raise ValueError(f"expected {frame['expect']}, got {result}")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{case['id']} frame {number}: {exc}") from exc
        if emit == frame["from"]:
            print(json.dumps(message, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="replay a single case by ID")
    parser.add_argument("--emit", choices=("client", "server"), help="output raw messages as JSON Lines")
    parser.add_argument("--file", type=Path, default=CONTRACTS / "examples.v1.json", help="test a compatible fixture")
    args = parser.parse_args()
    try:
        catalog = json.loads((CONTRACTS / "events.v1.json").read_text(encoding="utf-8"))
        cases = json.loads(args.file.read_text(encoding="utf-8"))
        selected = [case for case in cases if not args.case or case["id"] == args.case]
        if not selected:
            raise ValueError("no matching cases")
        for case in selected:
            replay(case, catalog, args.emit)
            if not args.emit:
                print(f"OK  {case['id']}  ({len(case['frames'])})  {case['title']}")
    except (OSError, KeyError, ValueError, TypeError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
