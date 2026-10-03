"""Tiny local web dashboard. Serves only on this computer (127.0.0.1), nothing leaves the device."""
import json
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATE = {"status": "starting", "last_heard": "", "last_reply": "",
         "route": "", "confidence": None, "memories": 0, "emergency": None}
LOG_FILE = Path("online_log.jsonl")
PAGE = Path(__file__).with_name("dashboard.html")


def update(**changes):
    STATE.update(changes)


def _log_summary(n=15):
    if not LOG_FILE.exists():
        return [], {}
    entries = [json.loads(line) for line in LOG_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    totals = Counter(e.get("status", "ok") for e in entries)
    return list(reversed(entries[-n:])), dict(totals)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/state":
            log, totals = _log_summary()
            body = json.dumps({**STATE, "log": log, "totals": totals}).encode()
            ctype = "application/json"
        else:
            body = PAGE.read_bytes()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass    # keep the terminal clean


def start(port=8000):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"📊 Dashboard: http://localhost:{port}")