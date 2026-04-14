#!/bin/bash
set -e

APP_DATA="${OPENHOST_APP_DATA:-/data/app_data}"
TOR_HS_DIR="/var/lib/tor/hidden_service"
TOR_DATA_DIR="/var/lib/tor/data"
PERSISTENT_HS_DIR="$APP_DATA/hidden_service"
PAGES_DIR="$APP_DATA/pages"
STATE_FILE="$APP_DATA/state.json"

TLS_DIR="/app/tls"
PERSISTENT_TLS_DIR="$APP_DATA/tls"

echo "[entrypoint] Setting up Tor hidden service..."

# Create all directories
mkdir -p "$TOR_HS_DIR" "$TOR_DATA_DIR" "$TLS_DIR" "$PAGES_DIR"

# Create default index page if none exists
if [ ! -f "$PAGES_DIR/index.md" ]; then
    cat > "$PAGES_DIR/index.md" << 'DEFAULTPAGE'
# Welcome

This is a Tor hidden service powered by [OpenHost](https://github.com/imbue-ai/openhost).

Edit this page from the admin panel.
DEFAULTPAGE
    echo "[entrypoint] Created default index page"
fi

# Initialize state file if it doesn't exist
if [ ! -f "$STATE_FILE" ]; then
    echo '{"status": "initializing"}' > "$STATE_FILE"
fi

# Generate or restore self-signed TLS certificate
if [ -d "$PERSISTENT_TLS_DIR" ] && [ -f "$PERSISTENT_TLS_DIR/cert.pem" ]; then
    echo "[entrypoint] Restoring persisted TLS certificate..."
    cp "$PERSISTENT_TLS_DIR/cert.pem" "$TLS_DIR/"
    cp "$PERSISTENT_TLS_DIR/key.pem" "$TLS_DIR/"
else
    echo "[entrypoint] Generating self-signed TLS certificate..."
    openssl req -x509 -newkey rsa:2048 -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem" \
        -days 3650 -nodes -subj "/CN=onion-hidden-service" 2>/dev/null
    mkdir -p "$PERSISTENT_TLS_DIR"
    cp "$TLS_DIR/cert.pem" "$PERSISTENT_TLS_DIR/"
    cp "$TLS_DIR/key.pem" "$PERSISTENT_TLS_DIR/"
    echo "[entrypoint] TLS certificate generated and persisted"
fi

# Restore existing hidden service keys if available
if [ -d "$PERSISTENT_HS_DIR" ] && [ -f "$PERSISTENT_HS_DIR/hostname" ]; then
    echo "[entrypoint] Restoring persisted hidden service keys..."
    cp "$PERSISTENT_HS_DIR/"* "$TOR_HS_DIR/"
    echo "[entrypoint] Restored onion address: $(cat "$PERSISTENT_HS_DIR/hostname")"
fi

# Standard startup
chmod 700 "$TOR_HS_DIR"
chown -R root:root "$TOR_HS_DIR" "$TOR_DATA_DIR"

# Start the Python web server in the background
echo "[entrypoint] Starting web server..."
python3 /app/server.py &
SERVER_PID=$!

# Start Tor as root
echo "[entrypoint] Starting Tor..."
tor -f /etc/tor/torrc --RunAsDaemon 0 --User root &
TOR_PID=$!

# Wait for Tor to generate the hostname file
echo "[entrypoint] Waiting for Tor to bootstrap..."
for i in $(seq 1 120); do
    if [ -f "$TOR_HS_DIR/hostname" ]; then
        ONION=$(cat "$TOR_HS_DIR/hostname")
        echo "[entrypoint] Hidden service is live at: $ONION"

        # Persist the hidden service keys
        mkdir -p "$PERSISTENT_HS_DIR"
        cp "$TOR_HS_DIR/hostname" "$PERSISTENT_HS_DIR/"
        cp "$TOR_HS_DIR/hs_ed25519_secret_key" "$PERSISTENT_HS_DIR/" 2>/dev/null || true
        cp "$TOR_HS_DIR/hs_ed25519_public_key" "$PERSISTENT_HS_DIR/" 2>/dev/null || true

        # Also copy hostname to app_data root for easy access
        cp "$TOR_HS_DIR/hostname" "$APP_DATA/hostname"
        echo "[entrypoint] Keys persisted to $PERSISTENT_HS_DIR"
        echo "{\"status\": \"running\", \"onion\": \"$ONION\"}" > "$STATE_FILE"
        break
    fi
    sleep 2
done

if [ ! -f "$TOR_HS_DIR/hostname" ]; then
    echo "[entrypoint] WARNING: Tor did not generate hostname within 240 seconds"
fi

# Wait for either process to exit
wait -n $TOR_PID $SERVER_PID
EXIT_CODE=$?

echo "[entrypoint] A process exited with code $EXIT_CODE, shutting down..."
kill $TOR_PID $SERVER_PID 2>/dev/null || true
wait
exit $EXIT_CODE
