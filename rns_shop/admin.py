"""shopctl: merchant admin CLI (direct DB, run next to shopd / via docker exec).

    python3 -m rns_shop.admin --db /data/shop.db list
    python3 -m rns_shop.admin --db /data/shop.db show <order_id>
    python3 -m rns_shop.admin --db /data/shop.db mark-paid <order_id>
    python3 -m rns_shop.admin --db /data/shop.db entitle <identity> <sku>

mark-paid is the invoice rail's settlement step: the LXMF worker picks up the
paid order, entitles digital SKUs, and sends the receipt automatically."""
import argparse
import json

from .store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("show"); p.add_argument("order_id")
    p = sub.add_parser("mark-paid"); p.add_argument("order_id")
    p = sub.add_parser("entitle"); p.add_argument("identity"); p.add_argument("sku")
    args = ap.parse_args()

    st = Store(args.db)
    if args.cmd == "list":
        for o in st.orders_all():
            print(f"{o['order_id']}  {o['status']:<16} {o['total']:>8.2f} "
                  f"{o['currency']}  {o['identity'][:12]}...  "
                  f"{','.join(e['sku'] for e in o['items'])}")
    elif args.cmd == "show":
        print(json.dumps(st.order_admin_get(args.order_id), indent=2))
    elif args.cmd == "mark-paid":
        st.order_set_status(args.order_id, "paid")
        print(f"{args.order_id} -> paid (worker will entitle + receipt)")
    elif args.cmd == "entitle":
        st.entitle(args.identity, args.sku)
        print(f"entitled {args.identity[:12]}... for {args.sku}")


if __name__ == "__main__":
    main()
