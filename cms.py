"""Simple file-based CMS for managing pages served on the Tor hidden service."""

import json
import os
import re
import shutil
import signal
import subprocess

import markdown

APP_DATA_DIR = os.environ.get("OPENHOST_APP_DATA_DIR", "/data/app_data/hidden-service")
PAGES_DIR = os.path.join(APP_DATA_DIR, "pages")
CUSTOM_KEYS_DIR = os.path.join(APP_DATA_DIR, "custom_keys")
STATE_FILE = os.path.join(APP_DATA_DIR, "state.json")
HOSTNAME_FILE = "/var/lib/tor/hidden_service/hostname"
TOR_HS_DIR = "/var/lib/tor/hidden_service"
PERSISTENT_HS_DIR = os.path.join(APP_DATA_DIR, "hidden_service")


def _ensure_dirs():
    os.makedirs(PAGES_DIR, exist_ok=True)
    os.makedirs(CUSTOM_KEYS_DIR, exist_ok=True)


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


# ---------------------------------------------------------------------------
# Custom key management
# ---------------------------------------------------------------------------

# Tor v3 hidden service key file format:
# - Secret key file: "== ed25519v1-secret: type0 ==" header (29 bytes) + 64 bytes key data = 96 bytes
# - Public key file: "== ed25519v1-public: type0 ==" header (29 bytes) + 32 bytes key data = 64 bytes

_SECRET_KEY_HEADER = b"== ed25519v1-secret: type0 ==\x00\x00\x00"
_PUBLIC_KEY_HEADER = b"== ed25519v1-public: type0 ==\x00\x00\x00"

SECRET_KEY_EXPECTED_SIZE = 96
PUBLIC_KEY_EXPECTED_SIZE = 64


def validate_secret_key(data: bytes) -> str | None:
    """Validate a Tor v3 Ed25519 secret key file. Returns error message or None if valid."""
    if len(data) != SECRET_KEY_EXPECTED_SIZE:
        return f"Secret key must be exactly {SECRET_KEY_EXPECTED_SIZE} bytes, got {len(data)}"
    if not data.startswith(_SECRET_KEY_HEADER):
        return "Secret key has invalid header (not a Tor v3 Ed25519 secret key)"
    return None


def validate_public_key(data: bytes) -> str | None:
    """Validate a Tor v3 Ed25519 public key file. Returns error message or None if valid."""
    if len(data) != PUBLIC_KEY_EXPECTED_SIZE:
        return f"Public key must be exactly {PUBLIC_KEY_EXPECTED_SIZE} bytes, got {len(data)}"
    if not data.startswith(_PUBLIC_KEY_HEADER):
        return "Public key has invalid header (not a Tor v3 Ed25519 public key)"
    return None


def get_custom_keys_info() -> dict | None:
    """Check if custom keys are staged and ready to apply. Returns info dict or None."""
    secret_path = os.path.join(CUSTOM_KEYS_DIR, "hs_ed25519_secret_key")
    public_path = os.path.join(CUSTOM_KEYS_DIR, "hs_ed25519_public_key")
    hostname_path = os.path.join(CUSTOM_KEYS_DIR, "hostname")
    if os.path.exists(secret_path) and os.path.exists(public_path):
        hostname = None
        if os.path.exists(hostname_path):
            with open(hostname_path) as f:
                hostname = f.read().strip()
        return {"staged": True, "hostname": hostname}
    return None


def stage_custom_keys(
    secret_key: bytes, public_key: bytes, hostname: str | None = None
) -> str | None:
    """Stage custom keys for the next restart. Returns error message or None on success."""
    _ensure_dirs()

    err = validate_secret_key(secret_key)
    if err:
        return err
    err = validate_public_key(public_key)
    if err:
        return err

    secret_path = os.path.join(CUSTOM_KEYS_DIR, "hs_ed25519_secret_key")
    public_path = os.path.join(CUSTOM_KEYS_DIR, "hs_ed25519_public_key")

    with open(secret_path, "wb") as f:
        f.write(secret_key)
    os.chmod(secret_path, 0o600)

    with open(public_path, "wb") as f:
        f.write(public_key)
    os.chmod(public_path, 0o600)

    if hostname:
        hostname_path = os.path.join(CUSTOM_KEYS_DIR, "hostname")
        with open(hostname_path, "w") as f:
            f.write(hostname.strip() + "\n")

    return None


