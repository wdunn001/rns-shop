#!/usr/bin/env python3
"""MY ORDERS: NomadNet executable page: the buyer's order history, keyed on
their proven identity. Digital goods they own are delivered right here.
Each order also gets a "reorder" action: a hidden text field prefilled with
THAT order's id, submitted by name (bare-name enumerated link, no literal
link-var), since order ids are only known at RUNTIME (unlike SKUs, they
can't be baked into a per-order wrapper file at catalog-render time the way
buy/<sku>.mu or cart/add/<sku>.mu are)."""
import json
import os
import time
import urllib.error
import urllib.request

try:                                    # Beacon-Analytics RUM page view (best-effort)
    from beaconrum import track as _rum
    _rum("rns-shop", "/page/orders.mu")
except Exception:
    pass

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("SHOP_LOCAL_API_URL", "http://127.0.0.1:8219")
STATE_COLOR = {"submitted": W, "awaiting_payment": W, "paid": G,
               "fulfilled": G, "cancelled": "e66", "expired": "e66"}

identity = os.environ.get("remote_identity", "")


def esc(s):
    return str(s).replace("`", "'").strip()


print(f"""`c
`B{BG}`F{A}  `!MY ORDERS`!  `f`b
`a""")

if not identity:
    print(f"""`F{W}Your client didn't identify on this link`f. Identify to this
node and reload; your RNS identity is your account.""")
    print(f"\n`[<- back to the catalog`:/page/index.mu]")
    raise SystemExit(0)


def api(path):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        return json.loads(r.read())


def api_post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"ok": False, "err": f"http {e.code}"}
    except Exception as ex:
        return {"ok": False, "err": str(ex)}


try:
    orders = api(f"/orders?identity={identity}").get("orders", [])
except Exception:
    print(f"`F{W}shop backend unreachable, try again in a moment`f")
    raise SystemExit(0)

# Reorder: exactly one `field_reorder_<oid>` is set per click (the CONFIRM
# link for each order only enumerates that order's own field), value ==
# the order id it was prefilled with.
reorder_msg = None
for o in orders:
    key = "field_reorder_" + o["order_id"]
    if os.environ.get(key, "").strip() == o["order_id"]:
        out = api_post("/cart/reorder", {"identity": identity, "order_id": o["order_id"]})
        if out.get("ok"):
            n = sum(e["qty"] for e in out.get("items", []))
            skip = out.get("skipped", 0)
            reorder_msg = (f"`F{G}Added order #{esc(o['order_id'])}'s items to your "
                           f"cart`f  `F{D}({n} item(s) in cart now"
                           + (f", {skip} no longer available" if skip else "") + f")`f  "
                           f"`[-> view cart`:/page/cart.mu]")
        else:
            reorder_msg = f"`F{W}Couldn't reorder: {esc(out.get('err', 'unknown'))}`f"
        break

if reorder_msg:
    print(reorder_msg + "\n")

if not orders:
    print(f"`F{D}No orders yet.`f Go find something: `[catalog`:/page/index.mu]")
else:
    for o in orders:
        color = STATE_COLOR.get(o["status"], D)
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(o["created"]))
        items = ", ".join(f"{e['qty']}x {esc(e['sku'])}" for e in o["items"])
        print(f"""`F{A}┌─`f `!#{esc(o['order_id'])}`!  `F{color}{esc(o['status'])}`f  `F{D}{when} UTC`f
`F{A}│`f  {esc(items)}:  `F{G}{o['total']:.2f} {esc(o['currency'])}`f""")
        if o["status"] in ("submitted", "awaiting_payment"):
            try:
                pay = api(f"/payment?identity={identity}&order_id={o['order_id']}"
                          ).get("payment")
            except Exception:
                pay = None
            if pay:
                print(f"`F{A}│`f  `F{W}HOW TO PAY (any of):`f")
                for line in (pay.get("text") or "").splitlines():
                    print(f"`F{A}│`f    `F{D}{esc(line)}`f")
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
                        print(f"`F{A}│`f  `!{esc(d['filename'])}`! "
                              f"`F{D}({d['bytes']} bytes, delivered below)`f\n{body}")
                    else:
                        print(f"`F{A}│`f  `!{esc(d['filename'])}`! "
                              f"`F{D}({d['bytes']} bytes, fetch with delivery.get "
                              f"over the shop service)`f")
        oid = esc(o["order_id"])
        print(f"""`F{A}│`f  `B{BG}`<10|reorder_{oid}`{oid}>`b `!`F{A}`[reorder`:/page/orders.mu`reorder_{oid}]`f`!
`F{A}└─`f""")

print(f"\n`[<- back to the catalog`:/page/index.mu]  ·  `[my cart`:/page/cart.mu]")
