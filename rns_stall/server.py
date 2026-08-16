"""stalld: the rns-stall service daemon.

Follows the proven rns-geo/rns-time template: standalone RNS instance riding a
local hub, request-handler destination, per-link token-bucket rate limiting,
health-gated announces, /healthz for monitoring, stable identity on disk
(NEVER lose the identity volume -- the destination hash is the published trust
anchor). Buyer identity comes from link.identify() -> remote_identity.
"""
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import RNS
from meshapi import service as meshapi_service

from . import catalog as catalog_mod
from . import manifest, protocol, render
from .store import Store

ANNOUNCE_INTERVAL = 900
HEALTH_INTERVAL = 60
PER_LINK_RPS = 2.0
PER_LINK_BURST = 8
MAX_ORDER_ITEMS = 50
MAX_TEXT = 2000

_state = {"ok": False, "dest": None, "items": 0}
_catalog = None
_store = None
_manifest = None
_pages_out = None

_buckets = {}
_buckets_lock = threading.Lock()


def _allow(link_id):
    key = bytes(link_id) if link_id is not None else b"anon"
    now = time.time()
    with _buckets_lock:
        tokens, ts = _buckets.get(key, (PER_LINK_BURST, now))
        tokens = min(PER_LINK_BURST, tokens + (now - ts) * PER_LINK_RPS)
        if tokens >= 1.0:
            _buckets[key] = (tokens - 1.0, now)
            if len(_buckets) > 512:
                for k, (_, t) in list(_buckets.items()):
                    if now - t > 300:
                        _buckets.pop(k, None)
            return True
        _buckets[key] = (tokens, now)
        return False


def _items_valid(items):
    """[{sku, qty}] -> normalized list or None."""
    if not isinstance(items, list) or not items or len(items) > MAX_ORDER_ITEMS:
        return None
    out = []
    for e in items:
        if not isinstance(e, dict):
            return None
        sku = str(e.get("sku", ""))
        it = _catalog.items.get(sku)
        try:
            qty = int(e.get("qty", 1))
        except (TypeError, ValueError):
            return None
        if not it or qty < 1 or qty > 999:
            return None
        out.append({"sku": sku, "qty": qty})
    return out


def _total(items):
    return round(sum(_catalog.items[e["sku"]]["price"] * e["qty"] for e in items), 2)


def _dispatch(req, identity_hex):
    op = req.get("op")
    if op == protocol.OP_CATALOG_LIST:
        return protocol.ok({"items": _catalog.list(req.get("tag"))}, req)
    if op == protocol.OP_CATALOG_GET:
        it = _catalog.get(req.get("sku", ""))
        return protocol.ok({"item": it}, req) if it else protocol.err("not_found", req)
    if op == protocol.OP_CART_GET:
        return protocol.ok({"items": _store.cart_get(identity_hex)}, req)
    if op == protocol.OP_CART_SET:
        items = _items_valid(req.get("items"))
        if items is None:
            return protocol.err("bad_items", req)
        return protocol.ok({"items": _store.cart_set(identity_hex, items)}, req)
    if op == protocol.OP_ORDER_SUBMIT:
        items = req.get("items") or _store.cart_get(identity_hex)
        items = _items_valid(items)
        if items is None:
            return protocol.err("bad_items", req, "pass items or fill your cart")
        shipping = str(req.get("shipping", ""))[:MAX_TEXT]
        note = str(req.get("note", ""))[:MAX_TEXT]
        total = _total(items)
        oid = _store.order_create(identity_hex, items, total,
                                  _catalog.shop.get("currency", "USD"),
                                  {"text": shipping} if shipping else None, note)
        RNS.log(f"[rns-stall] ORDER {oid} from {identity_hex[:8]}… "
                f"total {total}", RNS.LOG_NOTICE)
        return protocol.ok({"order_id": oid, "total": total,
                            "currency": _catalog.shop.get("currency", "USD"),
                            "payment_options": ["invoice"],
                            "invoice_note": _catalog.shop.get(
                                "invoice_note",
                                "You will receive an LXMF invoice.")}, req)
    if op == protocol.OP_ORDER_STATUS:
        o = _store.order_get(identity_hex, str(req.get("order_id", "")))
        return protocol.ok({"order": o}, req) if o else protocol.err("not_found", req)
    if op == protocol.OP_ENTITLEMENT:
        return protocol.ok(
            {"entitled": _store.entitled(identity_hex, str(req.get("sku", "")))}, req)
    return protocol.err("bad_op", req)


