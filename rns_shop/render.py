"""Render the catalog to a micron storefront — the flashy edition.

Micron gives us 12-bit color (`F<hex3>`/`f fg, `B<hex3>`/`b bg), bold (`!),
centering (`c / `a), dividers and box-drawing. The storefront should look like
a shop, not a man page: color-banded header, product cards with a colored rail,
availability color-coding, highlighted order box. MeshData blocks ride on top
(invisible) so crawlers index every listing.

Left-rail cards (no right border) keep alignment safe: markup codes render
zero-width, so fixed-width boxes would need visible-length math — the open
right edge sidesteps it entirely."""
import os

# palette (12-bit micron color)
ACCENT = "5cf"   # cyan — rails, links, structure
GOOD = "6d8"     # green — prices, in stock
WARN = "ec7"     # amber — made to order
BAD = "e66"      # red — out of stock
DIM = "89a"      # dim slate — secondary text
BAND_BG = "124"  # header band background

AVAIL = {
    "in_stock":      ("in stock",            GOOD),
    "made_to_order": ("made to order",       WARN),
    "out_of_stock":  ("out of stock",        BAD),
    "digital":       ("digital — instant",   ACCENT),
}

KIND_TAG = {"physical": "⛟ ships", "digital": "⬇ over the mesh",
            "service": "◈ service"}


def _esc(s):
    """Escape user/catalog text so it can't inject micron markup."""
    return str(s).replace("`", "'").replace("\n", " ").strip()


def _image_bits(item):
    """(meshdata_line, page_link) for an item image served from the node's
    files dir — product photos are public, so /file/ (no ACL) is the right
    home; paid goods never go there."""
    img = item.get("image")
    if not img:
        return "", ""
    name = os.path.basename(str(img))
    return (f"\n# +image: /file/{name}",
            f"`[📷 product photo`:/file/{name}]   ")


def _meshdata_block(item, shop, dest_hex):
    lines = [
        "# +type: product",
        f"# +title: {_esc(item['title'])}",
        f"# +description: {_esc(item.get('description', ''))[:200]}",
        f"# +price: {item['price']:.2f}",
        f"# +currency: {shop.get('currency', 'USD')}",
        f"# +availability: {item['availability']}",
        f"# +sku: {item['sku']}",
        f"# +vendor: {_esc(shop.get('vendor', shop.get('name', 'shop')))}",
        f"# +shop: {dest_hex}",
    ]
    if item.get("tags"):
        lines.append(f"# +tags: {', '.join(_esc(t) for t in item['tags'])}")
    return "\n".join(lines) + _image_bits(item)[0]


def _banner(shop):
    name = _esc(shop.get("name", "shop")).upper()
    tagline = _esc(shop.get("tagline",
                            "a shop on the mesh — no accounts, no passwords"))
    bar = "▔" * (len(name) + 8)
    return (f"`c\n"
            f"`B{BAND_BG}`F{ACCENT}                                        `f`b\n"
            f"`B{BAND_BG}`F{ACCENT}    `!{name}`!    `f`b\n"
            f"`B{BAND_BG}`F{DIM}  {tagline}  `f`b\n"
            f"`F{ACCENT}{bar}`f\n"
            f"`a")


def _avail(item):
    label, color = AVAIL.get(item["availability"], (item["availability"], DIM))
    return f"`F{color}{label}`f"


def _price(item, shop):
    return f"`!`F{GOOD}{item['price']:.2f} {shop.get('currency', 'USD')}`f`!"


def _card(item, shop):
    kind = KIND_TAG.get(item.get("kind", "physical"), "")
    tags = " ".join(f"`F{DIM}#{_esc(t)}`f" for t in item.get("tags", []))
    return (
        f"`F{ACCENT}┌─`f `!{_esc(item['title'])}`!\n"
        f"`F{ACCENT}│`f  {_price(item, shop)}   {_avail(item)}   "
        f"`F{DIM}{kind}`f\n"
        f"`F{ACCENT}│`f  {tags}\n"
        f"`F{ACCENT}└─`f `[view & order →`:/page/item/{item['sku']}.mu]\n")


