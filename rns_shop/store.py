"""Identity-keyed state: carts, orders, entitlements. SQLite so a merchant can
run a shop from one file; NomadNet/RNS gives us the customer key (the remote
identity hash) for free -- there are no accounts, sessions, or passwords."""
import json
import os
import secrets
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS carts(
  identity TEXT PRIMARY KEY, items TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS orders(
  order_id TEXT PRIMARY KEY, identity TEXT NOT NULL, items TEXT NOT NULL,
  shipping TEXT, note TEXT, total REAL NOT NULL, currency TEXT NOT NULL,
  status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS orders_identity ON orders(identity);
CREATE TABLE IF NOT EXISTS entitlements(
  identity TEXT NOT NULL, sku TEXT NOT NULL, expires REAL,
  PRIMARY KEY(identity, sku));
CREATE TABLE IF NOT EXISTS profiles(
  identity TEXT PRIMARY KEY, shipping TEXT, billing TEXT,
  pay_method TEXT, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS lxmf_map(
  identity TEXT PRIMARY KEY, dest TEXT NOT NULL, updated REAL NOT NULL);
"""


_MIGRATIONS = (
    "ALTER TABLE orders ADD COLUMN lxmf TEXT",
    "ALTER TABLE orders ADD COLUMN notified INTEGER DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN receipted INTEGER DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN xmr_address TEXT",
    "ALTER TABLE orders ADD COLUMN xmr_index INTEGER",
    "ALTER TABLE orders ADD COLUMN xmr_amount REAL",
    "ALTER TABLE orders ADD COLUMN pay_method TEXT",
    "ALTER TABLE orders ADD COLUMN pay_text TEXT",
    # shipping.py: subtotal is the pre-shipping item total, shipping_fee is
    # what shipping.quote() added (0/NULL when free or not applicable).
    # `total` (existing column) stays subtotal+shipping_fee -- unchanged
    # meaning for every caller that already reads it.
    "ALTER TABLE orders ADD COLUMN subtotal REAL",
    "ALTER TABLE orders ADD COLUMN shipping_fee REAL",
)


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
        for mig in _MIGRATIONS:
            try:
                self._db.execute(mig)
            except sqlite3.OperationalError:
                pass  # column already exists
        self._db.commit()

    # ---- carts ----
    # Persistent per-identity (SQLite, no expiry -- carries across visits by
    # design: the buyer's RNS identity IS the account, so "log back in
    # later" is just reconnecting with the same identity). One active cart
    # per identity, same as any standard storefront's server-side cart.
    MAX_CART_QTY = 999

    def cart_get(self, identity):
        with self._lock:
            row = self._db.execute(
                "SELECT items FROM carts WHERE identity=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else []

    def _cart_save(self, identity, items):
        with self._lock:
            self._db.execute(
                "INSERT INTO carts(identity,items,updated) VALUES(?,?,?) "
                "ON CONFLICT(identity) DO UPDATE SET items=excluded.items, "
                "updated=excluded.updated", (identity, json.dumps(items), time.time()))
            self._db.commit()
        return items

    def cart_set(self, identity, items):
        """Full replace -- bulk/programmatic clients (a dedicated RNS
        client that already tracks its own cart state) use this; the page
        UI uses the incremental ops below instead, so one "add to cart"
        click never needs to already know the whole current cart."""
        return self._cart_save(identity, items)

    def cart_add(self, identity, sku, qty):
        """Add `qty` of a sku, incrementing an existing line rather than
        duplicating it -- standard "add to cart" semantics."""
        items = self.cart_get(identity)
        for e in items:
            if e["sku"] == sku:
                e["qty"] = min(self.MAX_CART_QTY, e["qty"] + qty)
                break
        else:
            items.append({"sku": sku, "qty": min(self.MAX_CART_QTY, qty)})
        return self._cart_save(identity, items)

    def cart_remove(self, identity, sku, qty=None):
        """Remove a sku entirely (qty=None), or decrement by qty (dropping
        the line once it reaches 0) -- standard "remove" / "-" stepper."""
        items = self.cart_get(identity)
        out = []
        for e in items:
            if e["sku"] != sku:
                out.append(e)
                continue
            if qty is None:
                continue
            remaining = e["qty"] - qty
            if remaining > 0:
                out.append({"sku": sku, "qty": remaining})
        return self._cart_save(identity, out)

    def cart_set_qty(self, identity, sku, qty):
        """Exact quantity for one line (qty<=0 removes it) -- the "type a
        number in the qty box" case, without the caller computing a delta."""
        items = [e for e in self.cart_get(identity) if e["sku"] != sku]
        if qty > 0:
            items.append({"sku": sku, "qty": min(self.MAX_CART_QTY, qty)})
        return self._cart_save(identity, items)

    def cart_clear(self, identity):
        return self._cart_save(identity, [])

    # ---- orders ----
    def order_create(self, identity, items, total, currency, shipping=None,
                     note=None, lxmf=None, subtotal=None, shipping_fee=None):
        oid = secrets.token_hex(4)
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO orders(order_id,identity,items,shipping,note,total,"
                "currency,status,created,updated,lxmf,subtotal,shipping_fee) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, identity, json.dumps(items), json.dumps(shipping or {}),
                 note or "", total, currency, "submitted", now, now, lxmf,
                 subtotal, shipping_fee))
            self._db.execute("DELETE FROM carts WHERE identity=?", (identity,))
            self._db.commit()
        return oid

    def order_get(self, identity, order_id):
        with self._lock:
            row = self._db.execute(
                "SELECT order_id,items,total,currency,status,created,updated,"
                "subtotal,shipping_fee "
                "FROM orders WHERE order_id=? AND identity=?",
                (order_id, identity)).fetchone()
        if not row:
            return None
        return {"order_id": row[0], "items": json.loads(row[1]), "total": row[2],
                "currency": row[3], "status": row[4], "created": row[5],
                "updated": row[6], "subtotal": row[7], "shipping_fee": row[8]}

    def order_set_status(self, order_id, status):
        with self._lock:
            self._db.execute("UPDATE orders SET status=?, updated=? WHERE order_id=?",
                             (status, time.time(), order_id))
            self._db.commit()

    _WORKER_COLS = "order_id,identity,items,total,currency,status,lxmf,created"

    def _worker_rows(self, where):
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._WORKER_COLS} FROM orders WHERE {where}").fetchall()
        return [{"order_id": r[0], "identity": r[1], "items": json.loads(r[2]),
                 "total": r[3], "currency": r[4], "status": r[5], "lxmf": r[6],
                 "created": r[7]}
                for r in rows]

    def order_set_lxmf(self, order_id, dest):
        with self._lock:
            self._db.execute("UPDATE orders SET lxmf=? WHERE order_id=?",
                             (dest, order_id))
            self._db.commit()

    # ---- lxmf announce map: identity hash -> lxmf.delivery dest hash ----
    def lxmf_map_put(self, identity, dest):
        with self._lock:
            self._db.execute(
                "INSERT INTO lxmf_map VALUES(?,?,?) ON CONFLICT(identity) DO "
                "UPDATE SET dest=excluded.dest, updated=excluded.updated",
                (identity, dest, time.time()))
            self._db.commit()

    def lxmf_lookup(self, identity):
        with self._lock:
            row = self._db.execute(
                "SELECT dest FROM lxmf_map WHERE identity=?", (identity,)).fetchone()
        return row[0] if row else None

    def orders_unnotified(self):
        return self._worker_rows("status='submitted' AND notified=0")

    def orders_unreceipted(self):
        return self._worker_rows("status='paid' AND receipted=0")

    def orders_all(self):
        return self._worker_rows("1=1 ORDER BY created")

    def orders_for_identity(self, identity):
        with self._lock:
            rows = self._db.execute(
                "SELECT order_id,items,total,currency,status,created,"
                "subtotal,shipping_fee FROM orders "
                "WHERE identity=? ORDER BY created DESC LIMIT 25", (identity,)).fetchall()
        return [{"order_id": r[0], "items": json.loads(r[1]), "total": r[2],
                 "currency": r[3], "status": r[4], "created": r[5],
                 "subtotal": r[6], "shipping_fee": r[7]} for r in rows]

    def order_admin_get(self, order_id):
        with self._lock:
            row = self._db.execute(
                "SELECT order_id,identity,items,shipping,note,total,currency,"
                "status,created,updated,lxmf,notified,receipted,subtotal,"
                "shipping_fee FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            return None
        return {"order_id": row[0], "identity": row[1], "items": json.loads(row[2]),
                "shipping": json.loads(row[3] or "{}"), "note": row[4],
                "total": row[5], "currency": row[6], "status": row[7],
                "created": row[8], "updated": row[9], "lxmf": row[10],
                "notified": row[11], "receipted": row[12], "subtotal": row[13],
                "shipping_fee": row[14]}

    def mark_notified(self, order_id):
        with self._lock:
            self._db.execute("UPDATE orders SET notified=1 WHERE order_id=?",
                             (order_id,))
            self._db.commit()

    def mark_receipted(self, order_id, new_status):
        with self._lock:
            self._db.execute(
                "UPDATE orders SET receipted=1, status=?, updated=? WHERE order_id=?",
                (new_status, time.time(), order_id))
            self._db.commit()

    # ---- xmr rail ----
    def order_set_xmr(self, order_id, address, index, amount):
        with self._lock:
            self._db.execute(
                "UPDATE orders SET xmr_address=?, xmr_index=?, xmr_amount=?, "
                "status='awaiting_payment', updated=? WHERE order_id=?",
                (address, index, amount, time.time(), order_id))
            self._db.commit()

    def orders_awaiting_xmr(self):
        with self._lock:
            rows = self._db.execute(
                "SELECT order_id,xmr_index,xmr_amount FROM orders WHERE "
                "status='awaiting_payment' AND xmr_index IS NOT NULL").fetchall()
        return [{"order_id": r[0], "xmr_index": r[1], "xmr_amount": r[2]}
                for r in rows]

    def order_set_payment(self, order_id, method, text):
        with self._lock:
            self._db.execute(
                "UPDATE orders SET pay_method=?, pay_text=? WHERE order_id=?",
                (method, text, order_id))
            self._db.commit()

    def order_payment(self, order_id):
        with self._lock:
            row = self._db.execute(
                "SELECT pay_method,pay_text FROM orders WHERE order_id=?",
                (order_id,)).fetchone()
        return {"method": row[0], "text": row[1]} if row and row[0] else None

    # ---- profiles ----
    def profile_get(self, identity):
        with self._lock:
            row = self._db.execute(
                "SELECT shipping,billing,pay_method FROM profiles WHERE identity=?",
                (identity,)).fetchone()
        if not row:
            return {}
        return {"shipping": row[0] or "", "billing": row[1] or "",
                "pay_method": row[2] or ""}

    def profile_set(self, identity, shipping=None, billing=None, pay_method=None):
        cur = self.profile_get(identity)
        vals = {"shipping": shipping if shipping is not None else cur.get("shipping"),
                "billing": billing if billing is not None else cur.get("billing"),
                "pay_method": pay_method if pay_method is not None
                else cur.get("pay_method")}
        with self._lock:
            self._db.execute(
                "INSERT INTO profiles(identity,shipping,billing,pay_method,updated) "
                "VALUES(?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET "
                "shipping=excluded.shipping, billing=excluded.billing, "
                "pay_method=excluded.pay_method, updated=excluded.updated",
                (identity, vals["shipping"], vals["billing"],
                 vals["pay_method"], time.time()))
            self._db.commit()
        return vals

    # ---- entitlements ----
    def entitle(self, identity, sku, expires=None):
        with self._lock:
            self._db.execute(
                "INSERT INTO entitlements VALUES(?,?,?) ON CONFLICT(identity,sku) "
                "DO UPDATE SET expires=excluded.expires", (identity, sku, expires))
            self._db.commit()

    def entitled(self, identity, sku):
        with self._lock:
            row = self._db.execute(
                "SELECT expires FROM entitlements WHERE identity=? AND sku=?",
                (identity, sku)).fetchone()
        if not row:
            return False
        return row[0] is None or row[0] > time.time()
