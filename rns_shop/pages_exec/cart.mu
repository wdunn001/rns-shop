#!/usr/bin/env python3
"""MY CART: multi-item cart view + checkout. Line qty is adjusted via the
per-sku +/- wrappers (cart/add/<sku>.mu, cart/dec/<sku>.mu) and removal via
cart/remove/<sku>.mu (see render.py's write_pages). Same "bake the sku into
the file path" trick buy.mu's per-item checkout already uses, so no click
here ever needs a literal link-var. Checkout re-uses buy.mu's two-step
address-form pattern (CONFIRM enumerates bare field names + a step marker;
pay-AFTER-ordering, no method picker, see buy.mu's own docstring for why)."""
import json
import os
import urllib.error
import urllib.request

try:                                    # Beacon-Analytics RUM page view (best-effort)
    from beaconrum import track as _rum
    _rum("rns-shop", "/page/cart.mu")
except Exception:
    pass

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("SHOP_LOCAL_API_URL", "http://127.0.0.1:8219")

E = os.environ.get
identity = E("remote_identity", "")


def esc(s):
    return str(s).replace("`", "'").replace("\n", " ").strip()


def hdr(t="MY CART"):
    print(f"`c\n`B{BG}`F{A}  `!{t}`!  `f`b\n`a")


def foot():
    print(f"\n`[<- catalog`:/page/index.mu]  ·  `[my orders`:/page/orders.mu]")


def api_get(path):
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


hdr()
if not identity:
    print(f"""`F{W}┌─`f `!identify to see your cart`!
`F{W}│`f  Your client didn't identify on this link. The shop needs your RNS
`F{W}│`f  identity (that IS your account; nothing to register).
`F{W}└─`f  Allow identifying to this node in your client, then try again.""")
    foot(); raise SystemExit(0)

ADDR = ("name", "street", "street2", "city", "region", "postal", "country")
addr = {k: (E(f"field_{k}") or "").strip() for k in ADDR}
if not addr["name"]:
    addr["name"] = (E("field_fullname") or "").strip()   # 'name' is a reserved word in some clients
method = (E("field_method") or "").strip()
confirmed = (E("field_step") or "").strip() == "2"

# ---------- checkout step 2: place the order from whatever's in the cart ----------
if confirmed:
    body = {"identity": identity, "note": (E("field_note") or "")[:500],
            "method": method or None, "save_profile": (E("field_save") or "") != ""}
    body["shipping"] = {k: v for k, v in addr.items() if v}
    out = api_post("/cart/checkout", body)
    if out.get("ok"):
        opts = out.get("payments") or ([out["payment"]] if out.get("payment") else [])
        pay_lines = []
        for o in opts:
            pay_lines.append(f"`F{G}│`f  `F{A}{esc(o.get('label', o.get('method', '')))}`f")
            pay_lines.append(f"`F{G}│`f    `F{D}{esc(o.get('text', ''))}`f")
        pay_block = "\n".join(pay_lines) or f"`F{G}│`f  `F{D}(merchant will contact you)`f"
        totals = f"subtotal `!{out['subtotal']:.2f}`!"
        if out.get("shipping_fee"):
            totals += f"  +  shipping `!{out['shipping_fee']:.2f}`!"
        totals += f"  =  `!`F{G}{out['total']:.2f} {esc(out['currency'])}`f`!"
        print(f"""`F{G}┌─`f `!ORDER PLACED`!  `F{D}#{esc(out['order_id'])}`f
`F{G}│`f
`F{G}│`f  {totals}
`F{G}│`f
`F{G}│`f  `!PAY WHICHEVER WAY SUITS YOU`!
{pay_block}
`F{G}│`f
`F{G}│`f  `F{D}Same options arrive by LXMF; your receipt follows payment.`f
`F{G}└─`f  `F{D}Track it on`f `[my orders`:/page/orders.mu]""")
        foot(); raise SystemExit(0)
    else:
        err = out.get("err", "unknown")
        hint = {"empty_cart": "your cart is empty -- add something first",
                "address_required": "a physical item needs your full shipping "
                                    "address (name, street, city, postal, country)",
                "not_shipped_to_country": "sorry -- this shop doesn't ship to that "
                                          "country for one or more items in your cart",
                "bad_items": "one or more cart items didn't validate"}.get(err, err)
        print(f"""`F{W}┌─`f `!couldn't place the order`!
`F{W}│`f  {esc(hint)}
`F{W}└─`f  adjust below and confirm again.""")
        confirmed = False   # fall through to the cart view with what they typed

