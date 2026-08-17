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
        if first:
            greeting = (f"`c`F{A}hi, `!{first}`! — welcome back`f   "
                        f"`F{D}·`f  `[my orders`:/page/orders.mu]  "
                        f"`F{D}·`f  `[my account`:/page/account.mu]\n`a\n")
        else:
            greeting = (f"`c`F{D}welcome back`f   ·  "
                        f"`[my orders`:/page/orders.mu]  ·  "
                        f"`[my account`:/page/account.mu]\n`a\n")
    except Exception:
        pass

try:
    body = open(os.path.join(HERE, "index_body.mu"), encoding="utf-8").read()
except Exception:
    body = "(storefront body missing — re-render pending)"
print(greeting + body)
