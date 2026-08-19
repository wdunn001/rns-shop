"""Render the catalog to a micron storefront: the flashy edition.

Micron gives us 12-bit color (`F<hex3>`/`f fg, `B<hex3>`/`b bg), bold (`!),
centering (`c / `a), dividers and box-drawing. The storefront should look like
a shop, not a man page: color-banded header, product cards with a colored rail,
availability color-coding, highlighted order box. MeshData blocks ride on top
(invisible) so crawlers index every listing.

Left-rail cards (no right border) keep alignment safe: markup codes render
zero-width, so fixed-width boxes would need visible-length math, the open
right edge sidesteps it entirely."""
import os

# palette (12-bit micron color)
ACCENT = "5cf"   # cyan: rails, links, structure
GOOD = "6d8"     # green: prices, in stock
WARN = "ec7"     # amber: made to order
BAD = "e66"      # red: out of stock
DIM = "89a"      # dim slate: secondary text
BAND_BG = "124"  # header band background

AVAIL = {
    "in_stock":      ("in stock",            GOOD),
    "made_to_order": ("made to order",       WARN),
    "out_of_stock":  ("out of stock",        BAD),
    "digital":       ("digital, instant",   ACCENT),
}

KIND_TAG = {"physical": "ships", "digital": "over the mesh",
            "service": "service"}


def _esc(s):
    """Escape user/catalog text so it can't inject micron markup."""
    return str(s).replace("`", "'").replace("\n", " ").strip()


def _image_bits(item):
    """(meshdata_line, page_link) for an item image served from the node's
    files dir, product photos are public, so /file/ (no ACL) is the right
    home; paid goods never go there."""
    img = item.get("image")
    if not img:
        return "", ""
    name = os.path.basename(str(img))
    return (f"\n# +image: /file/{name}",
            f"`[product photo`:/file/{name}]   ")


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
                            "a shop on the mesh, no accounts, no passwords"))
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
        f"`F{ACCENT}└─`f `[view & order ->`:/page/item/{item['sku']}.mu]\n")


def item_page(item, shop, dest_hex):
    desc = _esc(item.get("description", ""))
    kind = KIND_TAG.get(item.get("kind", "physical"), "")
    digital_note = ""
    if item.get("kind") == "digital":
        digital_note = (f"`F{ACCENT}│`f  `F{DIM}after payment: fetch with "
                        f"delivery.get, the file arrives over the mesh`f\n")
    return f"""{_meshdata_block(item, shop, dest_hex)}
`c
`B{BAND_BG}`F{ACCENT}  `!{_esc(item['title'])}`!  `f`b
`a

{_price(item, shop)}   ·   {_avail(item)}   ·   `F{DIM}{kind}   ·   sku {item['sku']}`f

{desc}

{_image_bits(item)[1]}`F{DIM}ships to: {', '.join(item.get('ships_to') or []).lower() or 'not configured yet -- contact the merchant'}`f

`F{GOOD}┌─`f `!BUY IT`!
`F{GOOD}│`f
`F{GOOD}│`f  quantity `B{BAND_BG}`<3|qty`1>`b   `!`[BUY NOW`:/page/buy/{item['sku']}.mu`qty]`!   `!`F{ACCENT}`[+ ADD TO CART`:/page/cart/add/{item['sku']}.mu`qty]`f`!
`F{GOOD}│`f
{digital_note}`F{GOOD}│`f  `F{DIM}one click. Your RNS identity is the account. Confirmation + receipt by LXMF.`f
`F{GOOD}└─`f `F{DIM}new here?`f `[how buying works`:/page/docs/index.mu]  ·  `[my orders`:/page/orders.mu]  ·  `[my cart`:/page/cart.mu]

`[<- back to the catalog`:/page/index.mu]
"""


