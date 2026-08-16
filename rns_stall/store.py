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


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(_SCHEMA)
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
    def order_create(self, identity, items, total, currency, shipping=None, note=None):
        oid = secrets.token_hex(4)
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",
                (oid, identity, json.dumps(items), json.dumps(shipping or {}),
                 note or "", total, currency, "submitted", now, now))
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

    def orders_pending(self):
        """For the worker: orders needing action (confirmation, fulfillment)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT order_id,identity,items,total,currency,status FROM orders "
                "WHERE status IN ('submitted','paid')").fetchall()
        return [{"order_id": r[0], "identity": r[1], "items": json.loads(r[2]),
                 "total": r[3], "currency": r[4], "status": r[5]} for r in rows]

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
