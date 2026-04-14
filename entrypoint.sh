#!/bin/bash
set -e

APP_DATA="${OPENHOST_APP_DATA:-/data/app_data}"
TOR_HS_DIR="/var/lib/tor/hidden_service"
TOR_DATA_DIR="/var/lib/tor/data"
PERSISTENT_HS_DIR="$APP_DATA/hidden_service"
PAGES_DIR="$APP_DATA/pages"
CONFIG_DIR="$APP_DATA/config"
STATE_FILE="$APP_DATA/state.json"

TLS_DIR="/app/tls"
PERSISTENT_TLS_DIR="$APP_DATA/tls"

# Vanity prefix from config file (set via admin UI)
VANITY_CONFIG="$CONFIG_DIR/vanity_prefix"

echo "[entrypoint] Setting up Tor hidden service..."

# Create all directories
mkdir -p "$TOR_HS_DIR" "$TOR_DATA_DIR" "$TLS_DIR" "$PAGES_DIR" "$CONFIG_DIR"

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

# Handle vanity onion generation or restore existing keys
if [ -d "$PERSISTENT_HS_DIR" ] && [ -f "$PERSISTENT_HS_DIR/hostname" ]; then
    echo "[entrypoint] Restoring persisted hidden service keys..."
    cp "$PERSISTENT_HS_DIR/"* "$TOR_HS_DIR/"
    echo "[entrypoint] Restored onion address: $(cat "$PERSISTENT_HS_DIR/hostname")"
elif [ -f "$VANITY_CONFIG" ]; then
    VANITY_PREFIX=$(cat "$VANITY_CONFIG")
    if [ -n "$VANITY_PREFIX" ]; then
        echo "[entrypoint] Generating vanity onion address with prefix: $VANITY_PREFIX"
        echo "{\"status\": \"generating_vanity\", \"prefix\": \"$VANITY_PREFIX\"}" > "$STATE_FILE"

        # Start the web server early so health checks pass during generation
        echo "[entrypoint] Starting web server (vanity generation in progress)..."
        python3 /app/server.py &
        SERVER_PID=$!

        # Run mkp224o — outputs a directory named with the .onion address
        VANITY_OUT="/tmp/vanity_out"
        mkdir -p "$VANITY_OUT"
        echo "[entrypoint] Running mkp224o (this may take a while)..."
        if mkp224o -n 1 -d "$VANITY_OUT" "$VANITY_PREFIX"; then
            # Find the generated directory
            GENERATED_DIR=$(find "$VANITY_OUT" -mindepth 1 -maxdepth 1 -type d | head -1)
            if [ -n "$GENERATED_DIR" ]; then
                ONION_ADDR=$(basename "$GENERATED_DIR")
                echo "[entrypoint] Vanity address generated: $ONION_ADDR"

                # Install the keys
                cp "$GENERATED_DIR/hs_ed25519_secret_key" "$TOR_HS_DIR/"
                cp "$GENERATED_DIR/hs_ed25519_public_key" "$TOR_HS_DIR/"
                echo "$ONION_ADDR" > "$TOR_HS_DIR/hostname"

                # Persist
                mkdir -p "$PERSISTENT_HS_DIR"
                cp "$TOR_HS_DIR/hostname" "$PERSISTENT_HS_DIR/"
                cp "$TOR_HS_DIR/hs_ed25519_secret_key" "$PERSISTENT_HS_DIR/"
                cp "$TOR_HS_DIR/hs_ed25519_public_key" "$PERSISTENT_HS_DIR/"
                cp "$TOR_HS_DIR/hostname" "$APP_DATA/hostname"
                echo "[entrypoint] Vanity keys persisted"
            fi
        else
            echo "[entrypoint] WARNING: mkp224o failed, falling back to random address"
        fi
        rm -rf "$VANITY_OUT"

        # Tor will start below, server is already running
        echo "{\"status\": \"starting_tor\"}" > "$STATE_FILE"

        # Start Tor
        echo "[entrypoint] Starting Tor..."
        chmod 700 "$TOR_HS_DIR"
        chown -R root:root "$TOR_HS_DIR" "$TOR_DATA_DIR"
        tor -f /etc/tor/torrc --RunAsDaemon 0 --User root &
        TOR_PID=$!

        # Wait for bootstrap
        echo "[entrypoint] Waiting for Tor to bootstrap..."
        for i in $(seq 1 120); do
            if [ -f "$TOR_HS_DIR/hostname" ]; then
                ONION=$(cat "$TOR_HS_DIR/hostname")
                echo "[entrypoint] Hidden service is live at: $ONION"
                mkdir -p "$PERSISTENT_HS_DIR"
                cp "$TOR_HS_DIR/hostname" "$PERSISTENT_HS_DIR/" 2>/dev/null || true
                cp "$TOR_HS_DIR/hs_ed25519_secret_key" "$PERSISTENT_HS_DIR/" 2>/dev/null || true
                cp "$TOR_HS_DIR/hs_ed25519_public_key" "$PERSISTENT_HS_DIR/" 2>/dev/null || true
                cp "$TOR_HS_DIR/hostname" "$APP_DATA/hostname"
                echo "{\"status\": \"running\", \"onion\": \"$ONION\"}" > "$STATE_FILE"
                break
            fi
            sleep 2
        done

        wait -n $TOR_PID $SERVER_PID
        EXIT_CODE=$?
        echo "[entrypoint] A process exited with code $EXIT_CODE, shutting down..."
        kill $TOR_PID $SERVER_PID 2>/dev/null || true
        wait
        exit $EXIT_CODE
    fi
fi

# Standard startup (no vanity generation needed)
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
