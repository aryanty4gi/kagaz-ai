"""Run each Kagaz agent as its own process.

    python run_all.py

The coordinator gets an Agentverse mailbox so ASI:One can reach it. With
AGENTVERSE_API_KEY set (or KAGAZ_ALL_MAILBOX=true) every specialist gets its
own mailbox and profile too, so each one can be chatted with on its own.
On first run, open the coordinator's inspector link from the logs and click
Connect -> Mailbox.
"""

import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

AGENTS = ["coordinator", "reader", "verify", "rules", "action", "expert", "marketplace"]


def pump(name: str, proc: subprocess.Popen):
    for line in proc.stdout:
        print(f"[{name:<11}] {line}", end="")


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        lines = ["Kagaz AI agents are running. Chat with @kagaz on ASI:One.", ""]
        try:
            from kagaz import config

            for role in config.AGENTS:
                lines.append(f"{role:<12} {config.address_for(role)}")
        except Exception:  # never break the health check, never leak error text
            lines.append("(addresses unavailable)")
        body = ("\n".join(lines) + "\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET

    # Plain page, so hide the Python version from the Server header.
    server_version = "kagaz"
    sys_version = ""

    def log_message(self, *args):
        pass


def start_cloud_helpers():
    """On a cloud host (PORT is set), serve a health page and keep the service awake."""
    port = os.getenv("PORT")
    if not port:
        return
    server = HTTPServer(("0.0.0.0", int(port)), _Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[cloud      ] health page on port {port}", flush=True)

    # Free hosting plans sleep after ~15 idle minutes. Ping our own public URL.
    url = os.getenv("KAGAZ_KEEPALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if url:
        def ping():
            while True:
                time.sleep(300)
                try:
                    urllib.request.urlopen(url, timeout=20).read()
                except Exception as err:
                    print(f"[cloud      ] keep-alive ping failed: {err}", flush=True)

        threading.Thread(target=ping, daemon=True).start()


def main():
    start_cloud_helpers()
    # With an Agentverse API key every agent gets (and auto-creates) its own
    # mailbox, so each one can be chatted with on its own from ASI:One.
    default = "true" if os.getenv("AGENTVERSE_API_KEY") else "false"
    all_mailbox = os.getenv("KAGAZ_ALL_MAILBOX", default).lower() == "true"
    procs = []
    for name in AGENTS:
        mailbox = "true" if (name == "coordinator" or all_mailbox) else "false"
        env = dict(os.environ, KAGAZ_USE_MAILBOX=mailbox, PYTHONUNBUFFERED="1")
        proc = subprocess.Popen(
            [sys.executable, "-m", f"agents.{name}_agent"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        procs.append(proc)
        threading.Thread(target=pump, args=(name, proc), daemon=True).start()

    def stop(*_):
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    # If any agent exits, stop the rest and exit so the host restarts everything.
    while all(p.poll() is None for p in procs):
        time.sleep(2)
    print("[run_all    ] an agent stopped; shutting down so it can restart", flush=True)
    for p in procs:
        if p.poll() is None:
            p.terminate()
    sys.exit(1)


if __name__ == "__main__":
    main()
