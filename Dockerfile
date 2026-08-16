FROM python:3.12-slim
RUN pip install --no-cache-dir rns lxmf umsgpack pyyaml
WORKDIR /app
COPY rns_shop /app/rns_shop
# meshapi is vendored at deploy time (not yet on PyPI):
#   COPY vendor/meshapi /app/meshapi
ENV SHOP_CATALOG=/data/catalog.yaml \
    SHOP_DB=/data/shop.db \
    SHOP_IDENTITY=/data/identity \
    SHOP_PAGES_OUT=/pages \
    SHOP_FILES=/data/files \
    HEALTHZ_PORT=8216
CMD ["python3", "-m", "rns_shop.server"]
