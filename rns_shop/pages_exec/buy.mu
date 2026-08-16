#!/usr/bin/env python3
"""BUY NOW — NomadNet executable checkout.

Two-step flow inside one page:
  step 1 (no confirm flag): show the checkout form — qty carried in, address
    fields for physical items (prefilled from the buyer's saved profile),
    payment-method radios — with a CONFIRM link that submits every field back
    here (`*`).
  step 2 (field_confirm=yes): validate, place the order, save the profile if
    asked, show ORDER PLACED + the payment instruction.
Identity comes from NomadNet (remote_identity) — cryptographic, no accounts."""
import json
import os
import urllib.request

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("SHOP_LOCAL_API_URL", "http://127.0.0.1:8219")

E = os.environ.get
identity = E("remote_identity", "")
sku = (E("var_sku") or E("field_sku") or "").strip()
try:
    qty = max(1, min(999, int(E("field_qty", "1") or 1)))
except ValueError:
    qty = 1


def esc(s):
    return str(s).replace("`", "'").replace("\n", " ").strip()


def hdr(t="CHECKOUT"):
    print(f"`c\n`B{BG}`F{A}  `!{t}`!  `f`b\n`a")


def foot():
    print(f"\n`[← catalog`:/page/index.mu]  ·  `[my orders`:/page/orders.mu]")


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


hdr()
if not identity:
    print(f"""`F{W}┌─`f `!identify to buy`!
`F{W}│`f  Your client didn't identify on this link — the shop needs your RNS
`F{W}│`f  identity (that IS your account; nothing to register).
`F{W}└─`f  Allow identifying to this node in your client, then try again.""")
    foot(); raise SystemExit(0)

try:
    item = api_get(f"/item?sku={sku}")
    assert item.get("ok")
except Exception:
    print(f"`F{W}Item not found — pick something from the catalog.`f")
    foot(); raise SystemExit(0)

physical = item.get("kind", "physical") == "physical"
try:
    info = api_get("/shop_info")
    methods = info.get("methods", [{"method": "invoice", "label": "Invoice"}])
except Exception:
    methods = [{"method": "invoice", "label": "Invoice"}]

ADDR = ("name", "street", "street2", "city", "region", "postal", "country")
addr = {k: (E(f"field_{k}") or "").strip() for k in ADDR}
# 'name' is a reserved word in some clients' field handling (silently dropped)
# -> the page field is 'fullname'; map it back to the canonical key.
if not addr["name"]:
    addr["name"] = (E("field_fullname") or "").strip()
method = (E("field_method") or "").strip()
# step 2 iff the form was submitted — detected by the `step` marker text
# field (prefilled "2"). Payment method is NOT chosen at checkout: every
# enabled rail's instructions come back with the placed order (pay however
# you like; the rail that settles wins).
confirmed = (E("field_step") or "").strip() == "2"

# ---------- step 2: place the order ----------
if confirmed:
    body = {"identity": identity, "items": [{"sku": sku, "qty": qty}],
            "note": (E("field_note") or "")[:500],
            "method": method or None,
            "save_profile": (E("field_save") or "") != ""}
    if physical:
        body["shipping"] = {k: v for k, v in addr.items() if v}
    out = api_post("/order", body)
    if out.get("ok"):
        opts = out.get("payments") or ([out["payment"]] if out.get("payment") else [])
        pay_lines = []
        for o in opts:
            pay_lines.append(f"`F{G}│`f  `F{A}◆ {esc(o.get('label', o.get('method', '')))}`f")
            pay_lines.append(f"`F{G}│`f    `F{D}{esc(o.get('text', ''))}`f")
        pay_block = "\n".join(pay_lines) or f"`F{G}│`f  `F{D}(merchant will contact you)`f"
        print(f"""`F{G}┌─`f `!ORDER PLACED`!  `F{D}#{esc(out['order_id'])}`f
`F{G}│`f
`F{G}│`f  {qty}× `!{esc(item['title'])}`!  —  `!`F{G}{out['total']:.2f} {esc(out['currency'])}`f`!
`F{G}│`f
`F{G}│`f  `!PAY WHICHEVER WAY SUITS YOU`!
{pay_block}
`F{G}│`f
`F{G}│`f  `F{D}Same options arrive by LXMF; your receipt follows payment.`f
`F{G}└─`f  `F{D}Track it on`f `[my orders`:/page/orders.mu]""")
    else:
        err = out.get("err", "unknown")
        hint = {"address_required":
                "a physical item needs your full shipping address "
                "(name, street, city, postal, country)",
                "not_shipped_to_country":
                "sorry — this shop doesn't ship to that country",
                "bad_items": "item/quantity didn't validate"}.get(err, err)
        print(f"""`F{W}┌─`f `!couldn't place the order`!
`F{W}│`f  {esc(hint)}
`F{W}└─`f  adjust below and confirm again.""")
        confirmed = False  # fall through to the form with what they typed

# ---------- step 1: the checkout form ----------
if not confirmed:
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

    print(f"\n`!{esc(item['title'])}`!  —  `F{G}{item['price']:.2f} "
          f"{esc(item.get('currency', 'USD'))}`f   quantity `B{BG}`<3|qty`{qty}>`b\n")

    if physical:
        ships = ", ".join(item.get("ships_to", ["worldwide"])).lower()
        print(f"""`F{A}┌─`f `!SHIPPING`!  `F{D}(ships to: {esc(ships)})`f
`F{A}│`f  name     `B{BG}`<24|fullname`{esc(addr['name'])}>`b
`F{A}│`f  street   `B{BG}`<32|street`{esc(addr['street'])}>`b
`F{A}│`f  street 2 `B{BG}`<32|street2`{esc(addr['street2'])}>`b
`F{A}│`f  city     `B{BG}`<20|city`{esc(addr['city'])}>`b   region `B{BG}`<12|region`{esc(addr['region'])}>`b
`F{A}│`f  postal   `B{BG}`<12|postal`{esc(addr['postal'])}>`b   country (2-letter) `B{BG}`<4|country`{esc(addr['country'])}>`b
`F{A}└─`f  `F{D}saved to your profile if you tick remember`f""")

    pays = " · ".join(esc(m["label"]).split(" (")[0] for m in methods)
    # `step` is the submission marker (prefilled "2") — don't edit it.
    print(f"""`F{A}┌─`f `!FINISH UP`!  `F{D}(pay after ordering — accepted: {pays})`f
`F{A}│`f  note `B{BG}`<24|note`>`b   remember me `<?|save|yes`>   `F{D}confirm code`f `B{BG}`<1|step`2>`b
`F{A}└─`f""")
    link_fields = ["qty", "note", "save", "step"]
    if physical:
        link_fields += ["fullname"] + [k for k in ADDR if k != "name"]
    fields = "|".join(link_fields)
    print(f"""`c
`!`[▶ CONFIRM ORDER`:/page/buy/{sku}.mu`{fields}]`!
`a""")
foot()
