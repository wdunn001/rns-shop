#!/usr/bin/env python3
"""ADD TO CART: invoked via a per-sku baked wrapper (var_sku set by
write_pages -- /page/cart/add/<SKU>.mu -- same "bake the sku into the file
path" trick buy.mu's per-item checkout uses, so no click here ever needs a
literal link-var). field_qty comes from the item page's quantity stepper
when present (the "+" cart-view link omits it -> defaults to 1)."""
import json
import os
import urllib.error
import urllib.request

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("SHOP_LOCAL_API_URL", "http://127.0.0.1:8219")

identity = os.environ.get("remote_identity", "")
sku = (os.environ.get("var_sku") or "").strip()
try:
    qty = max(1, min(999, int(os.environ.get("field_qty", "1") or 1)))
except ValueError:
    qty = 1


def esc(s):
    return str(s).replace("`", "'").strip()


print(f"`c\n`B{BG}`F{A}  `!ADD TO CART`!  `f`b\n`a")

if not identity:
    print(f"`F{W}Your client didn't identify on this link`f. Identify to this "
          f"node and reload, then try again.")
    print(f"\n`[<- catalog`:/page/index.mu]")
    raise SystemExit(0)

try:
    req = urllib.request.Request(
        API + "/cart/add", data=json.dumps({"identity": identity, "sku": sku, "qty": qty}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        out = json.loads(r.read())
except urllib.error.HTTPError as e:
    try:
        out = json.loads(e.read())
    except Exception:
        out = {"ok": False, "err": f"http {e.code}"}
except Exception as ex:
    out = {"ok": False, "err": str(ex)}

if out.get("ok"):
    n = sum(e["qty"] for e in out.get("items", []))
    print(f"`F{G}Added {qty}x {esc(sku)} to your cart.`f  "
          f"`F{D}({n} item(s) total in cart)`f")
else:
    print(f"`F{W}Couldn't add to cart: {esc(out.get('err', 'unknown'))}`f")

print(f"\n`[<- keep shopping`:/page/index.mu]  ·  `[view cart`:/page/cart.mu]")
