"""Medusa v2 catalog connector (M5): read products from a MedusaJS /store API
and present them through the same CatalogSource interface as the YAML
catalog (see catalog.py), so shopd can run against a real commerce backend
without a separate export step.

Usage: SHOP_CATALOG=medusa://<base_url>?key=<publishable_key>[&currency=usd]
(the shop{} block then comes from SHOP_SHOP_* env or defaults).

Read-only: shopd never writes to Medusa; orders live in shopd's DB (pushing
them into Medusa/ERPNext is the merchant-side sync, a later connector).

KNOWN GAP (unchanged from before the CatalogSource refactor): no image
support. Medusa's publishable-key /store API doesn't return a usable
image URL cheaply here, so items render with no photo. See squarespace.py
for what a connector with image caching looks like."""
import json
import os
import time
import urllib.parse
import urllib.request

from .catalog import CatalogSource


class MedusaCatalog(CatalogSource):
    REFRESH = 300  # seconds between catalog refreshes

    def __init__(self, url, shop=None, files_dir=None):
        super().__init__(files_dir=files_dir)
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        self.base = f"http://{u.netloc}"
        self.key = (q.get("key") or [None])[0]
        self.currency = (q.get("currency") or ["usd"])[0]
        self.shop = shop or {"name": "shop (medusa)", "currency":
                             self.currency.upper()}
        # No ships_to source is fetched from Medusa's /store API here (M5,
        # untested against a live store). Same stance as squarespace.py:
        # catalog.CatalogSource.ships_ok() is safe-by-default (undeclared =
        # denied everywhere), so this stays unset unless MEDUSA_SHIPS_TO is
        # explicitly configured, rather than silently defaulting worldwide.
        self.default_ships_to = [c.strip().upper() for c in
                                 os.environ.get("MEDUSA_SHIPS_TO", "").split(",") if c.strip()]
        self._loaded = 0.0
        self.load()

    def _get(self, path):
        req = urllib.request.Request(self.base + path)
        if self.key:
            req.add_header("x-publishable-api-key", self.key)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def load(self):
        data = self._get("/store/products?limit=200&fields=*variants.calculated_price")
        items = {}
        for p in data.get("products", []):
            var = (p.get("variants") or [{}])[0]
            price_obj = (var.get("calculated_price") or {})
            amount = price_obj.get("calculated_amount")
            if amount is None:
                continue
            sku = var.get("sku") or p.get("handle") or p["id"]
            items[sku] = {
                "sku": sku,
                "title": p.get("title", sku),
                "description": (p.get("description") or "")[:500],
                "price": float(amount),
                "availability": "in_stock",
                "kind": "physical",
                "tags": [c.get("value", "") for c in (p.get("categories") or [])
                         if c.get("value")],
                "ships_to": self.default_ships_to,
            }
        self.items = items
        self._loaded = time.time()
        return self

    def changed(self):
        return time.time() - self._loaded > self.REFRESH
