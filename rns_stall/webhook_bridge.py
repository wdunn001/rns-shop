"""Webhook bridge: the clearnet edge posts here when a payment lands; we flip
the order to paid. The LXMF worker (in stalld, same DB) handles entitlement +
receipt from there.

    python3 -m rns_stall.webhook_bridge --db /data/stall.db --port 8218 \
        --secret-file /data/webhook_secret

POST /paid  {"order_id": "..."}  with header  X-Stall-Secret: <secret>

Put this behind your HTTPS edge (e.g. Stripe webhook -> a tiny adapter -> this,
or point a Stripe webhook handler's success path straight at it). v1 uses a
shared secret; verify processor signatures at your edge adapter."""
import argparse
import json
import os
import secrets as pysecrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import Store


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

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/paid":
                self.send_response(404); self.end_headers(); return
            if self.headers.get("X-Stall-Secret") != secret:
                self.send_response(403); self.end_headers(); return
            try:
                body = json.loads(self.rfile.read(
                    int(self.headers.get("Content-Length", 0))))
                oid = str(body["order_id"])
            except Exception:
                self.send_response(400); self.end_headers(); return
            store.order_set_status(oid, "paid")
            out = json.dumps({"ok": True, "order_id": oid, "status": "paid"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    print(f"webhook bridge on :{args.port} (POST /paid)")
    ThreadingHTTPServer(("0.0.0.0", args.port), H).serve_forever()


if __name__ == "__main__":
    main()
