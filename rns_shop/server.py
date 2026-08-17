"""shopd: the rns-shop service daemon.

Follows the proven rns-geo/rns-time template: standalone RNS instance riding a
local hub, request-handler destination, per-link token-bucket rate limiting,
health-gated announces, /healthz for monitoring, stable identity on disk
(NEVER lose the identity volume -- the destination hash is the published trust
anchor). Buyer identity comes from link.identify() -> remote_identity.
"""
import argparse
import json
import os
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import RNS
from meshapi import service as meshapi_service

from . import catalog as catalog_mod
from . import manifest, payments, protocol, providers, render
from .store import Store

ADDRESS_FIELDS = ("name", "street", "street2", "city", "region", "postal",
                  "country")

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
_files_dir = None
_xmr = None
_rails = None


def _clean_address(raw):
    """Structured shipping address from a dict (extra keys dropped, values
    trimmed). Returns None if it isn't usable as an address at all."""
    if isinstance(raw, str):  # legacy free-text
        t = raw.strip()[:MAX_TEXT]
        return {"text": t} if t else None
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in ADDRESS_FIELDS:
        v = str(raw.get(k, "") or "").strip()[:200]
        if v:
            out[k] = v
    return out or None


def _address_complete(addr):
    return bool(addr) and all(addr.get(k) for k in
                              ("name", "street", "city", "postal", "country"))


def _order_checks(items, shipping):
    """Shipping restrictions: physical items need a complete address in an
    allowed country. Returns error code or None."""
    physical = [_catalog.items[e["sku"]] for e in items
                if _catalog.items[e["sku"]].get("kind", "physical") == "physical"]
    if not physical:
        return None
    if not _address_complete(shipping):
        return "address_required"
    for it in physical:
        if not catalog_mod.CatalogSource.ships_ok(it, shipping.get("country")):
            return "not_shipped_to_country"
    return None

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
        result = _submit_order(identity_hex, items, req.get("shipping"),
                               str(req.get("note", ""))[:MAX_TEXT],
                               str(req.get("lxmf", ""))[:64] or None,
                               method=req.get("method"))
        if not result.get("ok"):
            return protocol.err(result.get("err", "order_failed"), req)
        result.pop("ok", None)
        return protocol.ok(result, req)
    if op == "profile.get":
        return protocol.ok({"profile": _store.profile_get(identity_hex),
                            "methods": _rails.methods()}, req)
    if op == "profile.set":
        addr = _clean_address(req.get("shipping"))
        prof = _store.profile_set(
            identity_hex,
            shipping=json.dumps(addr) if addr else None,
            billing=str(req.get("billing", ""))[:MAX_TEXT] or None,
            pay_method=str(req.get("pay_method", ""))[:32] or None)
        return protocol.ok({"profile": prof}, req)
    if op == protocol.OP_ORDER_STATUS:
        o = _store.order_get(identity_hex, str(req.get("order_id", "")))
        return protocol.ok({"order": o}, req) if o else protocol.err("not_found", req)
    if op == protocol.OP_ENTITLEMENT:
        return protocol.ok(
            {"entitled": _store.entitled(identity_hex, str(req.get("sku", "")))}, req)
    if op == protocol.OP_DELIVERY:
        sku = str(req.get("sku", ""))
        it = _catalog.items.get(sku)
        if not it:
            return protocol.err("not_found", req)
        if not _store.entitled(identity_hex, sku):
            return protocol.err("not_entitled", req, "buy it first")
        rel = it.get("file")
        if not rel:
            return protocol.err("no_file", req, "item has no digital payload")
        # confine to the files dir — nothing request-derived touches the path
        fp = os.path.realpath(os.path.join(_files_dir, rel))
        if not fp.startswith(os.path.realpath(_files_dir)) or not os.path.isfile(fp):
            return protocol.err("no_file", req)
        with open(fp, "rb") as fh:
            data = fh.read()
        RNS.log(f"[rns-shop] DELIVERY {sku} -> {identity_hex[:8]}… "
                f"({len(data)} bytes)", RNS.LOG_NOTICE)
        return protocol.ok({"filename": os.path.basename(fp), "data": data}, req)
    if op in (protocol.OP_PAY_LINK, protocol.OP_PAY_XMR):
        o = _store.order_get(identity_hex, str(req.get("order_id", "")))
        if not o:
            return protocol.err("not_found", req)
        method = "link" if op == protocol.OP_PAY_LINK else "xmr"
        ins = _rails.instruction(o, method)
        if ins.method != method:
            return protocol.err("not_configured", req,
                                f"{method} rail not enabled by this shop")
        _store.order_set_payment(o["order_id"], ins.method, ins.text)
        return protocol.ok({"payment": ins.as_dict()}, req)
    return protocol.err("bad_op", req)