def item_page(item, shop, dest_hex):
    desc = _esc(item.get("description", ""))
    kind = KIND_TAG.get(item.get("kind", "physical"), "")
    digital_note = ""
    if item.get("kind") == "digital":
        digital_note = (f"`F{ACCENT}│`f  `F{DIM}after payment: fetch with "
                        f"delivery.get — the file arrives over the mesh`f\n")
    return f"""{_meshdata_block(item, shop, dest_hex)}
`c
`B{BAND_BG}`F{ACCENT}  `!{_esc(item['title'])}`!  `f`b
`a

{_price(item, shop)}   ·   {_avail(item)}   ·   `F{DIM}{kind}   ·   sku {item['sku']}`f

{desc}

{_image_bits(item)[1]}`F{DIM}ships to: {', '.join(item.get('ships_to', ['worldwide'])).lower()}`f

`F{GOOD}┌─`f `!BUY IT`!
`F{GOOD}│`f
`F{GOOD}│`f  quantity `B{BAND_BG}`<3|qty`1>`b   `!`[⚡ BUY NOW`:/page/buy/{item['sku']}.mu`qty]`!
`F{GOOD}│`f
{digital_note}`F{GOOD}│`f  `F{DIM}one click — your RNS identity is the account. Confirmation + receipt by LXMF.`f
`F{GOOD}└─`f `F{DIM}new here?`f `[how buying works`:/page/docs/index.mu]  ·  `[my orders`:/page/orders.mu]

`[← back to the catalog`:/page/index.mu]
"""


def index_page(catalog, dest_hex):
    shop = catalog.shop
    cards = "\n".join(_card(catalog.items[s["sku"]], shop)
                      for s in catalog.list())
    if not cards:
        cards = f"`F{DIM}(no items yet)`f"
    return f"""# +type: index
# +title: {_esc(shop.get('name', 'shop'))}
# +description: {_esc(shop.get('name', 'shop'))} — a self-hosted shop on the Reticulum mesh (rns-shop). Buyer identity = RNS identity.

{_banner(shop)}

{cards}
-

>>How it works

`F{ACCENT}┌─`f  1. pick an item · set a quantity · hit `!BUY NOW`!
`F{ACCENT}│`f   2. your RNS identity IS your account — nothing to register
`F{ACCENT}│`f   3. confirmation + receipt arrive by LXMF
`F{ACCENT}└─`f  4. track everything on `[my orders`:/page/orders.mu]

`F{DIM}{_esc(shop.get('invoice_note', 'You will receive an LXMF invoice after ordering.'))}`f

`F{DIM}Building something? The shop is also a MeshAPI service — app rnshop,
aspect shop (answers ops, not pages):`f
`c`B{BAND_BG}`F{ACCENT}  {dest_hex}  `f`b
`a

-
`c`F{DIM}powered by`f `F{ACCENT}rns-shop`f `F{DIM}— run your own: https://github.com/wdunn001/rns-shop`f
`a"""


def sync_images(catalog, images_dir, node_files_dir):
    """Copy catalog item images into the NomadNet node's files dir so
    /file/<name> resolves. Returns count."""
    if not images_dir or not node_files_dir or not os.path.isdir(images_dir):
        return 0
    os.makedirs(node_files_dir, exist_ok=True)
    n = 0
    for item in catalog.items.values():
        img = item.get("image")
        if not img:
            continue
        src = os.path.join(images_dir, os.path.basename(str(img)))
        if os.path.isfile(src):
            dst = os.path.join(node_files_dir, os.path.basename(str(img)))
            with open(src, "rb") as s, open(dst, "wb") as d:
                d.write(s.read())
            n += 1
    return n


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
    # executable checkout pages (buy.mu / orders.mu): copied verbatim, +x so
    # the NomadNet node runs them per-request (that's how BUY NOW works)
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "pages_exec")
    for name in ("buy.mu", "orders.mu"):
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            dst = os.path.join(out_dir, name)
            with open(src, "rb") as s, open(dst, "wb") as d:
                d.write(s.read())
            os.chmod(dst, 0o755)
            written += 1
    # per-item buy wrappers: /page/buy/<SKU>.mu bakes the SKU in, so BUY NOW
    # links only submit the bare `qty` field — the one link-field form every
    # micron client supports (literal name=value entries are not universal)
    buy_dir = os.path.join(out_dir, "buy")
    os.makedirs(buy_dir, exist_ok=True)
    for sku in catalog.items:
        wrapper = (
            "#!/usr/bin/env python3\n"
            "import os, runpy\n"
            f"os.environ.setdefault('var_sku', {sku!r})\n"
            "runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),\n"
            "               '..', 'buy.mu'))\n")
        p = os.path.join(buy_dir, f"{sku}.mu")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(wrapper)
        os.chmod(p, 0o755)
        written += 1
    return written
