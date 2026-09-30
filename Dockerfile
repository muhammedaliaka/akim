FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AKIM_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY akim ./akim
RUN pip install --no-cache-dir ".[llm]" && useradd --create-home --uid 1000 akim && mkdir -p /data && chown akim /data

USER akim
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["akim"]
CMD ["run"]
