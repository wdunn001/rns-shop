#!/usr/bin/env python3
"""MY ORDERS — NomadNet executable page: the buyer's order history, keyed on
their proven identity. Digital goods they own are delivered right here."""
import json
import os
import time
import urllib.request

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("STALL_LOCAL_API_URL", "http://127.0.0.1:8219")
STATE_COLOR = {"submitted": W, "awaiting_payment": W, "paid": G,
               "fulfilled": G, "cancelled": "e66", "expired": "e66"}

identity = os.environ.get("remote_identity", "")


def esc(s):
    return str(s).replace("`", "'").strip()


print(f"""`c
`B{BG}`F{A}  `!MY ORDERS`!  `f`b
`a""")

if not identity:
    print(f"""`F{W}Your client didn't identify on this link`f — identify to this
node and reload; your RNS identity is your account.""")
    print(f"\n`[← back to the catalog`:/page/index.mu]")
    raise SystemExit(0)


def api(path):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.loads(r.read())


try:
    orders = api(f"/orders?identity={identity}").get("orders", [])
except Exception:
    print(f"`F{W}shop backend unreachable — try again in a moment`f")
    raise SystemExit(0)

if not orders:
    print(f"`F{D}No orders yet.`f Go find something: `[catalog`:/page/index.mu]")
else:
    for o in orders:
        color = STATE_COLOR.get(o["status"], D)
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(o["created"]))
        items = ", ".join(f"{e['qty']}x {esc(e['sku'])}" for e in o["items"])
        print(f"""`F{A}┌─`f `!#{esc(o['order_id'])}`!  `F{color}{esc(o['status'])}`f  `F{D}{when} UTC`f
`F{A}│`f  {esc(items)}  —  `F{G}{o['total']:.2f} {esc(o['currency'])}`f""")
        # deliver owned digital goods inline
        for e in o["items"]:
            if o["status"] in ("paid", "fulfilled"):
                try:
                    d = api(f"/deliver?identity={identity}&sku={e['sku']}")
                except Exception:
                    d = {"ok": False}
                if d.get("ok"):
                    if d.get("text"):
                        body = "\n".join(
                            f"`F{A}│`f  `F{D}{esc(l)}`f"
                            for l in d["text"].splitlines()[:20])
                        print(f"`F{A}│`f  `!⬇ {esc(d['filename'])}`! "
                              f"`F{D}({d['bytes']} bytes — delivered below)`f\n{body}")
                    else:
                        print(f"`F{A}│`f  `!⬇ {esc(d['filename'])}`! "
                              f"`F{D}({d['bytes']} bytes — fetch with delivery.get "
                              f"over the shop service)`f")
        print(f"`F{A}└─`f")

print(f"\n`[← back to the catalog`:/page/index.mu]")
