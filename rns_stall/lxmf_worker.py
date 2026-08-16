"""In-process LXMF worker: order confirmations, receipts, digital fulfillment.

Runs as a thread inside stalld (which already owns an RNS instance), using its
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
            RNS.log("[rns-stall] LXMF not installed — confirmations disabled",
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
            identity, display_name=f"{self.shop_name} (rns-stall)")
        self.router.announce(self.source.hash)
        RNS.log(f"[rns-stall] LXMF worker up as "
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
                RNS.log(f"[rns-stall] LXMF dest {dest_hash_hex[:8]}… unknown "
                        f"(no announce seen yet) — will retry", RNS.LOG_DEBUG)
                return False
            dest = RNS.Destination(identity, RNS.Destination.OUT,
                                   RNS.Destination.SINGLE, "lxmf", "delivery")
            msg = LXMF.LXMessage(dest, self.source, body, title,
                                 desired_method=LXMF.LXMessage.DIRECT)
            msg.try_propagation_on_fail = True
            self.router.handle_outbound(msg)
            return True
        except Exception as e:
            RNS.log(f"[rns-stall] LXMF send failed: {e}", RNS.LOG_DEBUG)
            return False

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
                RNS.log(f"[rns-stall] worker error: {e}", RNS.LOG_ERROR)
            time.sleep(POLL)

    def _tick(self):
        for o in self.store.orders_unnotified():
            if not o.get("lxmf"):
                self.store.mark_notified(o["order_id"])  # nothing to send to
                continue
            body = (f"Order {o['order_id']} received — {self._items_line(o)}.\n"
                    f"Total: {o['total']:.2f} {o['currency']}.\n"
                    f"{self.catalog.shop.get('invoice_note', 'Invoice follows.')}")
            if self._send(o["lxmf"], f"{self.shop_name}: order {o['order_id']}", body):
                self.store.mark_notified(o["order_id"])
                RNS.log(f"[rns-stall] confirmation sent for {o['order_id']}")
        for o in self.store.orders_unreceipted():
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
                body += (f"Your digital items are ready: {', '.join(digital)} — "
                         f"fetch with op delivery.get (identified).\n")
            if physical:
                body += "Physical items will ship; updates will follow.\n"
            sent_ok = (not o.get("lxmf")) or self._send(
                o["lxmf"], f"{self.shop_name}: receipt {o['order_id']}", body)
            if sent_ok:
                self.store.mark_receipted(o["order_id"],
                                          "fulfilled" if done else "paid")
                RNS.log(f"[rns-stall] receipt processed for {o['order_id']} "
                        f"(digital entitled: {digital})")