def _submit_order(identity_hex, items, shipping=None, note="", lxmf=None,
                  method=None):
    """Shared order path: RNS op and the node's local checkout both land here.
    Returns dict with err OR the order + its payment instruction."""
    addr = _clean_address(shipping)
    bad = _order_checks(items, addr)
    if bad:
        return {"ok": False, "err": bad}
    total = _total(items)
    oid = _store.order_create(identity_hex, items, total,
                              _catalog.shop.get("currency", "USD"),
                              addr, note, lxmf=lxmf)
    order = {"order_id": oid, "total": total, "items": items}
    preferred = method or _store.profile_get(identity_hex).get("pay_method")
    options = _rails.instructions_all(order, preferred=preferred)
    # persist the full option list for LXMF + later HOW-TO-PAY displays
    joined = "\n".join(f"[{o['label']}] {o['text']}" for o in options)
    _store.order_set_payment(oid, options[0]["method"] if options else "invoice",
                             joined)
    RNS.log(f"[rns-shop] ORDER {oid} from {identity_hex[:8]}… total {total}",
            RNS.LOG_NOTICE)
    return {"ok": True, "order_id": oid, "total": total,
            "currency": _catalog.shop.get("currency", "USD"),
            "payment": options[0] if options else None,
            "payments": options,
            "payment_options": [m["method"] for m in _rails.methods()]}


# ---- local checkout API (loopback-only) -------------------------------------
# The NomadNet node's executable pages (buy.mu / orders.mu) run on the same
# host and call this. AUTH MODEL: NomadNet already authenticated the buyer —
# the page script receives the cryptographic `remote_identity` and forwards it;
# this API binds 127.0.0.1 ONLY and trusts local page scripts. Never expose it.
class _LocalApiHandler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        req = json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0))))
        identity = str(req.get("identity", ""))
        assert len(identity) == 32 and int(identity, 16) is not None
        return req, identity

    def do_POST(self):
        try:
            req, identity = self._body()
        except Exception:
            return self._json(400, {"ok": False, "err": "bad_request"})
        if self.path == "/order":
            items = _items_valid(req.get("items"))
            if items is None:
                return self._json(400, {"ok": False, "err": "bad_items"})
            out = _submit_order(identity, items, req.get("shipping"),
                                str(req.get("note", ""))[:MAX_TEXT],
                                method=req.get("method"))
            if out.get("ok") and req.get("save_profile"):
                addr = _clean_address(req.get("shipping"))
                _store.profile_set(identity,
                                   shipping=json.dumps(addr) if addr else None,
                                   pay_method=req.get("method"))
            return self._json(200 if out.get("ok") else 400, out)
        if self.path == "/profile":
            addr = _clean_address(req.get("shipping"))
            prof = _store.profile_set(
                identity, shipping=json.dumps(addr) if addr else None,
                pay_method=str(req.get("method", ""))[:32] or None)
            return self._json(200, {"ok": True, "profile": prof})
        return self._json(404, {"ok": False, "err": "not_found"})

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/shop_info":  # no identity needed: public shop metadata
            return self._json(200, {
                "ok": True, "name": _catalog.shop.get("name", "shop"),
                "currency": _catalog.shop.get("currency", "USD"),
                "methods": _rails.methods()})
        if u.path == "/item":
            it = _catalog.get(q.get("sku", ""))
            if not it:
                return self._json(404, {"ok": False, "err": "not_found"})
            it["ok"] = True
            return self._json(200, it)
        identity = q.get("identity", "")
        if len(identity) != 32:
            return self._json(400, {"ok": False, "err": "bad_identity"})
        if u.path == "/profile":
            prof = _store.profile_get(identity)
            try:
                prof["shipping"] = json.loads(prof.get("shipping") or "{}")
            except Exception:
                prof["shipping"] = {}
            return self._json(200, {"ok": True, "profile": prof})
        if u.path == "/payment":
            pay = _store.order_payment(q.get("order_id", ""))
            return self._json(200, {"ok": True, "payment": pay})
        if u.path == "/orders":
            return self._json(200, {"ok": True,
                                    "orders": _store.orders_for_identity(identity)})
        if u.path == "/deliver":
            sku = q.get("sku", "")
            it = _catalog.items.get(sku)
            if not it or not _store.entitled(identity, sku):
                return self._json(403, {"ok": False, "err": "not_entitled"})
            rel = it.get("file")
            if not rel:
                return self._json(404, {"ok": False, "err": "no_file"})
            fp = os.path.realpath(os.path.join(_files_dir, rel))
            if not fp.startswith(os.path.realpath(_files_dir)) \
                    or not os.path.isfile(fp):
                return self._json(404, {"ok": False, "err": "no_file"})
            data = open(fp, "rb").read()
            try:  # inline-display small text goods; otherwise report size only
                text = data.decode("utf-8") if len(data) <= 65536 else None
            except UnicodeDecodeError:
                text = None
            return self._json(200, {"ok": True, "filename": os.path.basename(fp),
                                    "bytes": len(data), "text": text})
        return self._json(404, {"ok": False, "err": "not_found"})

    def log_message(self, *a):
        pass


def _start_local_api(port):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _LocalApiHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    RNS.log(f"[rns-shop] local checkout API on 127.0.0.1:{port} "
            f"(for the node's buy/orders pages)")


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
        RNS.log(f"[rns-shop] dispatch error: {e}", RNS.LOG_ERROR)
        return protocol.pack(protocol.err("internal", req))


