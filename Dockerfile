FROM debian:bookworm-slim

# Install Tor and Python
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tor \
        python3 \
        openssl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy application files
WORKDIR /app
COPY server.py .
COPY entrypoint.sh .
COPY torrc /etc/tor/torrc

RUN chmod +x /app/entrypoint.sh

# Health check port (for OpenHost) and onion service port (for Tor)
EXPOSE 8080 3000

ENTRYPOINT ["/app/entrypoint.sh"]
