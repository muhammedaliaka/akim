FROM python:3.12-slim

# İsteğe bağlı eklenti: yerel ve ücretsiz Laya için EXTRAS=laya ile derle (CPU PyTorch ~700 MB ekler).
# docker compose'da: build.args.EXTRAS
ARG EXTRAS=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AKIM_DATA_DIR=/data \
    HF_HOME=/data/hf

WORKDIR /app
COPY pyproject.toml README.md ./
COPY akim ./akim
RUN if echo "$EXTRAS" | grep -q laya; then \
        pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu; \
    fi \
    && if [ -n "$EXTRAS" ]; then pip install --no-cache-dir ".[${EXTRAS}]"; else pip install --no-cache-dir .; fi \
    && useradd --create-home --uid 1000 akim && mkdir -p /data && chown akim /data

USER akim
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["akim"]
CMD ["run"]
