#!/bin/bash
set -e

APP_DATA="${OPENHOST_APP_DATA:-/data/app_data}"
TOR_HS_DIR="/var/lib/tor/hidden_service"
TOR_DATA_DIR="/var/lib/tor/data"
PERSISTENT_HS_DIR="$APP_DATA/hidden_service"

echo "[entrypoint] Setting up Tor hidden service..."

# Create Tor directories
mkdir -p "$TOR_HS_DIR" "$TOR_DATA_DIR"

# Restore persisted hidden service keys if they exist
if [ -d "$PERSISTENT_HS_DIR" ] && [ -f "$PERSISTENT_HS_DIR/hostname" ]; then
    echo "[entrypoint] Restoring persisted hidden service keys..."
    cp "$PERSISTENT_HS_DIR/"* "$TOR_HS_DIR/"
fi

# Tor requires strict permissions on hidden service directory
chmod 700 "$TOR_HS_DIR"

# Try to set ownership to debian-tor (Debian's tor user) or just leave as root
if id -u debian-tor >/dev/null 2>&1; then
    chown -R debian-tor:debian-tor "$TOR_HS_DIR" "$TOR_DATA_DIR"
    TOR_USER="debian-tor"
else
    echo "[entrypoint] No tor user found, running Tor as root"
    TOR_USER=""
fi

# Start the Python web server in the background
echo "[entrypoint] Starting web server..."
python3 /app/server.py &
SERVER_PID=$!

# Start Tor in the background (as appropriate user)
echo "[entrypoint] Starting Tor..."
if [ -n "$TOR_USER" ]; then
    tor -f /etc/tor/torrc --RunAsDaemon 0 &
else
    # Run as root — need to tell Tor it's okay
    tor -f /etc/tor/torrc --RunAsDaemon 0 --User root &
fi
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
