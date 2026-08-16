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

1. Browse the storefront pages (this node, or any stall you found via Beacon).
2. Call the shop service with a MeshAPI client — identified ops sign the link:

`=
python3 -m rns_stall.client <dest> catalog.list
python3 -m rns_stall.client <dest> order.submit --item DEMO-ZINE:1
python3 -m rns_stall.client <dest> order.status --order-id <id>
`=

3. v1 payment rail = invoice (LXMF). Stripe-link and Monero rails are on the
   roadmap (M3/M4).

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
