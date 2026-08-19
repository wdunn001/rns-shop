#!/usr/bin/env python3
"""REMOVE / DECREMENT a cart line — invoked via a per-sku baked wrapper
(var_sku + var_mode set by write_pages -- /page/cart/remove/<SKU>.mu for a
full removal, /page/cart/dec/<SKU>.mu for a -1 step -- same file-path-bakes-
the-argument trick as cart_add.mu / buy.mu's per-item checkout)."""
import json
import os
import urllib.error
import urllib.request

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("SHOP_LOCAL_API_URL", "http://127.0.0.1:8219")

identity = os.environ.get("remote_identity", "")
sku = (os.environ.get("var_sku") or "").strip()
mode = (os.environ.get("var_mode") or "all").strip()   # "all" | "dec"


def esc(s):
    return str(s).replace("`", "'").strip()


print(f"`c\n`B{BG}`F{A}  `!CART`!  `f`b\n`a")

if not identity:
    print(f"`F{W}Your client didn't identify on this link`f — identify to this "
          f"node and reload, then try again.")
    print(f"\n`[<- cart`:/page/cart.mu]")
    raise SystemExit(0)

body = {"identity": identity, "sku": sku}
if mode == "dec":
    body["qty"] = 1   # decrement by one; the API drops the line at 0
# mode == "all" -> omit qty entirely, which /cart/remove treats as "drop the whole line"

try:
    req = urllib.request.Request(
        API + "/cart/remove", data=json.dumps(body).encode(),
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
    verb = "Removed" if mode == "all" else "Updated"
    print(f"`F{G}{verb} {esc(sku)}.`f")
else:
    print(f"`F{W}Couldn't update your cart: {esc(out.get('err', 'unknown'))}`f")

print(f"\n`[<- back to cart`:/page/cart.mu]")
