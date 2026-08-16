FROM python:3.12-slim
RUN pip install --no-cache-dir rns umsgpack pyyaml
WORKDIR /app
COPY rns_stall /app/rns_stall
# meshapi is vendored at deploy time (not yet on PyPI):
#   COPY vendor/meshapi /app/meshapi
ENV STALL_CATALOG=/data/catalog.yaml \
    STALL_DB=/data/stall.db \
    STALL_IDENTITY=/data/identity \
    STALL_PAGES_OUT=/pages \
    HEALTHZ_PORT=8216
CMD ["python3", "-m", "rns_stall.server"]
