#!/usr/bin/env python3
"""BUY NOW — NomadNet executable checkout page.

NomadNet hands us the buyer's cryptographically-proven identity
(remote_identity) plus the link/form data (var_sku, field_qty). We place the
order against stalld's loopback checkout API and render the confirmation.
No CLI, no accounts — click, buy, done."""
import json
import os
import urllib.request

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("STALL_LOCAL_API_URL", "http://127.0.0.1:8219")

identity = os.environ.get("remote_identity", "")
sku = (os.environ.get("var_sku") or os.environ.get("field_sku") or "").strip()
try:
    qty = max(1, min(999, int(os.environ.get("field_qty", "1") or 1)))
except ValueError:
    qty = 1
note = (os.environ.get("field_note") or "")[:500]


def esc(s):
    return str(s).replace("`", "'").replace("\n", " ").strip()


def page(body):
    print(f"""`c
`B{BG}`F{A}  `!CHECKOUT`!  `f`b
`a
{body}

`[← back to the catalog`:/page/index.mu]  ·  `[my orders`:/page/orders.mu]""")


if not identity:
    page(f"""`F{W}┌─`f `!identify to buy`!
`F{W}│`f  Your client didn't identify on this link — the shop needs your RNS
`F{W}│`f  identity (that IS your account; no signup exists).
`F{W}└─`f  In NomadNet/MeshChat: allow identifying to this node, then buy again.""")
    raise SystemExit(0)

if not sku:
    page(f"`F{W}No item selected.`f Pick something from the catalog first.")
    raise SystemExit(0)

try:
    req = urllib.request.Request(
        API + "/order",
        data=json.dumps({"identity": identity, "note": note,
                         "items": [{"sku": sku, "qty": qty}]}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        out = json.loads(r.read())
except Exception:
    out = {"ok": False, "err": "shop backend unreachable"}

if not out.get("ok"):
    page(f"""`F{W}┌─`f `!that didn't work`!
`F{W}│`f  {esc(out.get('err', 'unknown error'))}
`F{W}└─`f  try again in a moment — or ask the merchant.""")
else:
    page(f"""`F{G}┌─`f `!ORDER PLACED`!  `F{D}#{esc(out['order_id'])}`f
`F{G}│`f
`F{G}│`f  {qty}× `!{esc(sku)}`!  —  `!`F{G}{out['total']:.2f} {esc(out['currency'])}`f`!
`F{G}│`f
`F{G}│`f  `F{D}{esc(out.get('invoice_note', 'Invoice follows.'))}`f
`F{G}│`f  `F{D}Confirmation + receipt arrive by LXMF. Track it on`f `[my orders`:/page/orders.mu]`F{D}.`f
`F{G}└─`f  `F{D}Your account = your identity: `f`F{A}{identity}`f""")
