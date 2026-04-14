FROM debian:bookworm-slim AS builder

# Build mkp224o for vanity onion address generation
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git gcc make autoconf libsodium-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/cathugger/mkp224o.git /build/mkp224o && \
    cd /build/mkp224o && \
    ./autogen.sh && \
    ./configure --enable-donna && \
    make

FROM debian:bookworm-slim

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        tor \
        python3 \
        python3-pip \
        openssl \
        libsodium23 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies (markdown rendering)
RUN pip3 install --no-cache-dir --break-system-packages markdown==3.7

# Copy mkp224o from builder
COPY --from=builder /build/mkp224o/mkp224o /usr/local/bin/mkp224o

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
