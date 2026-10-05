"""Share a list to your phone without the cloud: a QR code on the screen opens the list straight from
this device, over your own Wi-Fi. The link is random, read-only, shows one list, and expires in 10 minutes.
The private dashboard stays on 127.0.0.1; only this tiny share server listens on the Wi-Fi."""
import html
import io
import secrets
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qrcode
import qrcode.image.svg

PORT = 8765
LIFETIME = 10 * 60          # seconds a share link works
_shares = {}                # token -> (expires_at, title, items, when)
_server = None

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>
body{{font-family:system-ui,sans-serif;max-width:520px;margin:24px auto;padding:0 16px;color:#222}}
h1{{font-size:24px;margin:0 0 4px}} .sub{{color:#777;font-size:13px;margin-bottom:16px}}
ul{{padding:0}} li{{list-style:none;padding:12px 4px;border-bottom:1px solid #eee;font-size:18px}}
label{{display:flex;gap:12px;align-items:center}} input{{width:22px;height:22px}}
button{{margin-top:20px;padding:12px 18px;font-size:16px;border-radius:10px;border:0;background:#1f6f5c;color:#fff}}
@media print{{button,.sub{{display:none}}}}
</style></head><body><h1>{title}</h1>
<div class="sub">Shared from Jarvis on {when}, straight over your Wi-Fi. This link expires in 10 minutes.</div>
<ul>{items}</ul><button onclick="window.print()">Save as PDF / Print</button></body></html>"""


def lan_ip():
    """This device's address on the Wi-Fi. Sends nothing: the 'connect' only asks for a route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 53))
            return s.getsockname()[0]
    except OSError:
        return None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        token = self.path.rsplit("/", 1)[-1]
        share = _shares.get(token) if self.path.startswith("/s/") else None
        if not share or share[0] < time.time():
            _shares.pop(token, None)
            body, code, ctype = b"This link has expired or doesn't exist.", 404, "text/plain; charset=utf-8"
        else:
            _, title, items, when = share
            rows = "".join(f'<li><label><input type="checkbox">{html.escape(i)}</label></li>' for i in items)
            body = PAGE.format(title=html.escape(title), when=when, items=rows or "<li>(empty)</li>").encode()
            code, ctype = 200, "text/html; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _ensure_server():
    global _server
    if _server is None:
        _server = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()


def share_list(title, items):
    """Returns (url, qr_svg), or (None, None) if this device isn't on a network."""
    ip = lan_ip()
    if not ip:
        return None, None
    _ensure_server()
    token = secrets.token_urlsafe(9)                      # random, unguessable
    _shares[token] = (time.time() + LIFETIME, title, list(items), datetime.now().strftime("%d %B, %I:%M %p"))
    url = f"http://{ip}:{PORT}/s/{token}"
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10).save(buf)
    return url, buf.getvalue().decode()