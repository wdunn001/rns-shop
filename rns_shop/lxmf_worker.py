"""In-process LXMF worker: order confirmations, receipts, digital fulfillment.

Runs as a thread inside shopd (which already owns an RNS instance), using its
own LXMF delivery identity. Buyers opt in by including their LXMF delivery
destination hash with the order (the CLI does this automatically and announces
the buyer's delivery destination so we can resolve it).

State machine work done here:
  submitted + not notified  -> send "order received + invoice" -> notified
  paid + not receipted      -> entitle digital SKUs -> send receipt
                               -> fulfilled (if nothing physical remains)
"""
import os
import threading
import time

import RNS

try:
    import LXMF
    HAVE_LXMF = True
except ImportError:
    HAVE_LXMF = False

POLL = 20  # seconds


class Worker:
    def __init__(self, store, catalog, state_dir, shop_name):
        self.store = store
        self.catalog = catalog
        self.shop_name = shop_name
        self.router = None
        self.source = None
        self._state_dir = state_dir

    def start(self):
        if not HAVE_LXMF:
            RNS.log("[rns-shop] LXMF not installed, confirmations disabled",
                    RNS.LOG_WARNING)
            return False
        idpath = os.path.join(self._state_dir, "lxmf_identity")
        if os.path.isfile(idpath):
            identity = RNS.Identity.from_file(idpath)
        else:
            identity = RNS.Identity()
            identity.to_file(idpath)
        self.router = LXMF.LXMRouter(identity=identity,
                                     storagepath=os.path.join(self._state_dir, "lxmf"))
        self.source = self.router.register_delivery_identity(
            identity, display_name=f"{self.shop_name} (rns-shop)")
        self.router.announce(self.source.hash)
        RNS.log(f"[rns-shop] LXMF worker up as "
                f"{RNS.hexrep(self.source.hash, delimit=False)}")
        threading.Thread(target=self._loop, daemon=True).start()
        return True

    def _send(self, dest_hash_hex, title, body):
        try:
            dh = bytes.fromhex(dest_hash_hex)
            if not RNS.Transport.has_path(dh):
                RNS.Transport.request_path(dh)
                deadline = time.time() + 15
                while not RNS.Transport.has_path(dh) and time.time() < deadline:
                    time.sleep(0.5)
            identity = RNS.Identity.recall(dh)
            if identity is None:
                RNS.log(f"[rns-shop] LXMF dest {dest_hash_hex[:8]}... unknown "
                        f"(no announce seen yet), will retry", RNS.LOG_DEBUG)
                return False
            dest = RNS.Destination(identity, RNS.Destination.OUT,
                                   RNS.Destination.SINGLE, "lxmf", "delivery")
            msg = LXMF.LXMessage(dest, self.source, body, title,
                                 desired_method=LXMF.LXMessage.DIRECT)
            msg.try_propagation_on_fail = True
            self.router.handle_outbound(msg)
            return True
        except Exception as e:
            RNS.log(f"[rns-shop] LXMF send failed: {e}", RNS.LOG_DEBUG)
            return False

    def send_message(self, order, title, body):
        """Merchant-authored, ad-hoc message to a buyer about a specific
        order (the admin portal's "message customer" action) -- reuses the
        SAME inbox-resolution + send path as the automated confirmation/
        receipt messages above, so a merchant note lands exactly where
        those already do (no new discovery/delivery logic to get wrong).
        Returns (ok, reason); reason is None on success, else "no_inbox"
        (the buyer's LXMF client hasn't announced yet -- same "keep
        waiting" state the automated flow tolerates) or "send_failed"."""
        inbox = self._inbox_for(order)
        if not inbox:
            return False, "no_inbox"
        if self._send(inbox, title, body):
            return True, None
        return False, "send_failed"

    def _items_line(self, order):
        parts = []
        for e in order["items"]:
            it = self.catalog.items.get(e["sku"], {})
            parts.append(f"{e['qty']}x {it.get('title', e['sku'])}")
        return ", ".join(parts)

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                RNS.log(f"[rns-shop] worker error: {e}", RNS.LOG_ERROR)
            time.sleep(POLL)

    GIVE_UP_AFTER = 86400  # stop retrying inbox discovery after a day

    def _inbox_for(self, o):
        """Order's LXMF inbox: explicit (CLI opt-in) or learned from the
        buyer identity's own lxmf.delivery announce (page purchases)."""
        if o.get("lxmf"):
            return o["lxmf"]
        found = self.store.lxmf_lookup(o["identity"])
        if found:
            self.store.order_set_lxmf(o["order_id"], found)
            RNS.log(f"[rns-shop] learned inbox for {o['identity'][:8]}... "
                    f"via announce", RNS.LOG_DEBUG)
        return found

    def _tick(self):
        for o in self.store.orders_unnotified():
            inbox = self._inbox_for(o)
            if not inbox:
                if time.time() - (o.get("created") or 0) > self.GIVE_UP_AFTER:
                    self.store.mark_notified(o["order_id"])  # no inbox found
                continue  # keep waiting for the buyer's client to announce
            o = dict(o, lxmf=inbox)
            pay = self.store.order_payment(o["order_id"])
            pay_line = pay["text"] if pay else self.catalog.shop.get(
                "invoice_note", "Invoice follows.")
            body = (f"Order {o['order_id']} received, {self._items_line(o)}.\n"
                    f"Total: {o['total']:.2f} {o['currency']}.\n"
                    f"HOW TO PAY: {pay_line}")
            if self._send(o["lxmf"], f"{self.shop_name}: order {o['order_id']}", body):
                self.store.mark_notified(o["order_id"])
                RNS.log(f"[rns-shop] confirmation sent for {o['order_id']}")
        for o in self.store.orders_unreceipted():
            o = dict(o, lxmf=self._inbox_for(o))
            digital, physical = [], []
            for e in o["items"]:
                it = self.catalog.items.get(e["sku"])
                (digital if it and it["kind"] in ("digital", "service")
                 else physical).append(e["sku"])
            for sku in digital:
                self.store.entitle(o["identity"], sku)
            done = not physical
            body = (f"Payment received for order {o['order_id']} "
                    f"({o['total']:.2f} {o['currency']}).\n")
            if digital:
                body += (f"Your digital items are ready: {', '.join(digital)}, "
                         f"fetch with op delivery.get (identified).\n")
            if physical:
                body += "Physical items will ship; updates will follow.\n"
            sent_ok = (not o.get("lxmf")) or self._send(
                o["lxmf"], f"{self.shop_name}: receipt {o['order_id']}", body)
            if sent_ok:
                self.store.mark_receipted(o["order_id"],
                                          "fulfilled" if done else "paid")
                RNS.log(f"[rns-shop] receipt processed for {o['order_id']} "
                        f"(digital entitled: {digital})")
