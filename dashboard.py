"""Tiny local web dashboard. Serves only on this computer (127.0.0.1), nothing leaves the device.
Touch buttons on the screen send small actions back to Jarvis: quiet mode, offline mode, mic mute,
and closing a card (like a QR code). Only these four actions exist, and only this device can send them."""
import json
import queue
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = {"status": "starting", "last_heard": "", "last_reply": "",
         "route": "", "confidence": None, "memories": 0, "emergency": None, "activity": None}
LOG_FILE = Path("online_log.jsonl")
PAGE = Path(__file__).with_name("dashboard.html")
ACTIONS = queue.Queue()                      # buttons pressed on the screen, read by main.py
ALLOWED = {"toggle_quiet", "toggle_offline", "toggle_mic", "close_card"}
PORT = 8000


def update(**changes):
    if changes.get("emergency"):
        changes["emergency_at"] = time.time()      # so it can clear once the talk moves on
    STATE.update(changes)


def _log_summary(n=15):
    if not LOG_FILE.exists():
        return [], {}
    entries = [json.loads(line) for line in LOG_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    totals = Counter(e.get("status", "ok") for e in entries)
    return list(reversed(entries[-n:])), dict(totals)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/state":
            log, totals = _log_summary()
            self._reply(200, json.dumps({**STATE, "log": log, "totals": totals}).encode(), "application/json")
        else:
            self._reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        # Only our own page can press buttons: the custom header makes browsers block other websites,
        # and the Host check stops tricks that point another name at this computer.
        host_ok = self.headers.get("Host", "") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        if self.path == "/action" and host_ok and self.headers.get("X-Jarvis") == "1":
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 200)
                action = json.loads(self.rfile.read(length) or b"{}").get("do")
            except (ValueError, AttributeError):
                action = None
            if action in ALLOWED:
                ACTIONS.put(action)
                self._reply(200, b"ok", "text/plain")
                return
        self._reply(400, b"no", "text/plain")

    def log_message(self, *args):
        pass    # keep the terminal clean


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass    # a browser tab closed or refreshed mid-update: harmless, keep the terminal clean


def start(port=PORT):
    server = _QuietServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"📊 Dashboard: http://localhost:{port}")