#!/usr/bin/env python3
"""Storefront index — thin exec wrapper over the static body so identified
visitors get greeted by name while crawlers/anonymous visitors get the plain
page (same content, same MeshData, still cache-friendly)."""
import json
import os
import urllib.request

try:                                    # Beacon-Analytics RUM page view (best-effort)
    from beaconrum import track as _rum
    _rum("rns-shop", "/page/index.mu")
except Exception:
    pass

A, D, BG = "5cf", "89a", "124"
API = os.environ.get("SHOP_LOCAL_API_URL", "http://127.0.0.1:8219")
HERE = os.path.dirname(os.path.abspath(__file__))

identity = os.environ.get("remote_identity", "")
greeting = ""
if identity and len(identity) == 32:
    try:
        with urllib.request.urlopen(
                f"{API}/profile?identity={identity}", timeout=5) as r:
            prof = json.loads(r.read()).get("profile", {})
        first = (prof.get("shipping", {}).get("name") or "").split(" ")[0]
        first = first.replace("`", "'").strip()
        cart_n = 0
        try:
            with urllib.request.urlopen(
                    f"{API}/cart?identity={identity}", timeout=5) as r:
                cart_n = sum(e["qty"] for e in json.loads(r.read()).get("items", []))
        except Exception:
            pass
        cart_link = (f"`[my cart ({cart_n})`:/page/cart.mu]" if cart_n
                    else "`[my cart`:/page/cart.mu]")
        who = f"hi, `!{first}`! — welcome back" if first else "welcome back"
        greeting = (f"`c`F{A}{who}`f   `F{D}·`f  `[my orders`:/page/orders.mu]  "
                    f"`F{D}·`f  {cart_link}  `F{D}·`f  `[my account`:/page/account.mu]\n`a\n")
    except Exception:
        pass

try:
    body = open(os.path.join(HERE, "index_body.mu"), encoding="utf-8").read()
except Exception:
    body = "(storefront body missing — re-render pending)"
print(greeting + body)
