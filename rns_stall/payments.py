"""Payment rails.

Rail A (hybrid web checkout): pay.link returns an HTTPS checkout URL the buyer
opens whenever they have internet. v1 providers:
  - template: shop.pay_link_template, e.g.
      "https://buy.stripe.com/XXXX?client_reference_id={order_id}"
  - per-sku: shop.pay_links: {SKU: url} (single-item orders only)
The webhook bridge (webhook_bridge.py) flips orders to paid when the processor
calls back; the LXMF worker then entitles/receipts.

Rail B (Monero, watch-only): per-order subaddress from monero-wallet-rpc;
watcher polls for confirmed transfers. Dormant unless STALL_XMR_RPC is set.
No spend keys are ever involved.
"""
import json
import threading
import time
import urllib.request

import RNS


# ---- Rail A: pay.link -------------------------------------------------------
def pay_link(shop, order):
    tmpl = shop.get("pay_link_template")
    if tmpl:
        return tmpl.format(order_id=order["order_id"])
    links = shop.get("pay_links") or {}
    items = order["items"]
    if len(items) == 1 and items[0]["qty"] == 1 and items[0]["sku"] in links:
        return links[items[0]["sku"]]
    return None


# ---- Rail B: Monero watcher --------------------------------------------------
class XmrWatcher:
    """Watch-only monero-wallet-rpc poller. For each order with an assigned
    subaddress, mark paid when a confirmed transfer >= amount arrives."""

    def __init__(self, store, rpc_url, poll=60, min_confirmations=3):
        self.store = store
        self.rpc = rpc_url.rstrip("/")
        self.poll = poll
        self.min_conf = min_confirmations

    def _call(self, method, params=None):
        req = urllib.request.Request(
            f"{self.rpc}/json_rpc",
            data=json.dumps({"jsonrpc": "2.0", "id": "0", "method": method,
                             "params": params or {}}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read())
        if "error" in out:
            raise RuntimeError(out["error"])
        return out["result"]

    def assign_subaddress(self, order_id):
        r = self._call("create_address", {"account_index": 0,
                                          "label": f"order:{order_id}"})
        return r["address"], r["address_index"]

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()
        RNS.log(f"[rns-stall] XMR watcher polling {self.rpc}")

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                RNS.log(f"[rns-stall] xmr watcher error: {e}", RNS.LOG_DEBUG)
            time.sleep(self.poll)

    def _tick(self):
        pending = self.store.orders_awaiting_xmr()
        if not pending:
            return
        transfers = self._call("get_transfers", {"in": True, "account_index": 0})
        confirmed = {}
        for t in transfers.get("in", []):
            if t.get("confirmations", 0) >= self.min_conf:
                idx = t.get("subaddr_index", {}).get("minor")
                confirmed[idx] = confirmed.get(idx, 0) + t.get("amount", 0)
        for o in pending:
            got = confirmed.get(o["xmr_index"], 0) / 1e12
            if got + 1e-12 >= o["xmr_amount"]:
                self.store.order_set_status(o["order_id"], "paid")
                RNS.log(f"[rns-stall] XMR paid: order {o['order_id']} "
                        f"({got} XMR)", RNS.LOG_NOTICE)