def apply_custom_keys() -> str | None:
    """Apply staged custom keys by installing them into Tor's HS dir and restarting Tor.

    Returns error message or None on success.
    """
    secret_src = os.path.join(CUSTOM_KEYS_DIR, "hs_ed25519_secret_key")
    public_src = os.path.join(CUSTOM_KEYS_DIR, "hs_ed25519_public_key")
    hostname_src = os.path.join(CUSTOM_KEYS_DIR, "hostname")

    if not os.path.exists(secret_src) or not os.path.exists(public_src):
        return "No custom keys staged"

    # Install into Tor's hidden service directory
    os.makedirs(TOR_HS_DIR, exist_ok=True)
    shutil.copy2(secret_src, os.path.join(TOR_HS_DIR, "hs_ed25519_secret_key"))
    shutil.copy2(public_src, os.path.join(TOR_HS_DIR, "hs_ed25519_public_key"))
    if os.path.exists(hostname_src):
        shutil.copy2(hostname_src, os.path.join(TOR_HS_DIR, "hostname"))
    else:
        # Remove old hostname so Tor regenerates it from the new key
        hostname_file = os.path.join(TOR_HS_DIR, "hostname")
        if os.path.exists(hostname_file):
            os.remove(hostname_file)

    os.chmod(TOR_HS_DIR, 0o700)
    os.chmod(os.path.join(TOR_HS_DIR, "hs_ed25519_secret_key"), 0o600)
    os.chmod(os.path.join(TOR_HS_DIR, "hs_ed25519_public_key"), 0o600)

    # Also persist to the persistent HS dir so entrypoint restores them on restart
    os.makedirs(PERSISTENT_HS_DIR, exist_ok=True)
    shutil.copy2(secret_src, os.path.join(PERSISTENT_HS_DIR, "hs_ed25519_secret_key"))
    shutil.copy2(public_src, os.path.join(PERSISTENT_HS_DIR, "hs_ed25519_public_key"))
    if os.path.exists(hostname_src):
        shutil.copy2(hostname_src, os.path.join(PERSISTENT_HS_DIR, "hostname"))
        shutil.copy2(hostname_src, os.path.join(APP_DATA_DIR, "hostname"))

    # Clean up staged keys
    shutil.rmtree(CUSTOM_KEYS_DIR)
    os.makedirs(CUSTOM_KEYS_DIR, exist_ok=True)

    # Restart Tor to pick up new keys
    restart_err = _restart_tor()
    if restart_err:
        return restart_err

    return None


def clear_custom_keys():
    """Remove any staged custom keys."""
    if os.path.exists(CUSTOM_KEYS_DIR):
        shutil.rmtree(CUSTOM_KEYS_DIR)
    os.makedirs(CUSTOM_KEYS_DIR, exist_ok=True)


def _restart_tor() -> str | None:
    """Send SIGHUP to Tor to reload config, or kill it to trigger entrypoint restart.

    Returns error message or None on success.
    """
    try:
        # Find the Tor process
        result = subprocess.run(["pgrep", "-x", "tor"], capture_output=True, text=True)
        if result.returncode != 0:
            return "Tor process not found"
        tor_pid = int(result.stdout.strip().split("\n")[0])
        # Kill Tor — the entrypoint will detect the exit and shut down the container,
        # which OpenHost will then restart with the new keys in persistent storage.
        os.kill(tor_pid, signal.SIGTERM)
        return None
    except Exception as e:
        return f"Failed to restart Tor: {e}"
