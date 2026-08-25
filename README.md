# rns-shop

**Self-hosted e-commerce over [Reticulum](https://reticulum.network/) / NomadNet.**
Point it at a catalog file and get a storefront on the mesh: browsable micron pages,
a discoverable API, orders keyed to cryptographic buyer identities, and your
listings surface automatically in [Beacon](https://github.com/wdunn001/beacon) search.

Store-and-checkout tooling that runs entirely over Reticulum.

```
catalog.yaml ──▶ shopd ──▶ storefront pages (.mu + MeshData product records) ─▶ your NomadNet node
                   │                                                                  │
                   └──▶ MeshAPI service: catalog · cart · order · entitlement    Beacon indexes it
                        (buyer identity = RNS identity; no accounts, no passwords)
```

## Why this is different from web e-commerce

- **No accounts.** Every request over an identified RNS Link carries the buyer's
  cryptographically-proven identity. That hash *is* the customer: carts, orders,
  and entitlements key on it. Nothing to sign up for, nothing to phish.
- **No web.** Browsing is micron pages (NomadNet / MeshChat), fast even over LoRa.
  The API is [MeshAPI](https://github.com/wdunn001/meshapi), discoverable via
  `__manifest__`, self-documenting, callable from any RNS node.
- **Discoverable by schema.** Every item page carries a
  [MeshData](https://github.com/wdunn001/meshdata) `product` record (schema.org
  Product/Offer mapped), crawlers index your listings with zero integration work.
- **Offline-tolerant commerce.** Orders are accepted on the mesh; payment happens
  wherever connectivity exists (rails below); confirmations travel by LXMF
  store-and-forward, so buyers can be offline at every step but the exchange.

## Quickstart

```sh
pip install rns umsgpack pyyaml
git clone https://github.com/wdunn001/rns-shop.git && cd rns-shop
cp examples/catalog.yaml catalog.yaml       # edit: your shop, your items
python3 -m rns_shop.server --catalog catalog.yaml --pages-out pages
```

shopd prints your **destination hash** (publish it; it's your shop address),
renders `pages/`, and announces while healthy. Serve `pages/` from a NomadNet
node, its **own** node with its own identity (sub-pages of another node aren't
independently discoverable). Docker: see `Dockerfile` + `docs/`.

> **Back up the identity file.** The destination hash derives from it; losing it
> breaks every published pointer to your shop. Same for buyers:
> `~/.rns_shop/client_identity` *is* the customer account.

## Buying

```sh
python3 -m rns_shop.client <dest> catalog.list
python3 -m rns_shop.client <dest> catalog.get --sku DEMO-ZINE
python3 -m rns_shop.client <dest> order.submit --item DEMO-ZINE:1 --note "hi"
python3 -m rns_shop.client <dest> order.status --order-id <id>
python3 -m rns_shop.client <dest> manifest       # live API docs
```

Identified ops (cart/order/entitlement) sign the link with your buyer identity
(created on first use).

## Ops

`catalog.list` · `catalog.get` · `cart.get` · `cart.set` · `order.submit` ·
`order.status` · `entitlement.check` · `delivery.get` · `pay.link` · `pay.xmr`, full schemas in the live manifest.

**Digital delivery**: `delivery.get` streams a purchased file over the encrypted
Link (entitlement-checked; RNS handles large payloads as Resources). Merchant
settles an invoice with `python3 -m rns_shop.admin --db ... mark-paid <order>`, the LXMF worker then entitles digital SKUs and sends the receipt automatically.
Buyers read confirmations with `python3 -m rns_shop.client <dest> inbox`.

## Payment rails

- **Invoice (default).** Orders record the buyer identity; the merchant settles
  out-of-band and runs `mark-paid`. LXMF confirmation + receipt are automatic.
- **Hybrid web checkout.** Set `shop.pay_link_template` (e.g. a Stripe payment
  link with `{order_id}` as client reference), `pay.link` hands buyers the URL;
  your processor's webhook hits `rns_shop.webhook_bridge` (`POST /paid`,
  shared-secret) to flip the order paid.
- **Monero (watch-only).** Set `SHOP_XMR_RPC` to a view-only monero-wallet-rpc
  and `shop.xmr_rate`, `pay.xmr` assigns a per-order subaddress; the watcher
  confirms transfers. No spend keys near the mesh, ever.

## Status / roadmap

v0.2: catalog (YAML or `medusa://` connector), storefront + MeshData records,
carts, orders, entitlements, LXMF confirmations/receipts, gated digital delivery
over the mesh, invoice + pay-link + XMR(watch-only) rails, webhook bridge, admin
CLI. Next: ERPNext order push, Beacon shop view, Sideband LXMF ordering bot.
Non-goal, permanently: hosting a multi-vendor marketplace, run your own shop.

## Family

[rns-time](https://github.com/wdunn001/rns-time) (when) ·
rns-geo (where) · **rns-shop** (what's for sale), built on [MeshAPI](https://github.com/wdunn001/meshapi) +
[MeshData](https://github.com/wdunn001/meshdata), indexed by
[Beacon](https://github.com/wdunn001/beacon).
