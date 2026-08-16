#!/usr/bin/env python3
"""MY ACCOUNT — the buyer's saved details, keyed on their proven identity.
View shipping/preferences; updating happens at checkout ('remember me')."""
import json
import os
import urllib.request

A, G, W, D, BG = "5cf", "6d8", "ec7", "89a", "124"
API = os.environ.get("SHOP_LOCAL_API_URL", "http://127.0.0.1:8219")

identity = os.environ.get("remote_identity", "")


def esc(s):
    return str(s).replace("`", "'").strip()


print(f"`c\n`B{BG}`F{A}  `!MY ACCOUNT`!  `f`b\n`a")

if not identity:
    print(f"`F{W}Your client didn't identify on this link`f — identify to this "
          f"node and reload.")
    print(f"\n`[← catalog`:/page/index.mu]")
    raise SystemExit(0)

try:
    with urllib.request.urlopen(f"{API}/profile?identity={identity}",
                                timeout=8) as r:
        prof = json.loads(r.read()).get("profile", {})
except Exception:
    prof = {}

ship = prof.get("shipping", {}) or {}
first = (ship.get("name") or "").split(" ")[0]
hello = f"hi, `!{esc(first)}`! — " if first else ""
print(f"`F{A}{hello}`fyour account is your RNS identity:")
print(f"`c`B{BG}`F{A}  {identity}  `f`b\n`a")

if ship:
    lines = [ship.get(k, "") for k in
             ("name", "street", "street2", "city", "region", "postal", "country")]
    lines = [esc(x) for x in lines if x]
    addr = "\n".join(f"`F{A}│`f  {x}" for x in lines)
    print(f"`F{A}┌─`f `!SAVED SHIPPING`!\n{addr}\n"
          f"`F{A}└─`f `F{D}update it at any checkout with 'remember me' ticked`f")
else:
    print(f"`F{D}No saved shipping yet — tick 'remember me' at checkout and "
          f"your details prefill forever after.`f")

if prof.get("pay_method"):
    print(f"\npreferred payment: `F{G}{esc(prof['pay_method'])}`f "
          f"`F{D}(listed first on your orders)`f")

print(f"""
`F{D}What we store: this identity hash, the shipping details above, and your
orders. Nothing else — no passwords exist to leak.`f

`[← catalog`:/page/index.mu]  ·  `[my orders`:/page/orders.mu]""")
