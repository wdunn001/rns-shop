"""Medusa v2 catalog connector (M5): read products from a MedusaJS /store API
and present them through the same interface as the YAML Catalog, so shopd can
run against a real commerce backend without a separate export step.

Usage: SHOP_CATALOG=medusa://<base_url>?key=<publishable_key>[&currency=usd]
(the shop{} block then comes from SHOP_SHOP_* env or defaults).

Read-only: shopd never writes to Medusa; orders live in shopd's DB (pushing
them into Medusa/ERPNext is the merchant-side sync, a later connector)."""
import json
import time
import urllib.parse
import urllib.request


class MedusaCatalog:
    REFRESH = 300  # seconds between catalog refreshes

    def __init__(self, url, shop=None):
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        self.base = f"http://{u.netloc}"
        self.key = (q.get("key") or [None])[0]
        self.currency = (q.get("currency") or ["usd"])[0]
        self.shop = shop or {"name": "shop (medusa)", "currency":
                             self.currency.upper()}
        self.items = {}
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
            }
        self.items = items
        self._loaded = time.time()
        return self

    def changed(self):
        return time.time() - self._loaded > self.REFRESH

    # same read interface as catalog.Catalog
    def summary(self, it):
        return {"sku": it["sku"], "title": it["title"], "price": it["price"],
                "currency": self.shop.get("currency", "USD"),
                "availability": it["availability"], "kind": it["kind"],
                "tags": it["tags"]}

    def list(self, tag=None):
        out = [self.summary(i) for i in self.items.values()
               if tag is None or tag in i["tags"]]
        return sorted(out, key=lambda i: i["sku"])

    def get(self, sku):
        it = self.items.get(str(sku))
        if not it:
            return None
        full = dict(self.summary(it))
        full["description"] = it.get("description", "")
        return full
