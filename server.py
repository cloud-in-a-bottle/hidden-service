"""
Tor Hidden Service server with admin CMS and HTTPS onion serving.

Port 8080 (HTTP): Admin panel + health check, behind OpenHost auth.
Port 3000 (HTTPS): Public-facing onion site serving CMS pages.
"""

import html
import http.server
import json
import os
import socketserver
import threading
import urllib.parse

import cms

ONION_PORT = 3000
ADMIN_PORT = 8080
TEMPLATES_DIR = "/app/templates"
STATIC_DIR = "/app/static"
APP_DATA_DIR = os.environ.get("OPENHOST_APP_DATA", "/data/app_data")


def _read_template(name: str) -> str:
    with open(os.path.join(TEMPLATES_DIR, name)) as f:
        return f.read()


def _render_admin(title: str, content: str, flash: str = "") -> str:
    base = _read_template("admin_base.html")
    return base.format(title=html.escape(title), content=content, flash=flash)


def _flash_html(message: str, kind: str = "success") -> str:
    return f'<div class="flash flash-{kind}">{html.escape(message)}</div>'


def _page_list_html(pages: list[dict], show_empty: bool = True) -> str:
    if not pages:
        if show_empty:
            return '<p style="color: var(--text-secondary);">No pages yet. <a href="/pages/new">Create one</a>.</p>'
        return ""
    items = []
    for p in pages:
        items.append(
            f"<li>"
            f'<div><span class="page-title">{html.escape(p["title"])}</span>'
            f'<span class="page-slug">/{html.escape(p["slug"])}</span></div>'
            f'<div class="page-actions">'
            f'<a href="/pages/edit/{html.escape(p["slug"])}" class="btn btn-secondary btn-sm">Edit</a>'
            f"</div>"
            f"</li>"
        )
    return '<ul class="page-list">' + "\n".join(items) + "</ul>"


