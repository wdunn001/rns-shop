"""Merchant admin portal -- a server-side web dashboard over the SAME
`_store`/`_catalog`/`_rails` objects shopd already holds in memory (no
separate DB connection, no separate container -- same "bolt a second HTTP
surface onto the existing process" pattern beacon.web uses for its
analytics dashboard). Wraps the exact operations shopctl (admin.py) already
offers (list/show/mark-paid/entitle) behind a browser UI instead of
docker-exec-a-CLI.

PRIVATE BY DESIGN: binds 0.0.0.0 (unlike the loopback-only /order local
API) because it's meant to be reached through the Caddy edge on .229:PORT,
Authentik-gated -- see the `rns-shop-admin.quasarke.net` vhost (mirrors
beacon's `rns-analytics.quasarke.net`: internal-only, `import authentik
192.168.1.229:PORT`, mario-ca TLS). This module implements NO auth itself;
Caddy's forward-auth is the only thing standing between this port and order
data (identities, shipping addresses, totals) -- it must never be exposed
any other way (no public vhost, no raw port-forward -- see
no-raw-public-port-exposure memory, the same lesson the buyer-facing demo
payment link just needed relearning)."""
import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

A, G, W, D, BG = "#5cf", "#6d8", "#ec7", "#89a", "#124"
STATE_COLOR = {"submitted": W, "awaiting_payment": W, "paid": G,
               "fulfilled": G, "cancelled": "#e66", "expired": "#e66"}

_CSS = f"""
:root{{--bg:#0a0e14;--fg:#cde;--accent:{A};--good:{G};--warn:{W};--dim:{D};--band:{BG};--card:#111722;--line:#1c2733}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-monospace,Menlo,Consolas,monospace;padding:22px;max-width:1200px;margin:0 auto}}
h1{{font-size:1.3rem;margin:0 0 2px;color:var(--accent)}} .sub{{color:var(--dim);margin:0 0 20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:26px}}
.c{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}}
.n{{font-size:1.8rem;font-weight:700}} .l{{color:var(--dim);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;margin-top:4px}}
h2{{font-size:.8rem;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);border-bottom:1px solid var(--line);padding-bottom:6px;margin:28px 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:.85rem}}
td,th{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left}} th{{color:var(--dim);font-weight:400;font-size:.72rem;text-transform:uppercase}}
a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
.panel{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:20px}}
.pill{{display:inline-block;padding:1px 8px;border-radius:10px;font-size:.72rem}}
button{{font:inherit;background:var(--card);color:var(--fg);border:1px solid var(--line);border-radius:6px;
padding:5px 10px;cursor:pointer}} button:hover{{border-color:var(--accent)}}
button.warn{{border-color:var(--warn);color:var(--warn)}} button.good{{border-color:var(--good);color:var(--good)}}
form{{display:inline}} .row{{display:flex;gap:8px;align-items:center}}
"""


def _esc(x):
    return html.escape(str(x))


def _pill(status):
    color = STATE_COLOR.get(status, D)
    return f'<span class=pill style="background:{color}22;color:{color}">{_esc(status)}</span>'


def _dashboard_html(store, catalog, rails):
    orders = store.orders_all()
    total_rev = sum(o["total"] for o in orders if o["status"] in ("paid", "fulfilled"))
    by_status = {}
    for o in orders:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1
    item_counts = {}
    for o in orders:
        for e in o["items"]:
            item_counts[e["sku"]] = item_counts.get(e["sku"], 0) + e["qty"]
    top_items = sorted(item_counts.items(), key=lambda kv: -kv[1])[:10]

    cards = "".join(
        f'<div class=c><div class=n>{_esc(v)}</div><div class=l>{_esc(k)}</div></div>'
        for k, v in [("orders", len(orders)), ("revenue (paid+)", f"{total_rev:.2f}"),
                     ("catalog items", len(catalog.items)),
                     *[(f"{s}", n) for s, n in sorted(by_status.items())]])

    rows = "".join(
        f"""<tr>
<td><a href="/order?id={_esc(o['order_id'])}">#{_esc(o['order_id'])}</a></td>
<td>{_pill(o['status'])}</td>
<td>{time.strftime('%Y-%m-%d %H:%M', time.gmtime(o['created']))}</td>
<td>{_esc(o['identity'][:12])}…</td>
<td>{_esc(', '.join(f"{e['qty']}x {e['sku']}" for e in o['items']))}</td>
<td style="text-align:right">{o['total']:.2f} {_esc(o['currency'])}</td>
</tr>""" for o in orders[:60])

    itemrows = "".join(f"<tr><td>{_esc(sku)}</td><td style='text-align:right'>{n}</td></tr>"
                       for sku, n in top_items)

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<meta http-equiv=refresh content=60><title>{_esc(catalog.shop.get('name', 'shop'))} -- admin</title>
<style>{_CSS}</style></head><body>
<h1>&#9881; {_esc(catalog.shop.get('name', 'shop'))} -- merchant admin</h1>
<p class=sub>private, Authentik-gated &middot; {len(orders)} orders total &middot; auto-refresh 60s</p>
<div class=grid>{cards}</div>

<h2>Recent orders (60 shown, newest first)</h2>
<div class=panel><table>
<tr><th>order</th><th>status</th><th>created</th><th>buyer</th><th>items</th><th style="text-align:right">total</th></tr>
{rows or '<tr><td colspan=6 style="color:var(--dim)">no orders yet</td></tr>'}
</table></div>

