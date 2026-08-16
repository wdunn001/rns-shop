"""The rns-stall MeshAPI manifest (adopter #3, after rns-geo and rns-time)."""
from meshapi import schema

from . import protocol


def build(dest_hex, shop_name):
    return schema.build_manifest(
        service={
            "name": f"rns-stall — {shop_name}",
            "summary": "Self-hosted commerce over Reticulum: catalog, carts, "
                       "orders, entitlements. Buyer identity = RNS identity.",
            "app": protocol.APP_NAME,
            "aspect": ".".join(protocol.ASPECTS),
            "path": protocol.PATH,
            "dest": dest_hex,
            "encoding": "umsgpack",
            "source": "https://github.com/wdunn001/rns-stall",
        },
        ops=[
            {"op": protocol.OP_CATALOG_LIST, "summary": "List items (optionally by tag)",
             "request": {"tag": "str"}, "response": {"items": "[item]"}},
            {"op": protocol.OP_CATALOG_GET, "summary": "Full detail for one item",
             "request": {"sku": "str!"}, "response": {"item": "item"}},
            {"op": protocol.OP_CART_GET, "summary": "Your cart (identity-keyed)",
             "auth": "identified", "request": {}, "response": {"items": "[{sku,qty}]"}},
            {"op": protocol.OP_CART_SET, "summary": "Replace your cart",
             "auth": "identified",
             "request": {"items": {"type": "[{sku,qty}]!", "desc": "list of {sku, qty}"}},
             "response": {"items": "[{sku,qty}]"}},
            {"op": protocol.OP_ORDER_SUBMIT,
             "summary": "Submit an order (from your cart, or pass items directly)",
             "auth": "identified",
             "request": {"items": "[{sku,qty}]", "shipping": "str", "note": "str"},
             "response": {"order_id": "str", "total": "float", "currency": "str",
                          "payment_options": "[str]"}},
            {"op": protocol.OP_ORDER_STATUS, "summary": "Check one of your orders",
             "auth": "identified", "request": {"order_id": "str!"},
             "response": {"order": "order"}},
            {"op": protocol.OP_ENTITLEMENT,
             "summary": "Do you own this SKU? (digital goods / services)",
             "auth": "identified", "request": {"sku": "str!"},
             "response": {"entitled": "bool"}},
        ],
    )
