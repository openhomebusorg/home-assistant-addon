ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11-alpine3.18
FROM ${BUILD_FROM}

# ── System dependencies ───────────────────────────────────────────────────
RUN apk add --no-cache \
    python3 \
    py3-pip \
    && rm -rf /var/cache/apk/*

# ── Python dependencies ───────────────────────────────────────────────────
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

ENV PYTHONPATH=/opt/ohb

# ── Copy application ──────────────────────────────────────────────────────
COPY ohb_dashboard/ /opt/ohb/ohb_dashboard/
COPY dashboard/ /opt/ohb/dashboard/

# ── S6 service ────────────────────────────────────────────────────────────
COPY rootfs/ /
RUN chmod +x /etc/services.d/ohb-dashboard/run \
    /etc/services.d/ohb-dashboard/finish

# ── Healthcheck ───────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD wget -q -O /dev/null http://localhost:6052/api/info || exit 1

# ── Labels ────────────────────────────────────────────────────────────────
LABEL \
    io.hass.name="OpenHomeBus" \
    io.hass.description="Management dashboard for the OpenHomeBus system" \
    io.hass.arch="amd64|aarch64|armv7" \
    io.hass.type="addon" \
    io.hass.version="0.4.0"

WORKDIR /opt/ohb
