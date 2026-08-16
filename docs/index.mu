# +type: wiki
# +title: rns-stall — commerce over the mesh
# +description: Self-hosted e-commerce for Reticulum/NomadNet: catalog, carts, orders, entitlements. MeshData-native, Beacon-discoverable.
# +tags: rns-stall, commerce, meshapi, meshdata

>rns-stall

Self-hosted commerce over Reticulum. Point it at a catalog file, get:

- a micron storefront node (static, fast, LoRa-friendly)
- a MeshAPI service: catalog · carts · orders · entitlements
- MeshData `!product`! records on every item page — so listings appear in
  Beacon's shop view automatically

Your buyer identity is your RNS identity. No accounts, no passwords, no PII
unless you're shipping something physical.

>>How buying works

`F6d8┌─`f  1. Browse the catalog (this node, or any stall Beacon found you)
`F6d8│`f   2. Set a quantity and hit `!BUY NOW`! — right on the item page
`F6d8│`f   3. Your RNS identity IS your account (no signup, no password)
`F6d8│`f   4. Confirmation + receipt arrive by LXMF
`F6d8└─`f  5. Track orders — and receive digital goods — on the `!my orders`! page

Payment: invoice by LXMF (default), plus Stripe-link handoff and Monero
(watch-only) where the merchant enables them.

>>For builders (programmatic buying)

The shop is also a MeshAPI service — everything the buttons do, scriptable:

`=
python3 -m rns_stall.client <dest> catalog.list
python3 -m rns_stall.client <dest> order.submit --item DEMO-ZINE:1
python3 -m rns_stall.client <dest> delivery.get --sku DEMO-ZINE
`=

>>Run your own stall

`=
git clone https://github.com/wdunn001/rns-stall.git
cd rns-stall && cp examples/catalog.yaml catalog.yaml   # edit it
python3 -m rns_stall.server --catalog catalog.yaml
`=

Serve the generated pages/ dir from a NomadNet node (its own node — announced,
discoverable). Full guide: `[getting started`:/page/getting-started.mu]

>>The pieces

- `!MeshAPI`! — service discovery + docs (`!__manifest__`! op)
- `!MeshData`! — the `!product`! record type (schema.org Product/Offer mapped)
- `!Beacon`! — the mesh search engine that surfaces every stall's listings

source: https://github.com/wdunn001/rns-stall