# ---------- cart view (+ checkout form if there's anything to buy) ----------
if not confirmed:
    try:
        cart = api_get(f"/cart?identity={identity}")
    except Exception:
        cart = {"ok": False}
    lines = cart.get("items", []) if cart.get("ok") else []

    if not lines:
        print(f"`F{D}Your cart is empty.`f  `[browse the catalog`:/page/index.mu]")
        foot(); raise SystemExit(0)

    print(f"`F{A}┌─`f `!{len(lines)} item(s)`!")
    for e in lines:
        av = e.get("availability", "")
        print(f"""`F{A}│`f  {e['qty']}x `!{esc(e['title'])}`!:  `F{G}{e['line_total']:.2f} {esc(e['currency'])}`f
`F{A}│`f    `!`F{A}`[+`:/page/cart/add/{e['sku']}.mu]`f`!  `!`F{A}`[-`:/page/cart/dec/{e['sku']}.mu]`f`!  `!`F{W}`[remove`:/page/cart/remove/{e['sku']}.mu]`f`!  `F{D}sku {esc(e['sku'])}`f""")
    print(f"`F{A}└─`f")

    subtotal = cart.get("subtotal", 0.0)
    fee = cart.get("shipping_fee_estimate")
    cur = cart.get("currency", "USD")
    est = f"`F{D}subtotal `!{subtotal:.2f}`!"
    if fee is not None:
        est += f"  ·  shipping (est.) `!{fee:.2f}`!  ·  total (est.) `!{subtotal + fee:.2f}`!"
    est += f" {esc(cur)}`f"
    print(f"\n{est}\n")

    # prefill from saved profile where the form is empty
    try:
        prof = api_get(f"/profile?identity={identity}").get("profile", {})
        saved = prof.get("shipping", {}) or {}
        if not method:
            method = prof.get("pay_method", "")
        for k in ADDR:
            if not addr[k]:
                addr[k] = saved.get(k, "")
    except Exception:
        pass

    print(f"""`F{A}┌─`f `!SHIPPING`!  `F{D}(only needed if your cart has a physical item)`f
`F{A}│`f  name     `B{BG}`<24|fullname`{esc(addr['name'])}>`b
`F{A}│`f  street   `B{BG}`<32|street`{esc(addr['street'])}>`b
`F{A}│`f  street 2 `B{BG}`<32|street2`{esc(addr['street2'])}>`b
`F{A}│`f  city     `B{BG}`<20|city`{esc(addr['city'])}>`b   region `B{BG}`<12|region`{esc(addr['region'])}>`b
`F{A}│`f  postal   `B{BG}`<12|postal`{esc(addr['postal'])}>`b   country (2-letter) `B{BG}`<4|country`{esc(addr['country'])}>`b
`F{A}└─`f  `F{D}saved to your profile if you tick remember`f

`F{A}┌─`f `!FINISH UP`!  `F{D}(pay after ordering)`f
`F{A}│`f  note `B{BG}`<24|note`>`b   remember me `<?|save|yes`>   `F{D}confirm code`f `B{BG}`<1|step`2>`b
`F{A}└─`f

`c
`!`[CHECKOUT`:/page/cart.mu`fullname|street|street2|city|region|postal|country|note|save|step]`!
`a""")

foot()
