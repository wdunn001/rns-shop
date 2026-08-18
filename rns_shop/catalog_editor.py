"""Admin-side mutation helpers for the YAML catalog backend -- read/modify/
write catalog.yaml directly, for the admin portal's item editor. Deliberately
separate from catalog.py (that module is the RUNTIME READ interface every
backend implements, including read-only ones like Squarespace/Medusa; this
module is YAML-specific WRITE support that only makes sense for that one
backend). The editor is offered only when the active catalog is a plain-file
Catalog (has a real `.path`) -- an externally-sourced catalog has nothing
here to write back to, by design (see catalog.CatalogSource's docstring).

Editing title/description/price/availability/tags/image here covers the
MeshData block too (render.py's `_meshdata_block` builds +title/+description/
+price/+availability/+tags/+image straight from these same item fields) --
there's no separate "MeshData editor" because MeshData isn't separate data,
it's a rendering of these fields.

KNOWN TRADEOFF: round-tripping through yaml.safe_load/safe_dump does not
preserve comments -- catalog.yaml's extensive inline documentation comments
are LOST the first time an item is saved through this editor. Accepted
pragmatically (ruamel.yaml's comment-preserving round-trip would be a new
dependency for a homelab admin tool); the merchant-facing behavior (what
gets sold, for how much, described how) is unaffected."""
import hashlib
import os
import re

import yaml

from .catalog import AVAILABILITY, KINDS


def load_doc(path):
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"shop": {}, "items": []}


def save_doc(path, doc):
    """Atomic write (tmp + rename) -- catalog.yaml is a live, hot-reloaded
    config (shopd's _health_loop polls its mtime every 60s); a half-written
    file read mid-write would either fail to parse or, worse, parse as a
    truncated-but-valid partial catalog."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, path)


def list_items(doc):
    return doc.get("items") or []


def get_item(doc, sku):
    for it in list_items(doc):
        if str(it.get("sku")) == str(sku):
            return it
    return None


def _clean_list(raw):
    return [s.strip() for s in re.split(r"[,\n]", raw or "") if s.strip()]


def item_from_form(form):
    """Build a catalog item dict from the editor form's flat string fields.
    Validates against the SAME rules catalog.Catalog.load() enforces at
    runtime (REQUIRED/AVAILABILITY/KINDS) so a bad save fails here, in the
    editor, with a clear message -- not silently at the next shopd reload."""
    sku = (form.get("sku") or "").strip()
    title = (form.get("title") or "").strip()
    price_raw = (form.get("price") or "").strip()
    if not sku or not title or not price_raw:
        raise ValueError("sku, title, and price are required")
    try:
        price = float(price_raw)
    except ValueError:
        raise ValueError(f"price {price_raw!r} is not a number")
    availability = (form.get("availability") or "in_stock").strip()
    if availability not in AVAILABILITY:
        raise ValueError(f"availability must be one of {', '.join(AVAILABILITY)}")
    kind = (form.get("kind") or "physical").strip()
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    item = {
        "sku": sku, "title": title, "price": price,
        "description": (form.get("description") or "").strip(),
        "availability": availability, "kind": kind,
        "tags": _clean_list(form.get("tags")),
    }
    ships_to = _clean_list(form.get("ships_to"))
    if ships_to:
        item["ships_to"] = ships_to
    weight = (form.get("weight_kg") or "").strip()
    if weight:
        try:
            item["weight_kg"] = float(weight)
        except ValueError:
            raise ValueError(f"weight_kg {weight!r} is not a number")
    image = (form.get("image") or "").strip()
    if image:
        item["image"] = image
    file_field = (form.get("file") or "").strip()
    if file_field:
        item["file"] = file_field
    return item


def upsert_item(doc, original_sku, item):
    """original_sku: the sku this item was loaded under (None for a new
    item) -- lets the editor RENAME a sku by removing the old entry and
    inserting the new one, rather than ending up with both."""
    items = doc.setdefault("items", [])
    if original_sku is not None:
        items[:] = [it for it in items if str(it.get("sku")) != str(original_sku)]
    # replace-in-place if this sku already exists (e.g. saving with the
    # same sku again), else append
    for i, it in enumerate(items):
        if str(it.get("sku")) == str(item["sku"]):
            items[i] = item
            return
    items.append(item)


def delete_item(doc, sku):
    items = doc.get("items") or []
    before = len(items)
    doc["items"] = [it for it in items if str(it.get("sku")) != str(sku)]
    return len(doc["items"]) != before


def save_image(images_dir, sku, filename, data):
    """Save an uploaded product photo into SHOP_IMAGES (the dir
    catalog.yaml `image:` filenames are read from -- see render.sync_images
    and docker-compose.yml's SHOP_IMAGES/SHOP_NODE_FILES split). Named
    <sku>-<contenthash>.<ext> so re-uploading the same photo for the same
    item is idempotent (same name) but a NEW photo never collides with an
    old one still referenced by a not-yet-reloaded catalog."""
    os.makedirs(images_dir, exist_ok=True)
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    safe_sku = re.sub(r"[^A-Za-z0-9_-]", "-", sku)
    name = f"{safe_sku}-{hashlib.sha256(data).hexdigest()[:12]}{ext}"
    with open(os.path.join(images_dir, name), "wb") as fh:
        fh.write(data)
    return name
