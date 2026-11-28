"""Serve the avatar preview or the live demo over loopback."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "web" / "avatar-preview"
PUBLIC_DIRS = (PREVIEW, ROOT / "assets" / "avatar", ROOT / "assets" / "scene")
FIXTURES = {ROOT / "contracts" / name for name in ("events.v1.json", "examples.v1.json")}


class PreviewHandler(SimpleHTTPRequestHandler):
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".mjs": "text/javascript",
    }

    def send_head(self):
        if urlsplit(self.path).path == "/":
            self.path = "/web/avatar-preview/index.html"
        requested = Path(self.translate_path(self.path)).resolve()
        allowed = requested in FIXTURES or any(requested.is_relative_to(path) for path in PUBLIC_DIRS)
        if not allowed or not requested.is_file():
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int)
    parser.add_argument("--live", action="store_true", help="run the local Flask/WebSocket demo")
    parser.add_argument("--open", action="store_true", help="open the default browser")
    args = parser.parse_args()
    if args.live:
        try:
            from live_demo import run
        except ModuleNotFoundError as exc:
            parser.exit(1, f"Install requirements-live.txt to start the live demo: {exc}\n")
        run(5052 if args.port is None else args.port, args.open)
        return
    handler = partial(PreviewHandler, directory=ROOT)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 5051 if args.port is None else args.port), handler)
    except (OSError, OverflowError) as exc:
        parser.exit(1, f"Cannot start avatar preview on port {args.port}: {exc}\n")
    with server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Avatar preview: {url}  (Ctrl+C to stop)", flush=True)
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