def _health_loop(dest):
    while True:
        try:
            if _catalog.changed():
                _catalog.load()
                n = render.write_pages(_catalog, _state["dest"], _pages_out)
                RNS.log(f"[rns-shop] catalog changed — re-rendered {n} pages")
            _state["ok"] = bool(_catalog.items)
            _state["items"] = len(_catalog.items)
        except Exception as e:
            _state["ok"] = False
            RNS.log(f"[rns-shop] health/catalog error: {e}", RNS.LOG_ERROR)
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
    global _catalog, _store, _manifest, _pages_out, _files_dir, _xmr
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", default=os.environ.get("SHOP_IDENTITY",
                    os.path.expanduser("~/.rns_shop/identity")))
    ap.add_argument("--config", default=os.environ.get("RNS_CONFIG"))
    ap.add_argument("--catalog", default=os.environ.get("SHOP_CATALOG", "catalog.yaml"))
    ap.add_argument("--db", default=os.environ.get("SHOP_DB", "shop.db"))
    ap.add_argument("--pages-out", default=os.environ.get("SHOP_PAGES_OUT", "pages"))
    ap.add_argument("--files", default=os.environ.get("SHOP_FILES", "files"))
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

    # _files_dir must exist BEFORE the catalog loads: a connector whose
    # upstream serves images from an external URL (Squarespace, ...) caches
    # them here on first load (see catalog.CatalogSource / squarespace.py --
    # NomadNet clients can't fetch a clearnet CDN URL, so this is the only
    # way a mesh client ever sees the photo).
    _files_dir = os.path.abspath(args.files)
    os.makedirs(_files_dir, exist_ok=True)
    _catalog = catalog_mod.open_catalog(args.catalog, files_dir=_files_dir)
    _store = Store(args.db)
    _pages_out = args.pages_out

    dest = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE,
                           protocol.APP_NAME, *protocol.ASPECTS)
    dest.register_request_handler(protocol.PATH, response_generator=on_request,
                                  allow=RNS.Destination.ALLOW_ALL)

    # learn buyers' LXMF inboxes from their clients' own announces, so page
    # purchases get confirmations without asking for an address
    class _LxmfAnnounces:
        aspect_filter = "lxmf.delivery"

        def received_announce(self, destination_hash, announced_identity,
                              app_data):
            try:
                _store.lxmf_map_put(
                    RNS.hexrep(announced_identity.hash, delimit=False),
                    RNS.hexrep(destination_hash, delimit=False))
            except Exception:
                pass

    RNS.Transport.register_announce_handler(_LxmfAnnounces())
    RNS.log("[rns-shop] listening for lxmf.delivery announces "
            "(auto-discovers buyer inboxes)")

    dest_hex = RNS.hexrep(dest.hash, delimit=False)
    _state["dest"] = dest_hex
    _manifest = manifest.build(dest_hex, _catalog.shop.get("name", "shop"))

    n = render.write_pages(_catalog, dest_hex, _pages_out)
    ni = render.sync_images(_catalog, os.environ.get("SHOP_IMAGES"),
                            os.environ.get("SHOP_NODE_FILES"))
    RNS.log(f"[rns-shop] rendered {n} storefront pages -> {_pages_out} "
            f"(+{ni} product images)")
    RNS.log(f"[rns-shop] serving as {RNS.prettyhexrep(dest.hash)}")
    print(f"rns-shop destination: {dest_hex}", flush=True)

    _state["ok"] = bool(_catalog.items)
    _state["items"] = len(_catalog.items)

    # LXMF worker: confirmations, receipts, digital entitlement (M2)
    from .lxmf_worker import Worker
    Worker(_store, _catalog, os.path.dirname(os.path.abspath(args.identity)),
           _catalog.shop.get("name", "shop")).start()

    # payment rails: generic provider registry (invoice/link/xmr/…)
    global _rails
    xmr_rpc = os.environ.get("SHOP_XMR_RPC")
    if xmr_rpc:
        _xmr = payments.XmrWatcher(_store, xmr_rpc)
    _rails = providers.Rails(_catalog.shop, _store, xmr_watcher=_xmr)
    _rails.start()
    RNS.log(f"[rns-shop] payment rails: "
            f"{[m['method'] for m in _rails.methods()]}")

    _start_local_api(int(os.environ.get("SHOP_LOCAL_API", "8219")))

    threading.Thread(target=_health_loop, args=(dest,), daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.healthz_port), _HealthHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    RNS.log(f"[rns-shop] healthz on :{args.healthz_port}")

    while True:
        if _state["ok"]:
            dest.announce()
            RNS.log(f"[rns-shop] announced ({_state['items']} items)")
        else:
            RNS.log("[rns-shop] NOT announcing — catalog empty/unhealthy",
                    RNS.LOG_WARNING)
        time.sleep(ANNOUNCE_INTERVAL)


if __name__ == "__main__":
    main()
