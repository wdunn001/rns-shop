"""Merchant admin portal: a server-side web dashboard over the SAME
`_store`/`_catalog`/`_rails`/`_lxmf_worker` objects shopd already holds in
memory (no separate DB connection, no separate container, the same "bolt a
second HTTP surface onto the existing process" pattern beacon.web uses for
its analytics dashboard). Wraps the operations shopctl (admin.py) already
offered via docker-exec (list/show/mark-paid/entitle), adds a merchant->
buyer message action over the existing LXMF channel, and a catalog.yaml
item editor (add/edit/delete items, upload product photos). See
catalog_editor.py for why editing item fields IS editing the MeshData block
(there's no separate MeshData surface; it's rendered FROM these fields).

PRIVATE BY DESIGN: binds 0.0.0.0 (unlike the loopback-only /order local
API) because it's meant to be reached through the Caddy edge on .229:PORT,
Authentik-gated. See the `rns-shop-admin.quasarke.net` vhost (mirrors
beacon's `rns-analytics.quasarke.net`: internal-only, `import authentik
192.168.1.229:PORT`, mario-ca TLS). This module implements NO auth itself;
Caddy's forward-auth is the only thing standing between this port and order
data (identities, shipping addresses, totals) plus catalog-write access.
It must never be exposed any other way (no public vhost, no raw port-
forward, see no-raw-public-port-exposure memory, the same lesson the
buyer-facing demo payment link needed relearning once already)."""
import html
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import catalog_editor
from .catalog import AVAILABILITY, KINDS

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
input,select,textarea{{font:inherit;background:#0d131c;color:var(--fg);border:1px solid var(--line);
border-radius:5px;padding:5px 7px}} label{{display:inline-block;margin:4px 10px 4px 0}}
.formcard{{display:block}} .formcard form{{display:block}}
"""


def _esc(x):
    return html.escape(str(x))


def _pill(status):
    color = STATE_COLOR.get(status, D)
    return f'<span class=pill style="background:{color}22;color:{color}">{_esc(status)}</span>'


def _page(title, body, head_extra=""):
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
{head_extra}<title>{_esc(title)}</title><style>{_CSS}</style></head><body>{body}</body></html>"""


def _nav():
    return ('<p class=sub><a href="/">dashboard</a> &middot; '
            '<a href="/catalog">catalog</a></p>')


# ---------------------------------------------------------------------------
# Minimal multipart/form-data parser. stdlib's cgi.FieldStorage is
# deprecated (removed in 3.13); this is deliberately small, only handling
# what the catalog editor's one form (a handful of text fields + one
# optional file) actually needs. It does not handle general MIME.
# ---------------------------------------------------------------------------
def _parse_multipart(content_type, body):
    m = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not m:
        return {}, {}
    boundary = ("--" + m.group(1)).encode()
    fields, files = {}, {}
    for part in body.split(boundary)[1:-1]:
        part = part.strip(b"\r\n")
        if not part:
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        headers = header_blob.decode("utf-8", "replace")
        nm = re.search(r'name="([^"]+)"', headers)
        if not nm:
            continue
        name = nm.group(1)
        fn = re.search(r'filename="([^"]*)"', headers)
        if fn:
            if fn.group(1):
                files[name] = (fn.group(1), content)
        else:
            fields[name] = content.decode("utf-8", "replace")
    return fields, files


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
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
<td>{_esc(o['identity'][:12])}...</td>
<td>{_esc(', '.join(f"{e['qty']}x {e['sku']}" for e in o['items']))}</td>
<td style="text-align:right">{o['total']:.2f} {_esc(o['currency'])}</td>
</tr>""" for o in orders[:60])

    itemrows = "".join(f"<tr><td>{_esc(sku)}</td><td style='text-align:right'>{n}</td></tr>"
                       for sku, n in top_items)

    body = f"""
