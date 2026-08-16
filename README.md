# rns-stall

**Self-hosted e-commerce over [Reticulum](https://reticulum.network/) / NomadNet.**
Point it at a catalog file and get a storefront on the mesh: browsable micron pages,
a discoverable API, orders keyed to cryptographic buyer identities — and your
listings surface automatically in [Beacon](https://github.com/wdunn001/beacon) search.

As far as we know, the first e-commerce tooling ever built for Reticulum.

```
catalog.yaml ──▶ stalld ──▶ storefront pages (.mu + MeshData product records) ─▶ your NomadNet node
                   │                                                                  │
                   └──▶ MeshAPI service: catalog · cart · order · entitlement    Beacon indexes it
                        (buyer identity = RNS identity; no accounts, no passwords)
```

## Why this is different from web e-commerce

- **No accounts.** Every request over an identified RNS Link carries the buyer's
  cryptographically-proven identity. That hash *is* the customer: carts, orders,
  and entitlements key on it. Nothing to sign up for, nothing to phish.
- **No web.** Browsing is micron pages (NomadNet / MeshChat), fast even over LoRa.
  The API is [MeshAPI](https://github.com/wdunn001/meshapi) — discoverable via
  `__manifest__`, self-documenting, callable from any RNS node.
- **Discoverable by schema.** Every item page carries a
  [MeshData](https://github.com/wdunn001/meshdata) `product` record (schema.org
  Product/Offer mapped) — crawlers index your listings with zero integration work.
- **Offline-tolerant commerce.** Orders are accepted on the mesh; payment happens
  wherever connectivity exists (rails below); confirmations travel by LXMF
  store-and-forward, so buyers can be offline at every step but the exchange.

## Quickstart

```sh
pip install rns umsgpack pyyaml
git clone https://github.com/wdunn001/rns-stall.git && cd rns-stall
cp examples/catalog.yaml catalog.yaml       # edit: your shop, your items
python3 -m rns_stall.server --catalog catalog.yaml --pages-out pages
```

stalld prints your **destination hash** (publish it; it's your shop address),
renders `pages/`, and announces while healthy. Serve `pages/` from a NomadNet
node — its **own** node with its own identity (sub-pages of another node aren't
independently discoverable). Docker: see `Dockerfile` + `docs/`.

> **Back up the identity file.** The destination hash derives from it; losing it
> breaks every published pointer to your shop. Same for buyers:
> `~/.rns_stall/client_identity` *is* the customer account.

## Buying

```sh
python3 -m rns_stall.client <dest> catalog.list
python3 -m rns_stall.client <dest> catalog.get --sku DEMO-ZINE
python3 -m rns_stall.client <dest> order.submit --item DEMO-ZINE:1 --note "hi"
python3 -m rns_stall.client <dest> order.status --order-id <id>
python3 -m rns_stall.client <dest> manifest       # live API docs
```

Identified ops (cart/order/entitlement) sign the link with your buyer identity
(created on first use).

## Ops

`catalog.list` · `catalog.get` · `cart.get` · `cart.set` · `order.submit` ·
`order.status` · `entitlement.check` — full schemas in the live manifest.

## Payment rails

- **v1 (now): invoice.** Orders record the buyer identity; the merchant invoices
  via LXMF and settles out-of-band. The honest rail — it works with zero
  infrastructure, and it proves the plumbing the automated rails reuse.
- **M3: hybrid web checkout.** `pay.link` returns a short-lived HTTPS checkout
  URL (Stripe/Medusa); a webhook bridge flips the order paid; LXMF confirms.
- **M4: Monero.** `pay.xmr` returns a per-order subaddress from a **watch-only**
  wallet; a watcher confirms. No spend keys near the mesh, ever.

## Status / roadmap

v0.1 = M0–M2 of the plan: catalog, storefront + MeshData records, carts, orders,
entitlements, invoice rail. Next: LXMF worker (auto-confirmations), payment
rails, Medusa/ERPNext connectors. Non-goal, permanently: hosting a multi-vendor
marketplace — run your own stall.

## Family

[rns-time](https://github.com/wdunn001/rns-time) (when) ·
rns-geo (where) · **rns-stall** (what's for sale) —
built on [MeshAPI](https://github.com/wdunn001/meshapi) +
[MeshData](https://github.com/wdunn001/meshdata), indexed by
[Beacon](https://github.com/wdunn001/beacon).
