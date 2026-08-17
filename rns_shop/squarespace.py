"""Squarespace Commerce catalog connector: read products (+ variants, images,
stock) from a real Squarespace store's Commerce API and present them through
the same CatalogSource interface every other backend uses (YAML, Medusa) --
so shopd can sell a Squarespace-sourced catalog over the mesh with zero
server.py changes. Read-only, same stance as Medusa: shopd never writes back
to Squarespace; orders live in shopd's own DB, not Squarespace's.

Usage: SHOP_CATALOG=squarespace://commerce (the URL is just a scheme
selector, no secrets in it -- the query-string-embedded key pattern
medusa:// uses is fine for a publishable/read key, but Squarespace's
Commerce API key is a full read/write-capable secret, so it comes from env
instead, never from a config value that ends up in a compose file or a
process listing).

Required env:
  SQUARESPACE_API_KEY     Settings > Advanced > API Keys in the Squarespace
                           admin; needs at least Products + Inventory read.
  SQUARESPACE_SHIPS_TO    comma-separated ISO-3166 alpha-2 codes, or the
                           literal value WORLDWIDE -- NO DEFAULT, see HONEST
                           LIMITATION below. Physical-item checkout is
                           refused for every destination until this is set
                           (catalog.CatalogSource.ships_ok is safe-by-
                           default: undeclared means denied, never allowed).
Optional env:
  SQUARESPACE_CURRENCY    default "usd"
  SQUARESPACE_SHOP_NAME   default "shop (squarespace)"
  SQUARESPACE_API_BASE    default https://api.squarespace.com
  SQUARESPACE_API_VERSION default "1.0"

Squarespace Commerce API notes (developers.squarespace.com/commerce-apis,
checked 2026-08-17 -- re-verify against live docs before relying on exact
field names, this is one connector's read of them, not a mirror of the spec):
  - Base: https://api.squarespace.com/{api-version}/commerce/...
  - Auth: `Authorization: Bearer <API key>`
  - Products: GET /commerce/products, cursor-paginated via
    `pagination.hasNextPage` / `pagination.nextPageCursor`. Each product has
    type (PHYSICAL/SERVICE/GIFT_CARD/DIGITAL), pricing, images[], and
    variants[] (sku, pricing{basePrice,salePrice,onSale}, stock{quantity,
    unlimited}, shippingMeasurements{weight,dimensions}).
  - Inventory (stock) is ALSO readable live per-variant via
    GET /commerce/inventory/{ids} -- deliberately NOT used here: variant.
    stock is already embedded in the product list response, so a separate
    poll would just be a second round trip for numbers this connector
    already has.
  - HONEST LIMITATION: the public Commerce API does not expose a per-product
    or store-wide "ships to these countries" list as of this writing --
    shipping ZONES are dashboard-only configuration with no read endpoint
    found. ships_to therefore comes from SQUARESPACE_SHIPS_TO (shop-level,
    like catalog.yaml's shop.ships_to default) rather than being read live;
    if that's wrong for a given store, fix the env var, not this connector.
    Package weight/dimensions (shippingMeasurements) ARE available per
    variant and are captured as shipping_weight_kg/shipping_dims_cm on the
    item dict for a future real-rate-shopping provider, even though nothing
    reads them yet -- documented rather than silently dropped.
"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

from .catalog import CatalogSource

_TYPE_TO_KIND = {"PHYSICAL": "physical", "SERVICE": "service",
                 "DIGITAL": "digital", "GIFT_CARD": "digital"}


def _kg(weight):
    """shippingMeasurements.weight -> kg float, or None. Squarespace's unit
    enum has been seen as KILOGRAM/POUND; anything else is left unconverted
    (better a slightly-off number on an unanticipated unit than a silent
    wrong conversion -- flagged via the 'unit' passthrough if ever needed)."""
    if not weight or weight.get("value") is None:
        return None
    v = float(weight["value"])
    unit = (weight.get("unit") or "KILOGRAM").upper()
    return round(v * 0.45359237, 4) if unit == "POUND" else round(v, 4)


class SquarespaceCatalog(CatalogSource):
    REFRESH = 300  # seconds between polls -- same cadence as MedusaCatalog

    def __init__(self, url, files_dir=None):
        super().__init__(files_dir=files_dir)
        self.key = os.environ.get("SQUARESPACE_API_KEY")
        if not self.key:
            raise RuntimeError(
                "SHOP_CATALOG=squarespace://... needs SQUARESPACE_API_KEY set "
                "(Settings > Advanced > API Keys in the Squarespace admin; "
                "Products + Inventory read scopes)")
        self.base = os.environ.get("SQUARESPACE_API_BASE", "https://api.squarespace.com").rstrip("/")
        self.api_version = os.environ.get("SQUARESPACE_API_VERSION", "1.0")
        currency = os.environ.get("SQUARESPACE_CURRENCY", "usd").upper()
        # REQUIRED, no silent default. Squarespace's public Commerce API does
        # not expose a real ships-to-country list (see module docstring's
        # HONEST LIMITATION), so there is no ground truth to fall back to --
        # only the merchant knows where they're actually willing to ship
        # physical goods, and catalog.CatalogSource.ships_ok() is safe-by-
        # default (undeclared = denied everywhere) specifically so a
        # connector like this one can never turn "nobody set this yet" into
        # "ships worldwide" by accident. Explicitly set to WORLDWIDE if
        # that's genuinely true for this shop.
        raw_ships_to = os.environ.get("SQUARESPACE_SHIPS_TO")
        if not raw_ships_to:
            raise RuntimeError(
                "SHOP_CATALOG=squarespace://... needs SQUARESPACE_SHIPS_TO set -- "
                "comma-separated ISO-3166 alpha-2 country codes this shop will "
                "actually ship physical goods to (or the literal value WORLDWIDE "
                "if genuinely unrestricted). No default: Squarespace's API doesn't "
                "expose real shipping-destination data, so guessing here would "
                "mean silently offering to ship somewhere the merchant never agreed to.")
        self.default_ships_to = [c.strip().upper() for c in raw_ships_to.split(",") if c.strip()]
        name = os.environ.get("SQUARESPACE_SHOP_NAME", "shop (squarespace)")
        self.shop = {"name": name, "vendor": name, "currency": currency}
        self._loaded = 0.0
        self.load()

    def _get(self, path, params=None):
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(
            f"{self.base}/{self.api_version}{path}{qs}",
            headers={"Authorization": f"Bearer {self.key}",
                     "User-Agent": "rns-shop-squarespace-connector/0.1",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def _all_products(self):
        """Walk every page (cursor pagination) -- see module docstring."""
        cursor = None
        while True:
            data = self._get("/commerce/products", {"cursor": cursor} if cursor else None)
            for p in data.get("products", []):
                yield p
            page = data.get("pagination") or {}
            cursor = page.get("nextPageCursor")
            if not page.get("hasNextPage") or not cursor:
                return

    def _cache_image(self, url):
        """Download a remote product image into the shop's /file/ directory
        (NomadNet clients can't fetch an external clearnet CDN URL -- see
        catalog.CatalogSource's files_dir docstring) and return the local
        filename render.py's _image_bits() expects, or None on any failure
        (a missing photo is never worth breaking catalog sync over).
        Content-addressed by URL hash so a re-poll (every REFRESH seconds)
        skips the download entirely once an image is already cached --
        Squarespace's CDN URLs embed a stable asset id, so the same product
        photo always hashes to the same local filename."""
        if not url or not self.files_dir:
            return None
        try:
            ext = os.path.splitext(urllib.parse.urlparse(url).path)[1][:5] or ".jpg"
            name = "sq-" + hashlib.sha256(url.encode()).hexdigest()[:20] + ext
            dest = os.path.join(self.files_dir, name)
            if os.path.isfile(dest) and os.path.getsize(dest) > 0:
                return name
            os.makedirs(self.files_dir, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": "rns-shop-squarespace-connector/0.1"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            tmp = dest + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, dest)
            return name
        except Exception:  # noqa: BLE001 -- a photo is a nice-to-have, never a blocker
            return None

    def load(self):
        items = {}
        for p in self._all_products():
            if not p.get("isVisible", True):
                continue
            kind = _TYPE_TO_KIND.get(p.get("type"), "physical")
            variants = p.get("variants") or []
            if not variants:
                # Defensive fallback for a single-SKU product shape with no
                # variants array at all -- mirrors medusa.py's `or [{}]`.
                variants = [{"sku": p.get("id"), "pricing": p.get("pricing"),
                            "stock": {"unlimited": True}}]
            imgs = p.get("images") or []
            image_name = None
            if imgs and imgs[0].get("url"):
                image_name = self._cache_image(imgs[0]["url"])
            for v in variants:
                sku = v.get("sku") or f"{p.get('id', 'sq')}-{v.get('id', '0')}"
                pricing = v.get("pricing") or {}
                price_obj = (pricing.get("salePrice") if pricing.get("onSale")
                            else pricing.get("basePrice"))
                if not price_obj or price_obj.get("value") is None:
                    continue   # unpriced variant -- can't sell it, skip rather than crash
                stock = v.get("stock") or {}
                if kind == "digital":
                    availability = "digital"
                elif stock.get("unlimited") or (stock.get("quantity") or 0) > 0:
                    availability = "in_stock"
                else:
                    availability = "out_of_stock"
                sm = v.get("shippingMeasurements") or {}
                items[sku] = {
                    "sku": sku, "title": p.get("name", sku),
                    "description": (p.get("description") or "")[:2000],
                    "price": float(price_obj["value"]),
                    "availability": availability, "kind": kind,
                    "tags": list(p.get("tags") or []),
                    "ships_to": self.default_ships_to,
                    "image": image_name,
                    "shipping_weight_kg": _kg(sm.get("weight")),
                    "shipping_dims_cm": sm.get("dimensions"),
                    "squarespace_product_id": p.get("id"),
                    "squarespace_url": p.get("url"),
                }
        self.items = items
        self._loaded = time.time()
        return self

    def changed(self):
        return time.time() - self._loaded > self.REFRESH
