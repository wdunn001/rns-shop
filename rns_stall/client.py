"""rns-stall CLI client: browse and buy from a stall over the mesh.

    python3 -m rns_stall.client <dest> catalog.list [--tag t]
    python3 -m rns_stall.client <dest> catalog.get --sku SKU
    python3 -m rns_stall.client <dest> cart.set --item SKU:2 --item SKU2:1
    python3 -m rns_stall.client <dest> order.submit [--item SKU:1] [--shipping "..."] [--note "..."]
    python3 -m rns_stall.client <dest> order.status --order-id ID
    python3 -m rns_stall.client <dest> entitlement.check --sku SKU
    python3 -m rns_stall.client <dest> manifest

Identified ops sign the link with your buyer identity (created on first use at
~/.rns_stall/client_identity — BACK IT UP: it is your customer account)."""
import argparse
import json
import os

import RNS
from meshapi import client as meshapi_client

from . import protocol

IDENTITY_PATH = os.path.expanduser("~/.rns_stall/client_identity")


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
    ap.add_argument("dest", help="stall destination hash (hex)")
    ap.add_argument("op", help="op name, or 'manifest'")
    ap.add_argument("--sku")
    ap.add_argument("--tag")
    ap.add_argument("--item", action="append", help="SKU:QTY (repeatable)")
    ap.add_argument("--shipping")
    ap.add_argument("--note")
    ap.add_argument("--order-id")
    ap.add_argument("--config", default=None, help="RNS config dir")
    ap.add_argument("--timeout", type=float, default=90)
    args = ap.parse_args()

    aspect = ".".join(protocol.ASPECTS)
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
    ok, resp = meshapi_client.call(args.dest, protocol.APP_NAME, aspect,
                                   protocol.PATH, body, config=args.config,
                                   timeout=args.timeout, identify=identify)
    print(json.dumps(resp, indent=2))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
