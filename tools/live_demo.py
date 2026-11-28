"""Local Flask/WebSocket service for exercising the avatar with Mandarin PCM."""

import argparse
import base64
from dataclasses import dataclass, field
import json
from pathlib import Path
from threading import RLock
from uuid import uuid4
import wave
import webbrowser

from flask import Flask, abort, jsonify, make_response, request, send_from_directory, session
from flask_sock import Sock
from simple_websocket import ConnectionClosed
from werkzeug.serving import make_server

from replay_contract import validate_message


ROOT = Path(__file__).resolve().parents[1]
VOICE = ROOT / "assets" / "voice" / "live-demo"
CATALOG = json.loads((ROOT / "contracts" / "events.v1.json").read_text(encoding="utf-8"))


@dataclass
class Page:
    sid: str = field(default_factory=lambda: uuid4().hex)
    welcomed: bool = False
    number: int = 0
    inputs: dict[str, str] = field(default_factory=dict)
    tail: tuple[str, list] | None = None
    connection: object = None
    lock: RLock = field(default_factory=RLock)


def read_voice():
    lines = json.loads((VOICE / "lines.json").read_text(encoding="utf-8"))
    samples = {}
    for kind in lines:
        with wave.open(str(VOICE / f"{kind}.wav"), "rb") as voice:
            if (voice.getnchannels(), voice.getsampwidth(), voice.getframerate()) != (1, 2, 24000):
                raise ValueError(f"Invalid demo clip format: {kind}")
            samples[kind] = voice.readframes(voice.getnframes())
    return lines, samples


def make_app():
    phrases, clips = read_voice()
    app = Flask(__name__, static_folder=None)
    app.secret_key = uuid4().hex
    app.config["SOCK_SERVER_OPTIONS"] = {"max_message_size": 262144, "ping_interval": 25}
    sock = Sock(app)
    pages = {}
    pages_lock = RLock()

    @app.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        html = (ROOT / "web" / "avatar-preview" / "index.html").read_text(encoding="utf-8")
        return make_response(html.replace('data-mode="preview"', 'data-mode="live"'))

    @app.get("/web/avatar-preview/<path:name>")
    def frontend(name):
        mimetype = "text/javascript" if name.endswith(".mjs") else None
        return send_from_directory(ROOT / "web" / "avatar-preview", name, mimetype=mimetype)

    @app.get("/assets/<path:name>")
    def asset(name):
        return send_from_directory(ROOT / "assets", name)

    @app.get("/contracts/<path:name>")
    def contract(name):
        if name not in {"events.v1.json", "examples.v1.json"}:
            abort(404)
        return send_from_directory(ROOT / "contracts", name)

    @app.get("/health")
    def health():
        return {"status": "ok", "v": 1}

    @app.get("/api/session/bootstrap")
    def bootstrap():
        page_key = request.headers.get("X-Page-ID", "")
        if not 0 < len(page_key) <= 128:
            abort(400)
        visitor = session.setdefault("demo_visitor", uuid4().hex)
        with pages_lock:
            page = pages.setdefault(visitor, {}).setdefault(page_key, Page())
        return jsonify(
            v=1, session_id=page.sid, ws_path="/ws/session", scene_id="default",
            voice_style="neutral", features={"asr": False, "tts": True, "vision": False, "memory": False},
        )

    @app.before_request
    def check_socket_origin():
        if request.path != "/ws/session":
            return
        if request.headers.get("Origin") != f"http://{request.host}":
            abort(403)
        if not session.get("demo_visitor"):
            abort(401)

    @sock.route("/ws/session")
    def websocket(ws):
        page = None
        audio_output = False
        visitor = session["demo_visitor"]

        def emit(kind, sid=None, **payload):
            ws.send(json.dumps({"v": 1, "type": kind, "session_id": sid or page.sid, **payload}))

        def reply(kind, input_id=None):
            turn = f"R{page.number}"
            page.number += 1
            page.tail = None
            frame = {} if input_id is None else {"input_id": input_id}
            style = "practice" if kind == "practice" else "neutral"
            emit("turn.state", turn_id=turn, state="preparing",
                 reason="greeting" if input_id is None else "user_input", **frame)
            if kind == "practice":
                emit("scene.set", scene_id="practice", transition_ms=650, reason="practice_requested")
            emit("reply.text", turn_id=turn, sentence_id="Q0", text=phrases[kind], voice_style=style)
            if not audio_output:
                return
            pcm = clips[kind]
            chunk_size = 5760
            packets = [
                {
                    "turn_id": turn, "sentence_id": "Q0", "seq": number, "sample_rate": 24000,
                    "codec": "pcm_s16le", "data": base64.b64encode(pcm[start:start + chunk_size]).decode("ascii"),
                    "is_last": start + chunk_size >= len(pcm),
                }
                for number, start in enumerate(range(0, len(pcm), chunk_size))
            ]
            if kind == "interrupt":
                early = min(len(packets) - 1, 16)
                page.tail = (turn, packets[early:])
                packets = packets[:early]
            for packet in packets:
                emit("reply.audio", **packet)

        try:
            while ws.connected:
                data = ws.receive()
                try:
                    event = json.loads(data)
                    validate_message("client", event, CATALOG)
                except (ValueError, TypeError, KeyError):
                    if page is not None:
                        emit("session.error", code="INVALID_EVENT", message="无法读取联调事件。", retryable=False)
                    else:
                        break
                    continue
                sid = event["session_id"]
                kind = event["type"]
                if page is None:
                    with pages_lock:
                        page = next((item for item in pages.get(visitor, {}).values() if item.sid == sid), None)
                    if page is None:
                        emit("session.error", sid=sid, code="SESSION_EXPIRED",
                             message="会话已过期，正在重新连接。", retryable=True)
                        break
                    if kind != "session.ready":
                        break
                    with page.lock:
                        previous = page.connection
                        page.connection = ws
                        page.tail = None
                    if previous is not None:
                        previous.close()
                if sid != page.sid:
                    break
                with page.lock:
                    if page.connection is not ws:
                        break
                    if kind == "session.ready":
                        audio_output = event["audio_output"]
                        if not page.welcomed:
                            page.welcomed = True
                            reply("greeting")
                    elif kind == "input.text":
                        input_id = event["input_id"]
                        text = event["text"].strip()
                        if input_id in page.inputs:
                            if text == page.inputs[input_id]:
                                emit("transcript", input_id=input_id, text=text, is_final=True)
                            else:
                                emit("session.error", code="CONFLICT", message="输入已提交。", retryable=False)
                            continue
                        page.inputs[input_id] = text
                        emit("transcript", input_id=input_id, text=text, is_final=True)
                        if "打断" in text:
                            reply("interrupt", input_id)
                        elif "练习" in text:
                            reply("practice", input_id)
                        else:
                            reply("reply", input_id)
                    elif kind == "turn.cancel":
                        turn = event["turn_id"]
                        emit("turn.cancel", turn_id=turn, reason=event["reason"])
                        if page.tail and page.tail[0] == turn:
                            for packet in page.tail[1]:
                                emit("reply.audio", **packet)
                            page.tail = None
        except ConnectionClosed:
            pass
        finally:
            if page is not None:
                with page.lock:
                    if page.connection is ws:
                        page.connection = None
                        page.tail = None

    return app


def run(port=5052, open_page=False):
    server = make_server("127.0.0.1", port, make_app(), threaded=True)
    with server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Live avatar demo: {url}  (Ctrl+C to stop)", flush=True)
        if open_page:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5052)
    parser.add_argument("--open", action="store_true")
    options = parser.parse_args()
    run(options.port, options.open)
