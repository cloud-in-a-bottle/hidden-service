FROM debian:bookworm-slim

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tor \
        python3 \
        python3-pip \
        openssl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (markdown rendering)
RUN pip3 install --no-cache-dir --break-system-packages markdown==3.7

# Copy application files
WORKDIR /app
COPY server.py .
COPY cms.py .
COPY entrypoint.sh .
COPY torrc /etc/tor/torrc
COPY templates/ templates/
COPY static/ static/

RUN chmod +x /app/entrypoint.sh

# Health check / admin port (for OpenHost) and onion service port (for Tor)
EXPOSE 8080 3000

ENTRYPOINT ["/app/entrypoint.sh"]
