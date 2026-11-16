# syntax=docker/dockerfile:1
# Priva Bridge: één container (FastAPI + SQLite in /data), te installeren via het Custom App-formulier
# van TrueNAS SCALE. Draait als niet-root (UID/GID 568, de standaard 'apps'-gebruiker van TrueNAS).
FROM python:3.12.14-slim-bookworm AS wheels
WORKDIR /build
COPY requirements.txt pyproject.toml README.md ./
COPY bridge ./bridge
# Optioneel build-secret 'build_ca' voor een TLS-inspecterende proxy; komt nooit in de image.
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build-ca.pem \
    if [ -s /run/secrets/build-ca.pem ]; then export PIP_CERT=/run/secrets/build-ca.pem; fi; \
    pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt \
    && pip wheel --no-cache-dir --no-deps --wheel-dir=/wheels .

FROM python:3.12.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    BRIDGE_CATALOG_DIR=/app/catalog \
    TZ=Europe/Amsterdam
WORKDIR /app
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-index /wheels/*.whl \
    && groupadd --system --gid 568 apps \
    && useradd --system --uid 568 --gid 568 --home-dir /app --shell /usr/sbin/nologin apps \
    && mkdir -p /data && chown 568:568 /data && chmod 0770 /data
COPY --chown=root:root catalog ./catalog
USER 568:568
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
CMD ["bridge", "serve", "--host", "0.0.0.0", "--port", "8000"]