<h1>&#9881; {_esc(catalog.shop.get('name', 'shop'))}: merchant admin</h1>
<p class=sub>private, Authentik-gated &middot; {len(orders)} orders total &middot; auto-refresh 60s
&middot; <a href="/catalog">catalog editor &rarr;</a></p>
<div class=grid>{cards}</div>

<h2>Recent orders (60 shown, newest first)</h2>
<div class=panel><table>
<tr><th>order</th><th>status</th><th>created</th><th>buyer</th><th>items</th><th style="text-align:right">total</th></tr>
{rows or '<tr><td colspan=6 style="color:var(--dim)">no orders yet</td></tr>'}
</table></div>

<h2>Top-selling SKUs (all time)</h2>
<div class=panel><table><tr><th>sku</th><th style="text-align:right">units</th></tr>
{itemrows or '<tr><td colspan=2 style="color:var(--dim)">no sales yet</td></tr>'}</table></div>
"""
    return _page(f"{catalog.shop.get('name', 'shop')}: admin", body,
                 head_extra='<meta http-equiv=refresh content=60>\n')


# ---------------------------------------------------------------------------
# Order detail (+ message-customer + entitlement/status actions)
# ---------------------------------------------------------------------------
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

    msgs = store.messages_for_order(order_id)
    msg_rows = "".join(
        f"""<div class=panel style="margin-bottom:8px">
<div class=sub style="margin:0">{time.strftime('%Y-%m-%d %H:%M', time.gmtime(m['created']))} UTC
&middot; {'<span style="color:var(--good)">sent</span>' if m['ok'] else f'<span style="color:var(--warn)">failed ({_esc(m["reason"])})</span>'}
{f" &middot; <b>{_esc(m['title'])}</b>" if m.get('title') else ''}</div>
<div style="white-space:pre-wrap">{_esc(m['body'])}</div></div>"""
        for m in msgs)

    return _page(f"Order #{order_id}: admin", f"""
{_nav()}
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

<h2>Message the buyer</h2>
<p class=sub style="margin-top:-8px">sent over LXMF, the same channel order confirmations/receipts already
use. If their client hasn't announced an LXMF inbox yet, this queues nothing: it reports "no inbox known" so
you know to try again later, same as the automated flow's own retry window.</p>
<div class=panel>
<form method=POST action="/order/{_esc(order_id)}/message">
<label>subject <input name=title value="{_esc(catalog.shop.get('name', 'shop'))}: order {_esc(order_id)}" style="width:60%"></label><br><br>
<textarea name=body rows=4 style="width:100%" placeholder="Message to the buyer about this order..." required></textarea><br><br>
<button class=good type=submit>send message</button>
</form>
</div>
{f'<div>{msg_rows}</div>' if msg_rows else ''}
""")


# ---------------------------------------------------------------------------
# Catalog editor (YAML backend only, see catalog_editor.py)
# ---------------------------------------------------------------------------
def _catalog_list_html(catalog):
    path = getattr(catalog, "path", None)
    rows = "".join(
        f"""<tr><td><a href="/catalog/item?sku={_esc(sku)}">{_esc(sku)}</a></td>
