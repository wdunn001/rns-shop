"""Payment providers — one generic interface, any rail behind it.

A provider turns an order into a PaymentInstruction (what the buyer must do)
and can check/settle it. Instructions travel to the buyer BOTH in the checkout
page response and in the LXMF invoice — so a link rail works for any
traditional processor (Stripe/PayPal/Square/... just template the URL), and
crypto rails complete in-app when the watcher sees the transfer.

Shop config (catalog.yaml):
  shop:
    payments:
      - method: invoice                      # always sensible; manual settle
      - method: link
        label: "Card / PayPal (web link)"
        template: "https://buy.stripe.com/XYZ?client_reference_id={order_id}"
        # or per_sku: {SKU: url, ...}
      - method: xmr
        label: "Monero"
        rate: 165.0                          # units of shop currency per XMR
"""
import RNS


class PaymentInstruction:
    def __init__(self, method, text, url=None, address=None, amount=None):
        self.method = method
        self.text = text          # human line for pages + LXMF
        self.url = url            # link rails
        self.address = address    # crypto rails
        self.amount = amount

    def as_dict(self):
        out = {"method": self.method, "text": self.text}
        for k in ("url", "address", "amount"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


class Provider:
    """Interface. method: config id. label: shown to buyers."""
    method = "base"

    def __init__(self, cfg, ctx):
        self.cfg = cfg
        self.ctx = ctx  # {store, shop, xmr_watcher?}
        self.label = cfg.get("label", self.method)

    def instruction(self, order):
        """-> PaymentInstruction. Called at order time (and re-shown later)."""
        raise NotImplementedError

    def start(self):
        """Optional: begin watching for settlement (crypto rails)."""


class InvoiceProvider(Provider):
    method = "invoice"

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.label = cfg.get("label", "Invoice (settle by arrangement)")

    def instruction(self, order):
        note = self.ctx["shop"].get(
            "invoice_note", "You will receive an LXMF invoice; the merchant "
            "confirms payment manually.")
        return PaymentInstruction("invoice", note)


class LinkProvider(Provider):
    """Any web-checkout processor: the buyer opens the URL whenever they have
    internet; your processor's webhook (webhook_bridge) flips the order paid."""
    method = "link"

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.label = cfg.get("label", "Pay online (card, etc.)")

    def instruction(self, order):
        url = None
        if self.cfg.get("template"):
            url = self.cfg["template"].format(order_id=order["order_id"])
        else:
            per = self.cfg.get("per_sku") or {}
            items = order["items"]
            if len(items) == 1 and items[0]["qty"] == 1:
                url = per.get(items[0]["sku"])
        if not url:
            return None
        return PaymentInstruction(
            "link", f"Pay online (any device with internet): {url}", url=url)


class XmrProvider(Provider):
    """Monero, watch-only: per-order subaddress; the watcher completes the
    transaction when the transfer confirms. No spend keys, ever."""
    method = "xmr"

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.label = cfg.get("label", "Monero (XMR)")

    def instruction(self, order):
        w = self.ctx.get("xmr_watcher")
        rate = float(self.cfg.get("rate", 0))
        if w is None or not rate:
            return None
        store = self.ctx["store"]
        existing = store.order_admin_get(order["order_id"]) or {}
        if existing.get("xmr_address"):
            addr, amount = existing["xmr_address"], existing["xmr_amount"]
        else:
            addr, idx = w.assign_subaddress(order["order_id"])
            amount = round(order["total"] / rate, 8)
            store.order_set_xmr(order["order_id"], addr, idx, amount)
        return PaymentInstruction(
            "xmr", f"Send exactly {amount} XMR to {addr} — the shop completes "
            f"the order automatically after confirmations.",
            address=addr, amount=amount)

    def start(self):
        w = self.ctx.get("xmr_watcher")
        if w:
            w.start()


class ManualProvider(Provider):
    """Arbitrary payment instructions from config — bank transfer, cash at
    pickup, a crypto address you check by hand, anything. Multiple manual
    rails can coexist via distinct `id`s. Placeholders: {order_id} {total}."""
    method = "manual"

    def __init__(self, cfg, ctx):
        super().__init__(cfg, ctx)
        self.method = cfg.get("id", "manual")
        self.label = cfg.get("label", "Manual payment")

    def instruction(self, order):
        text = self.cfg.get("instructions",
                            "The merchant will contact you with payment steps.")
        try:
            text = text.format(order_id=order["order_id"],
                               total=f"{order['total']:.2f}")
        except (KeyError, IndexError):
            pass
        return PaymentInstruction(self.method, text)


BUILTINS = {p.method: p for p in (InvoiceProvider, LinkProvider, XmrProvider,
                                  ManualProvider)}


class Rails:
    """The configured provider set for a shop."""

    def __init__(self, shop, store, xmr_watcher=None):
        self.providers = {}
        ctx = {"shop": shop, "store": store, "xmr_watcher": xmr_watcher}
        configured = shop.get("payments") or [{"method": "invoice"}]
        for cfg in configured:
            cls = BUILTINS.get(cfg.get("method"))
            if cls is None:
                RNS.log(f"[rns-shop] unknown payment method "
                        f"{cfg.get('method')!r} — skipped", RNS.LOG_WARNING)
                continue
            p = cls(cfg, ctx)
            self.providers[p.method] = p  # ManualProvider may rename via `id`
        if "invoice" not in self.providers:
            self.providers["invoice"] = InvoiceProvider({}, ctx)

    def start(self):
        for p in self.providers.values():
            p.start()

    def methods(self):
        """[{method, label}] for checkout UIs."""
        return [{"method": m, "label": p.label}
                for m, p in self.providers.items()]

    def instruction(self, order, method=None):
        """Instruction for the chosen (or best available) method."""
        for m in ([method] if method else []) + ["invoice"]:
            p = self.providers.get(m)
            if p:
                ins = p.instruction(order)
                if ins:
                    return ins
        return InvoiceProvider({}, {"shop": {}, "store": None}).instruction(order)