def on_request(path, data, request_id, link_id, remote_identity, requested_at):
    try:
        req = protocol.unpack(data)
    except Exception:
        return protocol.pack(protocol.err("bad_encoding"))
    if not isinstance(req, dict):
        return protocol.pack(protocol.err("bad_encoding"))
    if not _allow(link_id):
        return protocol.pack(protocol.err("rate_limited", req))
    if meshapi_service.is_manifest_request(req):
        return protocol.pack(meshapi_service.manifest_reply(_manifest, protocol.VERSION))
    if req.get("v") != protocol.VERSION:
        return protocol.pack(protocol.err("bad_version", req))
    identity_hex = (RNS.hexrep(remote_identity.hash, delimit=False)
                    if remote_identity else None)
    op_def = next((o for o in _manifest["ops"] if o["op"] == req.get("op")), None)
    if op_def is None:
        return protocol.pack(protocol.err("bad_op", req))
    ok_auth, code = meshapi_service.authorize(op_def, identity_hex)
    if not ok_auth:
        return protocol.pack(protocol.err(code, req))
    try:
        return protocol.pack(_dispatch(req, identity_hex))
    except Exception as e:
        RNS.log(f"[rns-stall] dispatch error: {e}", RNS.LOG_ERROR)
        return protocol.pack(protocol.err("internal", req))


def _health_loop(dest):
    while True:
        try:
            if _catalog.changed():
                _catalog.load()
                n = render.write_pages(_catalog, _state["dest"], _pages_out)
                RNS.log(f"[rns-stall] catalog changed — re-rendered {n} pages")
            _state["ok"] = bool(_catalog.items)
            _state["items"] = len(_catalog.items)
        except Exception as e:
            _state["ok"] = False
            RNS.log(f"[rns-stall] health/catalog error: {e}", RNS.LOG_ERROR)
        time.sleep(HEALTH_INTERVAL)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": _state["ok"], "items": _state["items"],
                           "dest_hash": _state["dest"],
                           "app": protocol.APP_NAME,
                           "aspect": ".".join(protocol.ASPECTS)}).encode()
        self.send_response(200 if _state["ok"] else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    global _catalog, _store, _manifest, _pages_out
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", default=os.environ.get("STALL_IDENTITY",
                    os.path.expanduser("~/.rns_stall/identity")))
    ap.add_argument("--config", default=os.environ.get("RNS_CONFIG"))
    ap.add_argument("--catalog", default=os.environ.get("STALL_CATALOG", "catalog.yaml"))
    ap.add_argument("--db", default=os.environ.get("STALL_DB", "stall.db"))
    ap.add_argument("--pages-out", default=os.environ.get("STALL_PAGES_OUT", "pages"))
    ap.add_argument("--healthz-port", type=int,
                    default=int(os.environ.get("HEALTHZ_PORT", "8216")))
    args = ap.parse_args()

    RNS.Reticulum(args.config)

    os.makedirs(os.path.dirname(os.path.abspath(args.identity)), exist_ok=True)
    if os.path.isfile(args.identity):
        identity = RNS.Identity.from_file(args.identity)
    else:
        identity = RNS.Identity()
        identity.to_file(args.identity)

    _catalog = catalog_mod.Catalog(args.catalog)
    _store = Store(args.db)
    _pages_out = args.pages_out

    dest = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE,
                           protocol.APP_NAME, *protocol.ASPECTS)
    dest.register_request_handler(protocol.PATH, response_generator=on_request,
                                  allow=RNS.Destination.ALLOW_ALL)

    dest_hex = RNS.hexrep(dest.hash, delimit=False)
    _state["dest"] = dest_hex
    _manifest = manifest.build(dest_hex, _catalog.shop.get("name", "stall"))

    n = render.write_pages(_catalog, dest_hex, _pages_out)
    RNS.log(f"[rns-stall] rendered {n} storefront pages -> {_pages_out}")
    RNS.log(f"[rns-stall] serving as {RNS.prettyhexrep(dest.hash)}")
    print(f"rns-stall destination: {dest_hex}", flush=True)

    _state["ok"] = bool(_catalog.items)
    _state["items"] = len(_catalog.items)
    threading.Thread(target=_health_loop, args=(dest,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.healthz_port), _HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    RNS.log(f"[rns-stall] healthz on :{args.healthz_port}")

    while True:
        if _state["ok"]:
            dest.announce()
            RNS.log(f"[rns-stall] announced ({_state['items']} items)")
        else:
            RNS.log("[rns-stall] NOT announcing — catalog empty/unhealthy",
                    RNS.LOG_WARNING)
        time.sleep(ANNOUNCE_INTERVAL)


if __name__ == "__main__":
    main()