<td>{_esc(it.get('title', ''))}</td><td style="text-align:right">{it.get('price', 0):.2f}</td>
<td>{_esc(it.get('availability', ''))}</td><td>{_esc(it.get('kind', ''))}</td>
<td>{_esc(', '.join(it.get('ships_to') or []) or '(not configured)')}</td></tr>"""
        for sku, it in sorted(catalog.items.items()))
    readonly_note = "" if path else (
        '<div class=panel style="border-color:var(--warn)"><b style="color:var(--warn)">Read-only.</b> '
        "This shop's catalog comes from an external connector (Squarespace/Medusa). There is no local "
        "catalog.yaml. Edit it at the source. Nothing here writes back to it.</div>")
    new_link = '<p><a href="/catalog/item">+ add new item</a></p>' if path else ''
    return _page("Catalog: admin", f"""
{_nav()}
<h1>&#128230; catalog: {len(catalog.items)} items</h1>
{readonly_note}
<div class=panel><table>
<tr><th>sku</th><th>title</th><th style="text-align:right">price</th><th>availability</th><th>kind</th><th>ships to</th></tr>
{rows or '<tr><td colspan=6 style="color:var(--dim)">no items</td></tr>'}
</table></div>
{new_link}
""")


def _catalog_item_html(catalog, sku, error=None):
    it = catalog.items.get(sku, {}) if sku else {}
    is_new = not sku

    def v(k, default=""):
        return _esc(it.get(k, default))

    tags = ", ".join(it.get("tags") or [])
    ships_to = ", ".join(it.get("ships_to") or [])
    img = it.get("image")
    img_preview = (f'<div style="margin:6px 0"><span class=sub>current image: {_esc(img)}</span></div>'
                  if img else "")
    error_html = (f'<div class=panel style="border-color:var(--warn)"><b style="color:var(--warn)">'
                 f'{_esc(error)}</b></div>' if error else "")
    avail_opts = "".join(f'<option value="{a}"{" selected" if it.get("availability", "in_stock") == a else ""}>{a}</option>'
                         for a in AVAILABILITY)
    kind_opts = "".join(f'<option value="{k}"{" selected" if it.get("kind", "physical") == k else ""}>{k}</option>'
                        for k in KINDS)

    return _page(f"{'add item' if is_new else f'edit {sku}'}: admin", f"""
{_nav()}
<p><a href="/catalog">&larr; catalog</a></p>
<h1>{'Add item' if is_new else f'Edit {_esc(sku)}'}</h1>
{error_html}
<div class="panel formcard">
<form method=POST action="/catalog/item" enctype="multipart/form-data">
<input type=hidden name=original_sku value="{_esc(sku)}">
<label>sku <input name=sku value="{v('sku', sku)}"{'readonly' if not is_new else ''} required></label><br><br>
<label>title <input name=title value="{v('title')}" required style="width:60%"></label><br><br>
<label>description<br><textarea name=description rows=4 style="width:100%">{v('description')}</textarea></label><br><br>
<label>price <input name=price type=number step="0.01" value="{it.get('price', '')}" required></label>
<label>kind <select name=kind>{kind_opts}</select></label>
<label>availability <select name=availability>{avail_opts}</select></label><br><br>
<label>tags, comma-separated <input name=tags value="{tags}" style="width:50%"></label><br><br>
<label>ships to: ISO codes comma-separated, or WORLDWIDE
<input name=ships_to value="{ships_to}" style="width:50%" placeholder="US, CA  (blank = ships nowhere until set)"></label><br><br>
<label>weight_kg (for shop.shipping's weight_tiers estimate) <input name=weight_kg type=number step="0.01" value="{it.get('weight_kg', '') or ''}"></label><br><br>
<label>image filename <input name=image value="{v('image')}" placeholder="leave blank if uploading below"></label>
{img_preview}
<label>upload a new product photo <input type=file name=photo accept="image/*"></label><br><br>
<label>digital file (relative to SHOP_FILES, for delivery.get) <input name=file value="{v('file')}"></label><br><br>
<button class=good type=submit>save</button>
</form>
{'' if is_new else f'''<form method=POST action="/catalog/item/delete" style="margin-top:14px"
onsubmit="return confirm('Delete {_esc(sku)}? This cannot be undone.')">
<input type=hidden name=sku value="{_esc(sku)}"><button class=warn>delete item</button></form>'''}
</div>
""")


# ---------------------------------------------------------------------------
def start(store, catalog_ref, rails_ref, port, worker_ref=None, images_dir=None):
    """catalog_ref/rails_ref/worker_ref: zero-arg callables returning the
    CURRENT _catalog/_rails/_lxmf_worker (server.py's catalog can be
    replaced wholesale on a reload (see _health_loop), so this always
    reads the live reference, never a stale one captured at start() time).
    images_dir: SHOP_IMAGES (where catalog.yaml `image:` filenames are read
    from). Needed so the catalog editor's photo upload writes to the same
    place render.sync_images() already reads from."""
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
                        self._html(404, "not found") if page is None else self._html(200, page)
                    elif route == "/catalog":
                        self._html(200, _catalog_list_html(catalog_ref()))
                    elif route == "/catalog/item":
                        self._html(200, _catalog_item_html(catalog_ref(), qs.get("sku", "")))
                    elif route == "/":
                        self._html(200, _dashboard_html(store, catalog_ref(), rails_ref()))
                    else:
                        self._json(404, {"ok": False, "err": "not_found"})
            except Exception as e:  # noqa: BLE001
                self._json(503, {"ok": False, "err": str(e)})

        def _read_body(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b""
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" in ctype:
                return _parse_multipart(ctype, raw)
            try:
                fields = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
            except Exception:
                fields = {}
            return fields, {}

        def do_POST(self):
            parsed = urlsplit(self.path)
            parts = parsed.path.strip("/").split("/")

            if parts[:2] == ["catalog", "item"] and len(parts) == 2:
                self._catalog_item_save()
                return
            if parts == ["catalog", "item", "delete"]:
                self._catalog_item_delete()
                return
            if len(parts) == 3 and parts[0] == "order":
                self._order_action(parts[1], parts[2])
                return
            self._json(404, {"ok": False, "err": "not_found"})

        def _order_action(self, order_id, action):
            fields, _files = self._read_body()
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
                        sku = fields.get("sku", "")
                        if sku:
                            store.entitle(o["identity"], sku)
                    elif action == "message":
                        title = (fields.get("title") or "").strip()[:120]
                        body = (fields.get("body") or "").strip()[:4000]
                        worker = worker_ref() if worker_ref else None
                        if not body:
                            self._json(400, {"ok": False, "err": "empty_message"})
                            return
                        if worker is None:
                            store.message_log(order_id, title, body, False, "lxmf_unavailable")
                        else:
                            ok, reason = worker.send_message(o, title or "Message from the shop", body)
                            store.message_log(order_id, title, body, ok, reason)
                    else:
                        self._json(404, {"ok": False, "err": "unknown_action"})
                        return
                self._redirect(f"/order?id={order_id}")
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "err": str(e)})

        def _catalog_item_save(self):
            catalog = catalog_ref()
            path = getattr(catalog, "path", None)
            if not path:
                self._json(403, {"ok": False, "err": "read_only_catalog"})
                return
            fields, files = self._read_body()
            sku_for_error = (fields.get("sku") or "").strip()
            try:
                with lock:
                    photo = files.get("photo")
                    if photo and photo[1] and images_dir:
                        fname = catalog_editor.save_image(
                            images_dir, sku_for_error or "item", photo[0], photo[1])
                        fields["image"] = fname
                    item = catalog_editor.item_from_form(fields)
                    doc = catalog_editor.load_doc(path)
                    original_sku = (fields.get("original_sku") or "").strip() or None
                    catalog_editor.upsert_item(doc, original_sku, item)
                    catalog_editor.save_doc(path, doc)
            except ValueError as e:
                self._html(400, _catalog_item_html(catalog, sku_for_error, error=str(e)))
                return
            except Exception as e:  # noqa: BLE001
                self._html(500, _catalog_item_html(catalog, sku_for_error, error=f"save failed: {e}"))
                return
            self._redirect(f"/catalog/item?sku={item['sku']}")

        def _catalog_item_delete(self):
            catalog = catalog_ref()
            path = getattr(catalog, "path", None)
            if not path:
                self._json(403, {"ok": False, "err": "read_only_catalog"})
                return
            fields, _files = self._read_body()
            sku = (fields.get("sku") or "").strip()
            try:
                with lock:
                    doc = catalog_editor.load_doc(path)
                    catalog_editor.delete_item(doc, sku)
                    catalog_editor.save_doc(path, doc)
            except Exception as e:  # noqa: BLE001
                self._json(500, {"ok": False, "err": str(e)})
                return
            self._redirect("/catalog")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
