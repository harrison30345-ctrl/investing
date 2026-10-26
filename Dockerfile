# QuantEdge: one image, three roles (dashboard, worker, one-off CLI), chosen by the compose command.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QE_HOME=/data

WORKDIR /app
COPY requirements.txt requirements-server.txt ./
RUN pip install -r requirements.txt -r requirements-server.txt

COPY quantedge ./quantedge

# Run as an unprivileged user; /data holds local state (SQLite fallback, caches).
RUN useradd --create-home --uid 10001 quantedge && mkdir -p /data && chown quantedge /data
USER quantedge

EXPOSE 8501
CMD ["python", "-m", "quantedge.cli", "dashboard", "--port", "8501"]
