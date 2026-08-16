# +type: wiki
# +title: rns-stall — getting started
# +description: Stand up your own stall on the mesh in an afternoon.
# +tags: rns-stall, guide

>Getting started

>>1. Catalog

One YAML file is the whole shop:

`=
shop:
  name: My Stall
  currency: USD
  invoice_note: "LXMF invoice follows; pay by arrangement."
items:
  - sku: THING-1
    title: A Thing
    price: 12.00
    availability: in_stock    # in_stock|made_to_order|out_of_stock|digital
    kind: physical            # physical|digital|service
`=

>>2. Run stalld

`=
python3 -m rns_stall.server --catalog catalog.yaml --pages-out pages
`=

It prints your destination hash (your shop's address — PUBLISH it, and BACK UP
the identity file: losing it breaks every published pointer), renders the
storefront pages, and announces on the mesh while healthy. Edit the catalog any
time; pages re-render within a minute.

>>3. Serve the pages

Point a NomadNet node's pages dir at the rendered output. Run it as its OWN
node with its own identity (a sub-page of another node is not independently
discoverable). Beacon will crawl the pages and index your items via their
MeshData `!product`! blocks.

>>4. Take orders

Watch stalld's log (each order logs an ORDER line), or poll the db. v1 rail is
invoice: you contact the buyer (their identity hash is on the order) via LXMF.
Grant digital goods with an entitlement row; buyers verify with
`!entitlement.check`!.

>>Ops reference

Discover live docs from the service itself:

`=
python3 -m rns_stall.client <dest> manifest
`=

`[back`:/page/index.mu]
