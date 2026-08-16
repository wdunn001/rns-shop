"""Render the catalog to a micron storefront: index + one page per product,
each carrying a MeshData `product` head block so crawlers (Beacon) can index
the listing and link buyers to the shop service.

Called by stalld at startup and whenever the catalog file changes -- pages are
static (fast, LoRa-friendly, cacheable); only checkout is dynamic."""
import os


def _esc(s):
    """Escape user/catalog text so it can't inject micron markup."""
    return str(s).replace("`", "'").replace("\n", " ").strip()


def _meshdata_block(item, shop, dest_hex):
    lines = [
        "# +type: product",
        f"# +title: {_esc(item['title'])}",
        f"# +description: {_esc(item.get('description', ''))[:200]}",
        f"# +price: {item['price']:.2f}",
        f"# +currency: {shop.get('currency', 'USD')}",
        f"# +availability: {item['availability']}",
        f"# +sku: {item['sku']}",
        f"# +vendor: {_esc(shop.get('vendor', shop.get('name', 'stall')))}",
        f"# +shop: {dest_hex}",
    ]
    if item.get("tags"):
        lines.append(f"# +tags: {', '.join(_esc(t) for t in item['tags'])}")
    return "\n".join(lines)


def item_page(item, shop, dest_hex):
    money = f"{item['price']:.2f} {shop.get('currency', 'USD')}"
    avail = item["availability"].replace("_", " ")
    return f"""{_meshdata_block(item, shop, dest_hex)}

>{_esc(item['title'])}

`!{money}`!  ·  {avail}  ·  sku {item['sku']}

{_esc(item.get('description', ''))}

To order: open the shop service `!{dest_hex}`! with a MeshAPI client
(op `!order.submit`!, identified), or follow the node's how-to-order page.

`[back to catalog`:/page/index.mu]
"""


def index_page(catalog, dest_hex):
    shop = catalog.shop
    rows = []
    for it in catalog.list():
        rows.append(f"`[{_esc(it['title'])}`:/page/item/{it['sku']}.mu]  "
                    f"{it['price']:.2f} {it['currency']}  ({it['availability'].replace('_', ' ')})")
    items = "\n".join(rows) if rows else "(no items yet)"
    return f"""# +type: index
# +title: {_esc(shop.get('name', 'stall'))}
# +description: {_esc(shop.get('name', 'stall'))} — a self-hosted shop on the Reticulum mesh (rns-stall).

>{_esc(shop.get('name', 'stall'))}

A shop on the mesh. Buyer identity = your RNS identity: no accounts, no passwords.

>>Catalog

{items}

>>How to order

Shop service (MeshAPI, app `!rnstall`! aspect `!shop`!):

`={dest_hex}`=

Ops: catalog.list · catalog.get · cart.get/set · order.submit · order.status ·
entitlement.check (identified ops need link.identify). Payment v1 = invoice:
{_esc(shop.get('invoice_note', 'you will receive an LXMF invoice after ordering.'))}

Powered by `!rns-stall`! — https://github.com/wdunn001/rns-stall
"""


def write_pages(catalog, dest_hex, out_dir):
    os.makedirs(os.path.join(out_dir, "item"), exist_ok=True)
    with open(os.path.join(out_dir, "index.mu"), "w", encoding="utf-8") as fh:
        fh.write(index_page(catalog, dest_hex))
    written = 1
    for sku, item in catalog.items.items():
        p = os.path.join(out_dir, "item", f"{sku}.mu")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(item_page(item, catalog.shop, dest_hex))
        written += 1
    return written
