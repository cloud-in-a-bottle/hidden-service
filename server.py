"""Simple web server that serves content over the Tor hidden service (HTTPS) and provides a health check (HTTP)."""

import http.server
import json
import os
import socketserver
import ssl
import sys
import threading

ONION_PORT = 3000
HEALTH_PORT = 8080
HOSTNAME_FILE = "/var/lib/tor/hidden_service/hostname"
APP_DATA_DIR = os.environ.get("OPENHOST_APP_DATA", "/data/app_data")
TLS_CERT = "/app/tls/cert.pem"
TLS_KEY = "/app/tls/key.pem"


def get_onion_address():
    """Read the .onion address from Tor's hidden service directory."""
    # Check persistent storage first, then Tor's live directory
    persistent_hostname = os.path.join(APP_DATA_DIR, "hostname")
    for path in [persistent_hostname, HOSTNAME_FILE]:
        try:
            with open(path) as f:
                return f.read().strip()
        except FileNotFoundError:
            continue
    return None


class OnionHandler(http.server.BaseHTTPRequestHandler):
    """Handler for requests arriving via the Tor hidden service (HTTPS)."""

    def do_GET(self):
        onion = get_onion_address() or "unknown (still bootstrapping)"

        if self.path == "/":
            body = f"""<!DOCTYPE html>
<html>
<head>
    <title>Tor Hidden Service</title>
    <style>
        body {{
            font-family: monospace;
            background: #1a1a2e;
            color: #e0e0e0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }}
        .container {{
            text-align: center;
            padding: 2rem;
            border: 1px solid #444;
            border-radius: 8px;
            background: #16213e;
            max-width: 600px;
        }}
        h1 {{ color: #7f5af0; }}
        .onion {{ color: #2cb67d; word-break: break-all; }}
        .info {{ color: #888; margin-top: 1rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Tor Hidden Service</h1>
        <p>This site is hosted as a Tor hidden service on OpenHost.</p>
        <p class="onion">{onion}</p>
        <p class="info">Powered by OpenHost + Tor</p>
    </div>
</body>
</html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())
        elif self.path == "/status":
            data = {"status": "ok", "onion_address": onion}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def log_message(self, format, *args):
        print(f"[onion] {args[0]}", flush=True)


class HealthHandler(http.server.BaseHTTPRequestHandler):
    """Handler for OpenHost health checks and status page (HTTP, not exposed via Tor)."""

    def do_GET(self):
        onion = get_onion_address()

        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {
                "status": "healthy",
                "onion_address": onion,
                "tor_bootstrapped": onion is not None,
            }
            self.wfile.write(json.dumps(data).encode())
        elif self.path == "/":
            onion_display = onion or "Tor is still bootstrapping..."
            onion_url = f"https://{onion}" if onion else "#"
            body = f"""<!DOCTYPE html>
<html>
<head>
    <title>Hidden Service Status</title>
    <style>
        body {{
            font-family: monospace;
            background: #1a1a2e;
            color: #e0e0e0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            margin: 0;
        }}
        .container {{
            text-align: center;
            padding: 2rem;
            border: 1px solid #444;
            border-radius: 8px;
            background: #16213e;
            max-width: 600px;
        }}
        h1 {{ color: #7f5af0; }}
        .onion {{ color: #2cb67d; word-break: break-all; font-size: 1.1em; }}
        .onion a {{ color: #2cb67d; text-decoration: none; }}
        .onion a:hover {{ text-decoration: underline; }}
        .info {{ color: #888; margin-top: 1rem; }}
        .status {{ color: {"#2cb67d" if onion else "#e53170"}; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Tor Hidden Service</h1>
        <p class="status">Status: {"Online (HTTPS)" if onion else "Bootstrapping..."}</p>
        <p>Onion address:</p>
        <p class="onion"><a href="{onion_url}">{onion_display}</a></p>
        <p class="info">Access this address using the Tor Browser.</p>
        <p class="info">Served over HTTPS with a self-signed certificate.</p>
        <p class="info">Powered by OpenHost + Tor</p>
    </div>
</body>
</html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[health] {args[0]}", flush=True)


def run_https_server(handler_class, port, name):
    """Run an HTTPS server with self-signed cert."""
    server = socketserver.TCPServer(("0.0.0.0", port), handler_class)
    server.allow_reuse_address = True

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=TLS_CERT, keyfile=TLS_KEY)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)

    print(f"[{name}] Listening on port {port} (HTTPS)", flush=True)
    server.serve_forever()


def run_http_server(handler_class, port, name):
    """Run a plain HTTP server (for health checks)."""
    server = socketserver.TCPServer(("0.0.0.0", port), handler_class)
    server.allow_reuse_address = True
    print(f"[{name}] Listening on port {port} (HTTP)", flush=True)
    server.serve_forever()


def main():
    # Start onion service handler with HTTPS (Tor connects to this)
    onion_thread = threading.Thread(
        target=run_https_server, args=(OnionHandler, ONION_PORT, "onion"), daemon=True
    )
    onion_thread.start()

    # Start health check handler as plain HTTP (OpenHost connects to this)
    print("[main] Starting servers...", flush=True)
    run_http_server(HealthHandler, HEALTH_PORT, "health")


if __name__ == "__main__":
    main()
