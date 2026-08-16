"""Webhook bridge: the clearnet edge posts here when a payment lands; we flip
the order to paid. The LXMF worker (in shopd, same DB) handles entitlement +
receipt from there.

    python3 -m rns_shop.webhook_bridge --db /data/shop.db --port 8218 \
        --secret-file /data/webhook_secret

POST /paid  {"order_id": "..."}  with header  X-Shop-Secret: <secret>
  -> production path: your Stripe/PayPal webhook adapter calls this.

DEMO MODE (SHOP_DEMO=1): the bridge also SERVES a fake processor —
GET  /demo/<order_id>       a card-checkout page a buyer opens in any browser
POST /demo/<order_id>/pay   "payment succeeded" -> order flips paid (no secret;
                            demo only — never enable on a real shop)
This exercises the ENTIRE link rail (checkout URL -> web pay -> settle ->
LXMF receipt -> delivery) with the processor spoofed."""
import argparse
import html
import json
import os
import re
import secrets as pysecrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import Store

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>demo checkout</title><style>
body{{background:#0e1310;color:#dde6df;font-family:system-ui;display:grid;
place-items:center;min-height:100vh;margin:0}}
.card{{background:#151b17;border:1px solid #26302a;border-radius:16px;
padding:32px 36px;max-width:420px;text-align:center}}
.badge{{font-size:11px;letter-spacing:.14em;color:#d9a05b;border:1px solid
#d9a05b55;border-radius:99px;padding:4px 12px;display:inline-block}}
h1{{font-size:20px;margin:14px 0 4px}} .amt{{font-size:34px;color:#4ade80;
font-weight:700;margin:12px 0}} .dim{{color:#8b988f;font-size:13px}}
button{{background:#4ade80;color:#0e1310;font-size:16px;font-weight:700;
border:0;border-radius:10px;padding:14px 38px;margin-top:18px;cursor:pointer}}
code{{color:#5ccfff}}</style></head><body><div class="card">
<span class="badge">DEMO PROCESSOR — NO REAL MONEY</span>
{body}</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--port", type=int, default=8218)
    ap.add_argument("--secret-file", required=True)
    args = ap.parse_args()

    if not os.path.isfile(args.secret_file):
        with open(args.secret_file, "w") as fh:
            fh.write(pysecrets.token_hex(24))
        os.chmod(args.secret_file, 0o600)
        print(f"generated webhook secret at {args.secret_file}")
    secret = open(args.secret_file).read().strip()
    store = Store(args.db)
    demo = os.environ.get("SHOP_DEMO") == "1"

    def _order(oid):
        return store.order_admin_get(oid) if re.fullmatch(r"[0-9a-f]{8}", oid or "") else None

    class H(BaseHTTPRequestHandler):
        def _html(self, code, body):
            out = _PAGE.format(body=body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            m = re.fullmatch(r"/demo/([0-9a-f]{8})", self.path)
            if demo and m:
                o = _order(m.group(1))
                if not o:
                    return self._html(404, "<h1>Unknown order</h1>")
                items = ", ".join(f"{e['qty']}× {html.escape(e['sku'])}"
                                  for e in o["items"])
                if o["status"] in ("paid", "fulfilled"):
                    return self._html(200, f"<h1>Already paid ✓</h1>"
                                      f"<p class=dim>order <code>{o['order_id']}</code></p>")
                return self._html(200, f"""
<h1>Pay for order <code>{o['order_id']}</code></h1>
<p class=dim>{items}</p><div class=amt>{o['total']:.2f} {html.escape(o['currency'])}</div>
<form method=post action="/demo/{o['order_id']}/pay">
<button>PAY {o['total']:.2f} {html.escape(o['currency'])}</button></form>
<p class=dim>This simulates a card processor. On PAY, the webhook fires,
the order settles, and your receipt + goods travel back over the mesh.</p>""")
            self.send_response(404); self.end_headers()

        def do_POST(self):
            m = re.fullmatch(r"/demo/([0-9a-f]{8})/pay", self.path)
            if demo and m:
                o = _order(m.group(1))
                if not o:
                    return self._html(404, "<h1>Unknown order</h1>")
                store.order_set_status(o["order_id"], "paid")
                return self._html(200, f"""
<h1>Payment complete ✓</h1><div class=amt>{o['total']:.2f} {html.escape(o['currency'])}</div>
<p class=dim>Order <code>{o['order_id']}</code> is settled. Back on the mesh:
your LXMF receipt is on its way, digital goods are unlocked on
<b>my orders</b>. You can close this tab.</p>""")
            if self.path != "/paid":
                self.send_response(404); self.end_headers(); return
            if self.headers.get("X-Shop-Secret") != secret:
                self.send_response(403); self.end_headers(); return
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))))
                oid = str(body["order_id"])
            except Exception:
                self.send_response(400); self.end_headers(); return
            store.order_set_status(oid, "paid")
            out = json.dumps({"ok": True, "order_id": oid,
                              "status": "paid"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    print(f"webhook bridge on :{args.port} (POST /paid"
          f"{', DEMO checkout at /demo/<order_id>' if demo else ''})")
    ThreadingHTTPServer(("0.0.0.0", args.port), H).serve_forever()


if __name__ == "__main__":
    main()
