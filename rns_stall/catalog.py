"""Catalog backends. v1 ships YAML (the generic default); the interface is one
function so Medusa/other connectors can slot in later (M5).

catalog.yaml shape:
  shop:
    name: My Stall
    vendor: My Stall
    currency: USD
    invoice_note: "We'll send an LXMF invoice; pay by arrangement."
  items:
    - sku: DEMO-TEE
      title: Mesh Tee
      description: Soft cotton, node hash on the back.
      price: 24.00
      availability: in_stock      # in_stock|made_to_order|out_of_stock|digital
      tags: [apparel]
      kind: physical              # physical|digital|service
"""
import os

import yaml

REQUIRED = ("sku", "title", "price")
AVAILABILITY = ("in_stock", "made_to_order", "out_of_stock", "digital")
KINDS = ("physical", "digital", "service")


class Catalog:
    def __init__(self, path):
        self.path = path
        self.shop = {}
        self.items = {}
        self.mtime = 0.0
        self.load()

    def load(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        shop = doc.get("shop") or {}
        items = {}
        for it in doc.get("items") or []:
            for k in REQUIRED:
                if not it.get(k):
                    raise ValueError(f"catalog item missing {k}: {it!r}")
            sku = str(it["sku"])
            it = dict(it)
            it["sku"] = sku
            it["price"] = float(it["price"])
            it["availability"] = it.get("availability", "in_stock")
            if it["availability"] not in AVAILABILITY:
                raise ValueError(f"{sku}: bad availability {it['availability']!r}")
            it["kind"] = it.get("kind", "physical")
            if it["kind"] not in KINDS:
                raise ValueError(f"{sku}: bad kind {it['kind']!r}")
            it["tags"] = list(it.get("tags") or [])
            items[sku] = it
        self.shop = shop
        self.items = items
        self.mtime = os.path.getmtime(self.path)
        return self

    def changed(self):
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return False

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