<h2>Top-selling SKUs (all time)</h2>
<div class=panel><table><tr><th>sku</th><th style="text-align:right">units</th></tr>
{itemrows or '<tr><td colspan=2 style="color:var(--dim)">no sales yet</td></tr>'}</table></div>
</body></html>"""


def _order_html(store, catalog, rails, order_id):
    o = store.order_admin_get(order_id)
    if not o:
        return None
    items_rows = "".join(
        f"<tr><td>{_esc(e['sku'])}</td><td>{_esc(catalog.items.get(e['sku'], {}).get('title', '?'))}</td>"
        f"<td style='text-align:right'>{e['qty']}</td></tr>" for e in o["items"])
    ship = o.get("shipping") or {}
    ship_lines = "<br>".join(_esc(ship.get(k, "")) for k in
                             ("name", "street", "street2", "city", "region", "postal", "country")
                             if ship.get(k))
    pay_text = "\n".join(_esc(l) for l in (o.get("pay_text") or "").splitlines())

    actions = []
    if o["status"] in ("submitted", "awaiting_payment"):
        actions.append(f'<form method=POST action="/order/{_esc(order_id)}/mark-paid">'
                       f'<button class=good>mark paid</button></form>')
    if o["status"] not in ("cancelled",):
        actions.append(f'<form method=POST action="/order/{_esc(order_id)}/cancel">'
                       f'<button class=warn onclick="return confirm(\'Cancel this order?\')">cancel</button></form>')
    entitle_form = "".join(
        f'''<form method=POST action="/order/{_esc(order_id)}/entitle" class=row style="margin-top:8px">
<input type=hidden name=sku value="{_esc(e['sku'])}">
<button>manually entitle {_esc(e['sku'])}</button></form>'''
        for e in o["items"] if catalog.items.get(e["sku"], {}).get("kind") != "physical")

    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Order #{_esc(order_id)} -- admin</title><style>{_CSS}</style></head><body>
<p><a href="/">&larr; all orders</a></p>
<h1>Order #{_esc(order_id)}  {_pill(o['status'])}</h1>
<p class=sub>{time.strftime('%Y-%m-%d %H:%M', time.gmtime(o['created']))} UTC &middot; buyer {_esc(o['identity'])}</p>

<div class=panel>
<div class=row style="margin-bottom:10px">{' '.join(actions)}</div>
<table><tr><th>sku</th><th>title</th><th style="text-align:right">qty</th></tr>{items_rows}</table>
<p style="margin-top:10px">subtotal <b>{(o.get('subtotal') or o['total']):.2f}</b>
{f" + shipping <b>{o['shipping_fee']:.2f}</b>" if o.get('shipping_fee') else ""}
= total <b>{o['total']:.2f} {_esc(o['currency'])}</b></p>
</div>

<h2>Shipping</h2>
<div class=panel>{ship_lines or '<span style="color:var(--dim)">none on file (digital/service order)</span>'}</div>

<h2>Note from buyer</h2>
<div class=panel>{_esc(o.get('note') or '(none)')}</div>

<h2>Payment instructions sent</h2>
<div class=panel><pre style="white-space:pre-wrap;margin:0;font:inherit">{pay_text or '(none yet)'}</pre></div>

{f'<h2>Manual entitlement (digital/service items)</h2><div class=panel>{entitle_form}</div>' if entitle_form else ''}
</body></html>"""


def start(store, catalog_ref, rails_ref, port):
    """catalog_ref/rails_ref: zero-arg callables returning the CURRENT
    _catalog/_rails (server.py's catalog can be replaced wholesale on a
    reload -- see _health_loop -- so this always reads the live reference,
    never a stale one captured at start() time)."""
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _html(self, code, s):
            body = s.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, loc):
            self.send_response(303)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            parsed = urlsplit(self.path)
            route = parsed.path.rstrip("/") or "/"
            qs = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            try:
                with lock:
                    if route == "/healthz":
                        self._json(200, {"ok": True, "orders": len(store.orders_all())})
                    elif route == "/order" and qs.get("id"):
                        page = _order_html(store, catalog_ref(), rails_ref(), qs["id"])
                        if page is None:
                            self._json(404, {"ok": False, "err": "not_found"})
                        else:
                            self._html(200, page)
                    elif route == "/":
                        self._html(200, _dashboard_html(store, catalog_ref(), rails_ref()))
                    else:
                        self._json(404, {"ok": False, "err": "not_found"})
            except Exception as e:  # noqa: BLE001
                self._json(503, {"ok": False, "err": str(e)})

        def do_POST(self):
            parsed = urlsplit(self.path)
            parts = parsed.path.strip("/").split("/")
            # /order/<id>/<action>
            if len(parts) != 3 or parts[0] != "order":
                self._json(404, {"ok": False, "err": "not_found"})
                return
            order_id, action = parts[1], parts[2]
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(n) if n else b""
                form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
            except Exception:
                form = {}
            try:
                with lock:
                    o = store.order_admin_get(order_id)
                    if not o:
                        self._json(404, {"ok": False, "err": "not_found"})
                        return
                    if action == "mark-paid":
                        store.order_set_status(order_id, "paid")
                    elif action == "cancel":
                        store.order_set_status(order_id, "cancelled")
                    elif action == "entitle":
                        sku = form.get("sku", "")
                        if sku:
                            store.entitle(o["identity"], sku)
                    else:
                        self._json(404, {"ok": False, "err": "unknown_action"})
                        return
                self._redirect(f"/order?id={order_id}")
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "err": str(e)})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