def index_page(catalog, dest_hex):
    shop = catalog.shop
    cards = "\n".join(_card(catalog.items[s["sku"]], shop)
                      for s in catalog.list())
    if not cards:
        cards = f"`F{DIM}(no items yet)`f"
    return f"""# +type: index
# +title: {_esc(shop.get('name', 'shop'))}
# +description: {_esc(shop.get('name', 'shop'))}: a self-hosted shop on the Reticulum mesh (rns-shop). Buyer identity = RNS identity.

{_banner(shop)}

{cards}
-

>>How it works

`F{ACCENT}┌─`f  1. pick items · hit `!BUY NOW`! for one, or `!ADD TO CART`! for several
`F{ACCENT}│`f   2. your RNS identity IS your account, nothing to register
`F{ACCENT}│`f   3. confirmation + receipt arrive by LXMF
`F{ACCENT}└─`f  4. track everything on `[my orders`:/page/orders.mu] `F{DIM}·`f `[my cart`:/page/cart.mu] `F{DIM}·`f `[my account`:/page/account.mu]

`F{DIM}{_esc(shop.get('invoice_note', 'You will receive an LXMF invoice after ordering.'))}`f

`F{DIM}Building something? The shop is also a MeshAPI service, app rnshop,
aspect shop (answers ops, not pages):`f
`c`B{BAND_BG}`F{ACCENT}  {dest_hex}  `f`b
`a

-
`c`F{DIM}powered by`f `F{ACCENT}rns-shop`f `F{DIM}, run your own: https://github.com/wdunn001/rns-shop`f
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
    # index = exec wrapper (greets identified buyers) over the static body
    # (what crawlers and anonymous visitors see, MeshData block included)
    with open(os.path.join(out_dir, "index_body.mu"), "w", encoding="utf-8") as fh:
        fh.write(index_page(catalog, dest_hex))
    written = 1
    for sku, item in catalog.items.items():
        p = os.path.join(out_dir, "item", f"{sku}.mu")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(item_page(item, catalog.shop, dest_hex))
        written += 1
    # executable checkout pages: copied verbatim, +x so the NomadNet node
    # runs them per-request (that's how BUY NOW / cart / orders work)
    src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "pages_exec")
    for name in ("buy.mu", "orders.mu", "account.mu", "index.mu",
                "cart.mu", "cart_add.mu", "cart_remove.mu"):
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            dst = os.path.join(out_dir, name)
            with open(src, "rb") as s, open(dst, "wb") as d:
                d.write(s.read())
            os.chmod(dst, 0o755)
            written += 1

    def _wrapper(target, depth=1, **env):
        """A tiny per-sku exec page that bakes `env` in via os.environ before
        runpy-ing the shared `target` script (which lives directly in
        out_dir) -- the "bake the argument into the file path" trick that
        keeps every click a bare-link with no literal link-var (not every
        micron client supports those). `depth` = how many directories below
        out_dir this wrapper itself lives in (buy/<sku>.mu = 1,
        cart/add/<sku>.mu = 2, ...) -- how many '..' hops get back to
        out_dir/target at RUNTIME, resolved relative to the wrapper's own
        __file__ (the container actually serving pages, NOT this process --
        so an absolute path baked here would be wrong if the two containers
        mount the shared pages volume at different paths)."""
        lines = ["#!/usr/bin/env python3", "import os, runpy"]
        for k, v in env.items():
            lines.append(f"os.environ.setdefault({k!r}, {v!r})")
        ups = ", ".join(["'..'"] * depth)
        lines.append("runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)),")
        lines.append(f"               {ups}, {target!r}))")
        return "\n".join(lines) + "\n"

    # per-item buy wrappers: /page/buy/<SKU>.mu bakes the SKU in, so BUY NOW
    # links only submit the bare `qty` field, the one link-field form every
    # micron client supports (literal name=value entries are not universal)
    buy_dir = os.path.join(out_dir, "buy")
    os.makedirs(buy_dir, exist_ok=True)
    for sku in catalog.items:
        p = os.path.join(buy_dir, f"{sku}.mu")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(_wrapper("buy.mu", depth=1, var_sku=sku))
        os.chmod(p, 0o755)
        written += 1

    # per-item cart wrappers: add-to-cart (+ / ADD TO CART button), decrement
    # (-), and full removal (x) -- same trick, three two-level-deep dirs
    # (cart/add/<sku>.mu etc, depth=2) so each click is a bare link to a
    # pre-baked file, never a literal var.
    for subdir, target, extra_env in (
        ("cart/add", "cart_add.mu", {}),
        ("cart/dec", "cart_remove.mu", {"var_mode": "dec"}),
        ("cart/remove", "cart_remove.mu", {"var_mode": "all"}),
    ):
        d = os.path.join(out_dir, subdir)
        os.makedirs(d, exist_ok=True)
        for sku in catalog.items:
            p = os.path.join(d, f"{sku}.mu")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(_wrapper(target, depth=2, var_sku=sku, **extra_env))
            os.chmod(p, 0o755)
            written += 1
    return written
