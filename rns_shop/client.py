"""rns-shop CLI client: browse and buy from a shop over the mesh.

    python3 -m rns_shop.client <dest> catalog.list [--tag t]
    python3 -m rns_shop.client <dest> catalog.get --sku SKU
    python3 -m rns_shop.client <dest> cart.set --item SKU:2 --item SKU2:1
    python3 -m rns_shop.client <dest> order.submit [--item SKU:1] [--shipping "..."] [--note "..."]
    python3 -m rns_shop.client <dest> order.status --order-id ID
    python3 -m rns_shop.client <dest> entitlement.check --sku SKU
    python3 -m rns_shop.client <dest> manifest

Identified ops sign the link with your buyer identity (created on first use at
~/.rns_shop/client_identity — BACK IT UP: it is your customer account)."""
import argparse
import json
import os

import RNS
from meshapi import client as meshapi_client

from . import protocol

IDENTITY_PATH = os.path.expanduser("~/.rns_shop/client_identity")


def _buyer_identity():
    os.makedirs(os.path.dirname(IDENTITY_PATH), exist_ok=True)
    if os.path.isfile(IDENTITY_PATH):
        return RNS.Identity.from_file(IDENTITY_PATH)
    identity = RNS.Identity()
    identity.to_file(IDENTITY_PATH)
    print(f"(new buyer identity created at {IDENTITY_PATH} — back it up)")
    return identity


def _parse_items(pairs):
    out = []
    for p in pairs or []:
        sku, _, qty = p.partition(":")
        out.append({"sku": sku, "qty": int(qty or 1)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", help="shop destination hash (hex)")
    ap.add_argument("op", help="op name, or 'manifest'")
    ap.add_argument("--sku")
    ap.add_argument("--tag")
    ap.add_argument("--item", action="append", help="SKU:QTY (repeatable)")
    ap.add_argument("--shipping")
    ap.add_argument("--note")
    ap.add_argument("--order-id")
    ap.add_argument("--out", help="output path for delivery.get")
    ap.add_argument("--no-lxmf", action="store_true",
                    help="don't register an LXMF inbox with the order")
    ap.add_argument("--config", default=None, help="RNS config dir")
    ap.add_argument("--timeout", type=float, default=90)
    args = ap.parse_args()

    aspect = ".".join(protocol.ASPECTS)

    if args.op == "inbox":
        _inbox(args)
        return
    if args.op == "manifest":
        m = meshapi_client.fetch_manifest(args.dest, protocol.APP_NAME, aspect,
                                          protocol.PATH, config=args.config,
                                          timeout=args.timeout)
        print(json.dumps(m, indent=2))
        return

    body = {"v": protocol.VERSION, "op": args.op}
    if args.sku:
        body["sku"] = args.sku
    if args.tag:
        body["tag"] = args.tag
    if args.item:
        body["items"] = _parse_items(args.item)
    if args.shipping:
        body["shipping"] = args.shipping
    if args.note:
        body["note"] = args.note
    if args.order_id:
        body["order_id"] = args.order_id

    identify = _buyer_identity() if args.op in protocol.IDENTIFIED_OPS else None

    # Ordering: opt in to LXMF confirmations. The inbox hash is computable
    # WITHOUT initializing RNS (meshapi's client owns the one allowed
    # RNS.Reticulum() init in this process); we announce after the call.
    lxmf_optin = (args.op == protocol.OP_ORDER_SUBMIT
                  and not args.no_lxmf and identify)
    if lxmf_optin:
        body["lxmf"] = RNS.hexrep(
            RNS.Destination.hash(identify, "lxmf", "delivery"), delimit=False)
        print(f"(LXMF inbox attached: {body['lxmf']} — read receipts with "
              f"the 'inbox' op)")

    ok, resp = meshapi_client.call(args.dest, protocol.APP_NAME, aspect,
                                   protocol.PATH, body, config=args.config,
                                   timeout=args.timeout, identify=identify)

    if lxmf_optin and ok:
        try:  # RNS is initialized by the call above; announce so the shop
            # can resolve our inbox (the worker retries until it can).
            RNS.Destination(identify, RNS.Destination.IN, RNS.Destination.SINGLE,
                            "lxmf", "delivery").announce()
        except Exception as e:
            print(f"(inbox announce failed: {e} — run 'inbox' to announce)")

    # delivery.get: write the payload to disk instead of dumping bytes to stdout
    if ok and args.op == protocol.OP_DELIVERY and isinstance(resp.get("data"), bytes):
        out = args.out or resp.get("filename", "delivery.bin")
        with open(out, "wb") as fh:
            fh.write(resp["data"])
        print(json.dumps({"ok": True, "op": args.op, "saved": out,
                          "bytes": len(resp["data"]),
                          "filename": resp.get("filename")}, indent=2))
        raise SystemExit(0)

    print(json.dumps(resp, indent=2, default=repr))
    raise SystemExit(0 if ok else 1)


def _inbox(args):
    """Receive LXMF messages (order confirmations/receipts) for the buyer
    identity. Direct delivery: run it while your node is reachable."""
    try:
        import LXMF
    except ImportError:
        print("pip install lxmf"); raise SystemExit(1)
    RNS.Reticulum(args.config)
    identity = _buyer_identity()
    router = LXMF.LXMRouter(identity=identity,
                            storagepath=os.path.expanduser("~/.rns_shop/lxmf"))
    dest = router.register_delivery_identity(identity, display_name="rns-shop buyer")
    got = []

    def on_msg(msg):
        got.append(msg)
        print(f"\n--- {msg.title_as_string()} ---\n{msg.content_as_string()}\n")

    router.register_delivery_callback(on_msg)
    router.announce(dest.hash)
    print(f"inbox open as {RNS.hexrep(dest.hash, delimit=False)} — waiting "
          f"{int(args.timeout)}s for messages…")
    import time as _t
    _t.sleep(args.timeout)
    print(f"({len(got)} message(s) received)")


if __name__ == "__main__":
    main()
