"""Identity-keyed state: carts, orders, entitlements. SQLite so a merchant can
run a stall from one file; NomadNet/RNS gives us the customer key (the remote
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
"""


_MIGRATIONS = (
    "ALTER TABLE orders ADD COLUMN lxmf TEXT",
    "ALTER TABLE orders ADD COLUMN notified INTEGER DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN receipted INTEGER DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN xmr_address TEXT",
    "ALTER TABLE orders ADD COLUMN xmr_index INTEGER",
    "ALTER TABLE orders ADD COLUMN xmr_amount REAL",
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
    def cart_get(self, identity):
        with self._lock:
            row = self._db.execute(
                "SELECT items FROM carts WHERE identity=?", (identity,)).fetchone()
        return json.loads(row[0]) if row else []

    def cart_set(self, identity, items):
        with self._lock:
            self._db.execute(
                "INSERT INTO carts(identity,items,updated) VALUES(?,?,?) "
                "ON CONFLICT(identity) DO UPDATE SET items=excluded.items, "
                "updated=excluded.updated", (identity, json.dumps(items), time.time()))
            self._db.commit()
        return items

    # ---- orders ----
    def order_create(self, identity, items, total, currency, shipping=None,
                     note=None, lxmf=None):
        oid = secrets.token_hex(4)
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO orders(order_id,identity,items,shipping,note,total,"
                "currency,status,created,updated,lxmf) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (oid, identity, json.dumps(items), json.dumps(shipping or {}),
                 note or "", total, currency, "submitted", now, now, lxmf))
            self._db.execute("DELETE FROM carts WHERE identity=?", (identity,))
            self._db.commit()
        return oid

    def order_get(self, identity, order_id):
        with self._lock:
            row = self._db.execute(
                "SELECT order_id,items,total,currency,status,created,updated "
                "FROM orders WHERE order_id=? AND identity=?",
                (order_id, identity)).fetchone()
        if not row:
            return None
        return {"order_id": row[0], "items": json.loads(row[1]), "total": row[2],
                "currency": row[3], "status": row[4], "created": row[5],
                "updated": row[6]}

    def order_set_status(self, order_id, status):
        with self._lock:
            self._db.execute("UPDATE orders SET status=?, updated=? WHERE order_id=?",
                             (status, time.time(), order_id))
            self._db.commit()

    _WORKER_COLS = "order_id,identity,items,total,currency,status,lxmf"

    def _worker_rows(self, where):
        with self._lock:
            rows = self._db.execute(
                f"SELECT {self._WORKER_COLS} FROM orders WHERE {where}").fetchall()
        return [{"order_id": r[0], "identity": r[1], "items": json.loads(r[2]),
                 "total": r[3], "currency": r[4], "status": r[5], "lxmf": r[6]}
                for r in rows]

    def orders_unnotified(self):
        return self._worker_rows("status='submitted' AND notified=0")

    def orders_unreceipted(self):
        return self._worker_rows("status='paid' AND receipted=0")

    def orders_all(self):
        return self._worker_rows("1=1 ORDER BY created")

    def orders_for_identity(self, identity):
        with self._lock:
            rows = self._db.execute(
                "SELECT order_id,items,total,currency,status,created FROM orders "
                "WHERE identity=? ORDER BY created DESC LIMIT 25", (identity,)).fetchall()
        return [{"order_id": r[0], "items": json.loads(r[1]), "total": r[2],
                 "currency": r[3], "status": r[4], "created": r[5]} for r in rows]

    def order_admin_get(self, order_id):
        with self._lock:
            row = self._db.execute(
                "SELECT order_id,identity,items,shipping,note,total,currency,"
                "status,created,updated,lxmf,notified,receipted FROM orders "
                "WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            return None
        return {"order_id": row[0], "identity": row[1], "items": json.loads(row[2]),
                "shipping": json.loads(row[3] or "{}"), "note": row[4],
                "total": row[5], "currency": row[6], "status": row[7],
                "created": row[8], "updated": row[9], "lxmf": row[10],
                "notified": row[11], "receipted": row[12]}

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
