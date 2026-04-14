"""Simple file-based CMS for managing pages served on the Tor hidden service."""

import json
import os
import re
import time

import markdown

APP_DATA_DIR = os.environ.get("OPENHOST_APP_DATA", "/data/app_data")
PAGES_DIR = os.path.join(APP_DATA_DIR, "pages")
CONFIG_DIR = os.path.join(APP_DATA_DIR, "config")
STATE_FILE = os.path.join(APP_DATA_DIR, "state.json")
HOSTNAME_FILE = "/var/lib/tor/hidden_service/hostname"


def _ensure_dirs():
    os.makedirs(PAGES_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _sanitize_slug(slug: str) -> str:
    """Sanitize a page slug to be filesystem-safe."""
    slug = slug.strip().lower()
    slug = re.sub(r"[^a-z0-9\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "untitled"


def get_onion_address() -> str | None:
    """Read the .onion address."""
    persistent_hostname = os.path.join(APP_DATA_DIR, "hostname")
    for path in [persistent_hostname, HOSTNAME_FILE]:
        try:
            with open(path) as f:
                return f.read().strip()
        except FileNotFoundError:
            continue
    return None


def get_state() -> dict:
    """Read the current app state."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "unknown"}


def get_vanity_prefix() -> str:
    """Get the configured vanity prefix, if any."""
    config_path = os.path.join(CONFIG_DIR, "vanity_prefix")
    try:
        with open(config_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def set_vanity_prefix(prefix: str):
    """Set the vanity prefix (takes effect on next restart)."""
    _ensure_dirs()
    prefix = re.sub(r"[^a-z2-7]", "", prefix.lower())  # base32 chars only
    config_path = os.path.join(CONFIG_DIR, "vanity_prefix")
    with open(config_path, "w") as f:
        f.write(prefix)


def list_pages() -> list[dict]:
    """List all pages with metadata."""
    _ensure_dirs()
    pages = []
    for filename in sorted(os.listdir(PAGES_DIR)):
        if filename.endswith(".md"):
            slug = filename[:-3]
            filepath = os.path.join(PAGES_DIR, filename)
            stat = os.stat(filepath)
            with open(filepath) as f:
                content = f.read()
            # Extract title from first heading or use slug
            title = slug
            for line in content.split("\n"):
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            pages.append(
                {
                    "slug": slug,
                    "title": title,
                    "modified": stat.st_mtime,
                    "size": stat.st_size,
                }
            )
    return pages


def get_page(slug: str) -> dict | None:
    """Get a page by slug. Returns dict with slug, content, html."""
    _ensure_dirs()
    slug = _sanitize_slug(slug)
    filepath = os.path.join(PAGES_DIR, f"{slug}.md")
    if not os.path.exists(filepath):
        return None
    with open(filepath) as f:
        content = f.read()
    html = markdown.markdown(content, extensions=["fenced_code", "tables", "toc"])
    return {"slug": slug, "content": content, "html": html}


def save_page(slug: str, content: str) -> str:
    """Save a page. Returns the sanitized slug."""
    _ensure_dirs()
    slug = _sanitize_slug(slug)
    filepath = os.path.join(PAGES_DIR, f"{slug}.md")
    with open(filepath, "w") as f:
        f.write(content)
    return slug


def delete_page(slug: str) -> bool:
    """Delete a page. Returns True if it existed."""
    _ensure_dirs()
    slug = _sanitize_slug(slug)
    filepath = os.path.join(PAGES_DIR, f"{slug}.md")
    if os.path.exists(filepath):
        os.remove(filepath)
        return True
    return False


def rename_page(old_slug: str, new_slug: str) -> str | None:
    """Rename a page. Returns new slug or None if old page not found."""
    _ensure_dirs()
    old_slug = _sanitize_slug(old_slug)
    new_slug = _sanitize_slug(new_slug)
    old_path = os.path.join(PAGES_DIR, f"{old_slug}.md")
    new_path = os.path.join(PAGES_DIR, f"{new_slug}.md")
    if not os.path.exists(old_path):
        return None
    os.rename(old_path, new_path)
    return new_slug
