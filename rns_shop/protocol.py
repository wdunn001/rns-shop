"""rns-shop wire protocol: envelope, op names, pack/unpack helpers."""
import umsgpack

VERSION = 1
APP_NAME = "rnshop"
ASPECTS = ("shop",)
PATH = "/shop"
MANIFEST_OP = "__manifest__"

OP_CATALOG_LIST = "catalog.list"
OP_CATALOG_GET = "catalog.get"
OP_CART_GET = "cart.get"
OP_CART_SET = "cart.set"
OP_ORDER_SUBMIT = "order.submit"
OP_ORDER_STATUS = "order.status"
OP_ENTITLEMENT = "entitlement.check"
OP_DELIVERY = "delivery.get"
OP_PAY_LINK = "pay.link"
OP_PAY_XMR = "pay.xmr"

OPS = (OP_CATALOG_LIST, OP_CATALOG_GET, OP_CART_GET, OP_CART_SET,
       OP_ORDER_SUBMIT, OP_ORDER_STATUS, OP_ENTITLEMENT,
       OP_DELIVERY, OP_PAY_LINK, OP_PAY_XMR)

# ops that require an identified link (link.identify() client-side)
IDENTIFIED_OPS = (OP_CART_GET, OP_CART_SET, OP_ORDER_SUBMIT,
                  OP_ORDER_STATUS, OP_ENTITLEMENT,
                  OP_DELIVERY, OP_PAY_LINK, OP_PAY_XMR)

ORDER_STATES = ("submitted", "awaiting_payment", "paid", "fulfilled",
                "cancelled", "expired")


def pack(obj):
    return umsgpack.packb(obj)


def unpack(data):
    return umsgpack.unpackb(data)


def ok(payload, req=None):
    out = {"v": VERSION, "ok": True}
    if req and req.get("op"):
        out["op"] = req["op"]
    out.update(payload)
    return out


def err(code, req=None, detail=None):
    out = {"v": VERSION, "ok": False, "err": code}
    if req and req.get("op"):
        out["op"] = req["op"]
    if detail:
        out["detail"] = str(detail)[:200]
    return out
