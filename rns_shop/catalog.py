"""Catalog backends: v1 ships YAML (the generic, zero-config default). The
read interface is a small base class (CatalogSource) so ANY data source
(another commerce platform's API, a hand-rolled dict store, literally any
service reachable over the network that can answer "what do you sell") can
back a shop without touching server.py. Same idiom as providers.py for
payment rails: a connector is "one class, registered under a URL scheme";
shopd finds it via open_catalog(). See medusa.py and squarespace.py for two
working non-YAML examples.

catalog.yaml shape (the default backend):
  shop:
    name: My Shop
    vendor: My Shop
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
      ships_to: [US, CA]          # ISO-3166 alpha-2, or [worldwide]. Item
                                   # override; else shop.ships_to; else
                                   # DENIED EVERYWHERE (see CatalogSource.
                                   # ships_ok, safe by default, never a
                                   # silent worldwide fallback)
      weight_kg: 0.3               # optional, for shop.shipping's weight_tiers
                                   # method (see shipping.py)

External source: SHOP_CATALOG=<scheme>://<...> selects a registered
CatalogSource subclass instead of the YAML file. See SCHEME_MODULES below.

Shipping COST (separate from ships_to, which only gates whether an order is
accepted at all): see shipping.py, shop.shipping in this same YAML block.
"""
import importlib
import os

import yaml

REQUIRED = ("sku", "title", "price")
AVAILABILITY = ("in_stock", "made_to_order", "out_of_stock", "digital")
KINDS = ("physical", "digital", "service")


class CatalogSource:
    """Shared read interface every catalog backend implements. A connector's
    entire job is to populate self.shop (dict) and self.items (dict[sku] ->
    item dict with at least sku/title/price/availability/kind/tags/ships_to,
    optionally description/image) inside load(), and to say via changed()
    when a re-poll + re-render is due. summary()/list()/get()/ships_ok() are
    inherited for free. See medusa.py / squarespace.py for the ~50-line
    connectors this makes possible.

    `files_dir` (optional): the shop's local /file/ directory (see
    server.py's --files). A connector whose upstream serves images from an
    external URL (anything not already a local filename under this dir:
    NomadNet clients are mesh-native and can't fetch a clearnet CDN URL) is
    expected to download + cache them here and set item["image"] to the
    resulting local filename, exactly like a YAML catalog's merchant-placed
    image file. Backends with no images (or that already point at local
    files) can ignore it."""

    REFRESH = 300  # seconds; subclasses polling a remote API should honor this

    def __init__(self, files_dir=None):
        self.shop = {}
        self.items = {}
        self.files_dir = files_dir

    def load(self):
        raise NotImplementedError

    def changed(self):
        raise NotImplementedError

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
        # SAFE BY DEFAULT: no declared destinations means "not configured",
        # never "everywhere". See ships_ok's docstring for why this matters
        # more than it looks for a generic, plug-in-any-source interface.
        full["ships_to"] = it.get("ships_to") or []
        if it.get("image"):
            full["image"] = it["image"]
        return full

    @staticmethod
    def ships_ok(item, country):
        """SAFE BY DEFAULT: an item with no declared ships_to is treated as
        "the vendor hasn't said where they'll ship this", not "anywhere".
        Physical goods checkout REFUSES rather than silently allowing a
        destination nobody configured. This interface backs any catalog
        source a vendor plugs in (Squarespace today, anything else
        tomorrow); a connector whose upstream doesn't expose real shipping-
        destination data (Squarespace's API doesn't, see squarespace.py)
        must never let that turn into "ships worldwide" by default. A
        vendor opts into WORLDWIDE explicitly, the same way they'd list any
        other destination. It's never assumed."""
        st = [str(c).strip().upper() for c in (item.get("ships_to") or [])]
        if not st:
            return False
        if "WORLDWIDE" in st:
            return True
        return str(country or "").strip().upper() in st


class Catalog(CatalogSource):
    """YAML file backend (the default, see module docstring for shape)."""

    def __init__(self, path, files_dir=None):
        super().__init__(files_dir=files_dir)
        self.path = path
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
            # ships_to: item override else shop default else NOT configured
            # (empty, see CatalogSource.ships_ok: that means "denied
            # everywhere" until the merchant says otherwise, not worldwide).
            # Uppercase ISO-3166 alpha-2 codes, or ["worldwide"] as an
            # explicit opt-in.
            st = it.get("ships_to") or shop.get("ships_to") or []
            it["ships_to"] = [str(c).strip().upper() for c in st]
            it["weight_kg"] = it.get("weight_kg")  # optional, for shipping.py's weight_tiers
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


# ---- Registry: URL scheme -> connector module/class -----------------------
# Lazy-imported (not eagerly imported at module load) so a connector's extra
# dependencies (or just its extra startup work, e.g. a first API poll)
# only happen when that scheme is actually selected. Adding a new source is
# "add one line here + write the class". No server.py changes.
SCHEME_MODULES = {
    "medusa":      ("rns_shop.medusa", "MedusaCatalog"),
    "squarespace": ("rns_shop.squarespace", "SquarespaceCatalog"),
}


def open_catalog(spec, files_dir=None):
    """SHOP_CATALOG dispatcher. A bare path/filename (no '://') always opens
    the YAML backend, the zero-config default every existing deployment
    already relies on. '<scheme>://...' opens whatever CatalogSource
    subclass is registered for that scheme. An unrecognized scheme raises
    immediately naming what IS registered, rather than silently falling back
    to an empty/wrong catalog on a typo'd SHOP_CATALOG."""
    spec = str(spec)
    if "://" not in spec:
        return Catalog(spec, files_dir=files_dir)
    scheme = spec.split("://", 1)[0]
    target = SCHEME_MODULES.get(scheme)
    if target is None:
        raise ValueError(
            f"unknown SHOP_CATALOG scheme {scheme!r} -- registered: "
            f"{sorted(SCHEME_MODULES)} (or a bare path for the YAML backend)")
    mod_name, cls_name = target
    cls = getattr(importlib.import_module(mod_name), cls_name)
    return cls(spec, files_dir=files_dir)
