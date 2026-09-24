#!/usr/bin/env python3
"""
Props trading desk: local demo server.

  python src/dashboard/server.py              # opens http://localhost:8050
  python src/dashboard/server.py --port 9000
  python src/dashboard/server.py --no-browser

Reads the odds snapshot from odds-api/data (real or sample), serves the dashboard,
and runs the bet simulator. Standard library only; works offline.

API (JSON):
  GET  /api/board             games, props, our prices, trader status
  GET  /api/live              exposure, alerts, bet feed, change log, totals
  GET  /api/quotes?id=<prop>  every book's price for one prop
  POST /api/prop              {id, action: live|off|suspended|set_price|set_line|shade|reset, ...}
  POST /api/bulk              {event_id, market_key (optional), status: live|off|suspended}
  POST /api/settings          {default_vig, simulator}
  POST /api/vig               {scope: default|game|market|prop, vig, event_id, market_key, id}
                              vig as a fraction (0.04 = 4%); null removes that level's override
  POST /api/bets/clear
"""

import argparse
import json
import mimetypes
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/, for general_utility
sys.path.insert(0, str(Path(__file__).resolve().parent))      # dashboard modules

from general_utility.create_path import DASHBOARD_STATE_FILE, DASHBOARD_STATIC_DIR, rel  # noqa: E402
from board import SnapshotMissing, build_board, load_snapshot  # noqa: E402
from desk import Desk  # noqa: E402
from simulator import BetSimulator  # noqa: E402

DESK = None
LOAD_ERROR = None


class Handler(BaseHTTPRequestHandler):
    server_version = "PropDesk/1.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet during demos
        pass

    # ---------------------------------------------------------------- utils

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        file = (DASHBOARD_STATIC_DIR / path.lstrip("/")).resolve()
        if DASHBOARD_STATIC_DIR.resolve() not in file.parents or not file.is_file():
            self.send_error(404)
            return
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def no_data(self):
        self.send_json({"error": LOAD_ERROR}, status=503)

    # --------------------------------------------------------------- routes

    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            return self.serve_static(url.path)
        if DESK is None:
            return self.no_data()
        if url.path == "/api/board":
            return self.send_json(DESK.board())
        if url.path == "/api/live":
            return self.send_json(DESK.live_view())
        if url.path == "/api/quotes":
            pid = parse_qs(url.query).get("id", [""])[0]
            if pid not in DESK.props:
                return self.send_json({"error": "Unknown prop"}, 404)
            return self.send_json(DESK.book_quotes(pid))
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        if DESK is None:
            return self.no_data()
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/prop":
                return self.send_json(DESK.prop_action(body["id"], body["action"], body))
            if path == "/api/bulk":
                return self.send_json(DESK.bulk(body["event_id"], body.get("market_key"), body["status"]))
            if path == "/api/vig":
                return self.send_json(DESK.set_vig(body["scope"], body.get("vig"), event_id=body.get("event_id"),
                                                   market_key=body.get("market_key"), pid=body.get("id")))
            if path == "/api/settings":
                return self.send_json(DESK.update_settings(body))
            if path == "/api/bets/clear":
                DESK.clear_bets()
                return self.send_json({"ok": True})
            self.send_json({"error": "Not found"}, 404)
        except KeyError as e:
            self.send_json({"error": f"Missing or unknown value: {e}"}, 400)
        except ValueError as e:
            self.send_json({"error": str(e)}, 400)


def main():
    global DESK, LOAD_ERROR
    ap = argparse.ArgumentParser(description="Run the props trading desk demo.")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    try:
        meta, events, rows = load_snapshot()
        games, props = build_board(meta, events, rows)
        DESK = Desk(meta, games, props, DASHBOARD_STATE_FILE)
        BetSimulator(DESK).start()
        source = "SAMPLE data" if meta.get("source") == "sample" else "real odds"
        print(f"Loaded {len(games)} games and {len(props):,} props ({source}, snapshot {meta.get('pulled_at', '?')[:16]})")
        print(f"Trader state: {rel(DASHBOARD_STATE_FILE)}")
    except SnapshotMissing as e:
        LOAD_ERROR = str(e)
        print(LOAD_ERROR)

    url = f"http://localhost:{args.port}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError:
        sys.exit(f"Port {args.port} is already in use. Is the dashboard already running? "
                 f"Otherwise try --port {args.port + 1}.")
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
