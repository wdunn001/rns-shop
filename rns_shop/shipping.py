"""Shipping cost calculation -- the SAME generic-provider idiom as
providers.py (payment rails) and catalog.py (catalog sources): one
interface, any method behind it. A shipping provider turns an order's
(physical) items into a fee, added to the item subtotal BEFORE payment
instructions are built -- so every rail (invoice/link/xmr/manual) quotes and
collects the true total, not just goods.

Shop config (catalog.yaml):
  shop:
    shipping:
      method: free              # the default when shop.shipping is omitted
    # OR
      method: flat
      amount: 5.00               # one fee per order with >=1 physical item
    # OR
      method: weight_tiers
      tiers:                     # cumulative order weight (kg) -> fee
        - {max_kg: 0.5, fee: 4.00}
        - {max_kg: 2.0, fee: 8.00}
        - {max_kg: null, fee: 15.00}   # null = no upper bound (heaviest tier)
      default_fee: 8.00          # used if NO item in the order carries
                                  # weight data at all (falls back rather
                                  # than silently charging 0) -- omit to
                                  # fall back to the cheapest tier instead

Per-item weight: catalog.yaml items may set `weight_kg: <number>`; the
Squarespace connector already supplies it (shippingMeasurements -> kg) for
every physical variant, so weight_tiers works out of the box against a
Squarespace-sourced catalog with zero extra config.

Digital/service items never carry a shipping fee, and an order with no
physical items always quotes None (no shipping line at all) regardless of
the configured method.

NOT implemented: live carrier rate-shopping (USPS/UPS/FedEx real quotes).
That needs its own third-party API credential (e.g. EasyPost/Shippo, which
themselves aggregate the carriers) and would slot in here as one more class
-- the interface is built generic enough for that to be a follow-on, not a
rewrite, exactly like adding a new payment rail or catalog source.
"""


def _physical_summary(catalog, items):
    """(has_physical, total_weight_kg, any_weight_known) over an order's
    PHYSICAL items only -- digital/service items never factor into a
    shipping fee. total_weight_kg is 0.0 and any_weight_known is False when
    every physical item lacks weight data (the caller decides how to
    degrade, e.g. weight_tiers' default_fee)."""
    has_physical = False
    total_kg = 0.0
    any_known = False
    for e in items:
        it = catalog.items.get(e["sku"]) or {}
        if it.get("kind", "physical") != "physical":
            continue
        has_physical = True
        w = it.get("weight_kg")
        if w:
            total_kg += float(w) * e["qty"]
            any_known = True
    return has_physical, total_kg, any_known


class ShippingProvider:
    """Interface. method: config id, matched against shop.shipping.method."""
    method = "base"

    def __init__(self, cfg, ctx):
        self.cfg = cfg
        self.ctx = ctx  # {catalog}

    def quote(self, items, address=None):
        """items: [{sku, qty}] (already validated by the caller).
        -> float fee, or None (not applicable -- e.g. no physical items)."""
        raise NotImplementedError


class FreeProvider(ShippingProvider):
    method = "free"

    def quote(self, items, address=None):
        has_physical, _, _ = _physical_summary(self.ctx["catalog"], items)
        return 0.0 if has_physical else None


class FlatProvider(ShippingProvider):
    """One fee, any order with at least one physical item."""
    method = "flat"

    def quote(self, items, address=None):
        has_physical, _, _ = _physical_summary(self.ctx["catalog"], items)
        if not has_physical:
            return None
        return float(self.cfg.get("amount", 0))


class WeightTierProvider(ShippingProvider):
    """Cumulative order weight -> fee, from an ascending list of
    {max_kg, fee} tiers (max_kg: null/omitted = the catch-all heaviest
    tier). See module docstring for config shape + the default_fee
    no-weight-data fallback."""
    method = "weight_tiers"

    def quote(self, items, address=None):
        has_physical, total_kg, any_known = _physical_summary(self.ctx["catalog"], items)
        if not has_physical:
            return None
        tiers = self.cfg.get("tiers") or []
        if not tiers:
            return float(self.cfg.get("default_fee", 0))
        if not any_known:
            # No item in the order carries weight data at all -- charging
            # 0 would silently undercharge shipping on every order from a
            # catalog that hasn't set weight_kg, which is worse than an
            # honest flat fallback the merchant explicitly configured.
            if "default_fee" in self.cfg:
                return float(self.cfg["default_fee"])
            total_kg = 0.0  # falls through to the cheapest (first) tier below
        ordered = sorted(tiers, key=lambda t: (t.get("max_kg") is None, t.get("max_kg", 0)))
        for t in ordered:
            max_kg = t.get("max_kg")
            if max_kg is None or total_kg <= float(max_kg):
                return float(t.get("fee", 0))
        return float(ordered[-1].get("fee", 0))


BUILTINS = {p.method: p for p in (FreeProvider, FlatProvider, WeightTierProvider)}


def quote(catalog, items, address=None):
    """The one entry point server.py calls. Builds the configured provider
    fresh each call (stateless -- shop.shipping can change on a catalog
    reload with no cache to invalidate) and NEVER raises: a shipping-config
    bug degrades to "no shipping fee charged" rather than breaking checkout,
    same failure posture as a payment rail returning no instruction."""
    cfg = (catalog.shop.get("shipping") or {"method": "free"})
    cls = BUILTINS.get(cfg.get("method", "free"), FreeProvider)
    try:
        fee = cls(cfg, {"catalog": catalog}).quote(items, address)
    except Exception:  # noqa: BLE001
        fee = None
    return round(fee, 2) if fee else (0.0 if fee == 0.0 else None)