class AdminHandler(http.server.BaseHTTPRequestHandler):
    """Admin panel and health check handler (port 8080, behind OpenHost auth)."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/health":
            self._health()
        elif path == "/":
            self._dashboard()
        elif path == "/pages":
            self._pages_list()
        elif path == "/pages/new":
            self._page_editor(slug="", content="", is_new=True)
        elif path.startswith("/pages/edit/"):
            slug = path[len("/pages/edit/") :]
            self._page_editor_load(slug)
        elif path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
        else:
            self._not_found()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        body = self._read_body()
        params = urllib.parse.parse_qs(body, keep_blank_values=True)

        if path == "/pages/new":
            self._page_save_new(params)
        elif path.startswith("/pages/edit/"):
            slug = path[len("/pages/edit/") :]
            self._page_save_existing(slug, params)
        elif path.startswith("/pages/delete/"):
            slug = path[len("/pages/delete/") :]
            self._page_delete(slug)
        else:
            self._not_found()

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode("utf-8")

    def _respond_html(self, code: int, body: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _respond_json(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _redirect(self, location: str):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _not_found(self):
        self._respond_html(404, _render_admin("Not Found", "<h2>404 Not Found</h2>"))

    def _health(self):
        onion = cms.get_onion_address()
        state = cms.get_state()
        self._respond_json(
            200,
            {
                "status": "healthy",
                "onion_address": onion,
                "tor_bootstrapped": onion is not None,
                "app_state": state.get("status", "unknown"),
            },
        )

    def _dashboard(self):
        onion = cms.get_onion_address() or "Bootstrapping..."
        state = cms.get_state()
        pages = cms.list_pages()

        tpl = _read_template("admin_dashboard.html")
        content = tpl.format(
            onion_address=html.escape(onion),
            status=html.escape(state.get("status", "unknown")),
            page_list=_page_list_html(pages),
        )
        self._respond_html(200, _render_admin("Dashboard", content))

    def _pages_list(self):
        pages = cms.list_pages()
        tpl = _read_template("admin_pages.html")
        content = tpl.format(page_list=_page_list_html(pages))
        self._respond_html(200, _render_admin("Pages", content))

    def _page_editor(self, slug: str, content: str, is_new: bool, flash: str = ""):
        tpl = _read_template("admin_edit.html")
        editor = tpl.format(
            heading="New Page" if is_new else f"Edit: {slug}",
            action="/pages/new" if is_new else f"/pages/edit/{html.escape(slug)}",
            slug=html.escape(slug),
            content=html.escape(content),
            slug_readonly="" if is_new else 'readonly style="opacity:0.6"',
            delete_button=""
            if is_new
            else f'<form method="POST" action="/pages/delete/{html.escape(slug)}" style="display:inline" '
            f"onsubmit=\"return confirm('Delete this page?');\">"
            f'<button type="submit" class="btn btn-danger">Delete</button></form>',
        )
        self._respond_html(
            200,
            _render_admin(
                "New Page" if is_new else f"Edit {slug}", editor, flash=flash
            ),
        )

    def _page_editor_load(self, slug: str):
        page = cms.get_page(slug)
        if not page:
            self._not_found()
            return
        self._page_editor(slug=page["slug"], content=page["content"], is_new=False)

    def _page_save_new(self, params: dict):
        slug = params.get("slug", [""])[0].strip()
        content = params.get("content", [""])[0]

        if not slug:
            self._page_editor(
                slug="",
                content=content,
                is_new=True,
                flash=_flash_html("Slug is required.", "error"),
            )
            return

        # Check if page already exists
        if cms.get_page(slug):
            self._page_editor(
                slug=slug,
                content=content,
                is_new=True,
                flash=_flash_html(f"Page '{slug}' already exists.", "error"),
            )
            return

        saved_slug = cms.save_page(slug, content)
        self._redirect(f"/pages/edit/{saved_slug}")

    def _page_save_existing(self, slug: str, params: dict):
        content = params.get("content", [""])[0]
        cms.save_page(slug, content)
        self._page_editor(
            slug=slug, content=content, is_new=False, flash=_flash_html("Page saved.")
        )

    def _page_delete(self, slug: str):
        cms.delete_page(slug)
        self._redirect("/pages")

    def _serve_static(self, filename: str):
        filepath = os.path.join(STATIC_DIR, filename)
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            return
        ext = os.path.splitext(filename)[1]
        content_type = {
            ".css": "text/css",
            ".js": "application/javascript",
            ".png": "image/png",
            ".svg": "image/svg+xml",
        }.get(ext, "application/octet-stream")

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        with open(filepath, "rb") as f:
            data = f.read()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        print(f"[admin] {args[0]}", flush=True)


class OnionHandler(http.server.BaseHTTPRequestHandler):
    """Public-facing onion site handler (port 3000, HTTPS via Tor)."""

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/":
            self._serve_page("index")
        elif path == "/status":
            self._status()
        else:
            # Strip leading slash to get slug
            slug = path.lstrip("/")
            self._serve_page(slug)

    def _serve_page(self, slug: str):
        page = cms.get_page(slug)
        if not page:
            self._not_found()
            return

        pages = cms.list_pages()
        nav = self._build_nav(pages)

        # Extract title from first h1 or use slug
        title = slug
        for line in page["content"].split("\n"):
            if line.startswith("# "):
                title = line[2:].strip()
                break

        tpl = _read_template("onion_page.html")
        body = tpl.format(
            title=html.escape(title),
            nav=nav,
            body=page["html"],
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _build_nav(self, pages: list[dict]) -> str:
        links = []
        for p in pages:
            href = "/" if p["slug"] == "index" else f"/{p['slug']}"
            links.append(f'<a href="{html.escape(href)}">{html.escape(p["title"])}</a>')
        if links:
            return '<nav class="nav">' + " ".join(links) + "</nav>"
        return ""

    def _not_found(self):
        tpl = _read_template("onion_page.html")
        body = tpl.format(
            title="Not Found",
            nav="",
            body="<h1>404</h1><p>Page not found.</p>",
        )
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def _status(self):
        onion = cms.get_onion_address()
        data = {"status": "ok", "onion_address": onion}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        print(f"[onion] {args[0]}", flush=True)


def run_http_server(handler_class, port, name):
    """Run a plain HTTP server."""
    server = socketserver.TCPServer(("0.0.0.0", port), handler_class)
    server.allow_reuse_address = True
    print(f"[{name}] Listening on port {port} (HTTP)", flush=True)
    server.serve_forever()


def main():
    # Start onion service handler as plain HTTP (Tor already provides encryption)
    onion_thread = threading.Thread(
        target=run_http_server, args=(OnionHandler, ONION_PORT, "onion"), daemon=True
    )
    onion_thread.start()

    # Start admin handler as plain HTTP (OpenHost connects to this)
    print("[main] Starting servers...", flush=True)
    run_http_server(AdminHandler, ADMIN_PORT, "admin")


if __name__ == "__main__":
    main()
