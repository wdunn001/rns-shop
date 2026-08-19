# rns-shop, the founding plan

*Drafted 2026-08-16 (as "rns-stall"), executed over the following 48 hours. Kept as the
project's founding document; status annotations show what shipped. Self-hosted copy, supersedes the original claude.ai artifact.*

## 1 · Thesis

**Nobody had ever built a store on Reticulum.** Not attempted-and-failed, unattempted.
Research over the ecosystem found zero prior art beyond one unfinished Cashu payments
experiment. Meanwhile every primitive a store needs already existed:

- NomadNet hands every page request a **cryptographically-proven buyer identity**
  (`remote_identity`), a customer account with no signup, no password, no PII.
- **MeshAPI** gives services a discoverable, documented, auth-aware RPC surface over RNS
  Links, with a live micron explorer.
- **MeshData** gives pages invisible machine-readable metadata, what makes listings
  searchable.
- **Beacon** crawls and indexes the mesh, a `type: product` record is all it takes for an
  item to surface in a shop view.

This is not a store for one merchant. It is **commerce infrastructure for the mesh**: a
toolkit anyone can self-host, point it at a catalog, get a storefront node, appear in
search.

**Explicit non-goal (permanent):** no hosted multi-vendor marketplace. Each merchant runs
their own node and carries their own heat.

## 2 · The two contracts

- **MeshData `product` type** (shipped, `wdunn001/meshdata`): commerce fields mirroring
  schema.org Product/Offer, price, currency, availability, sku, vendor, and `shop` (the
  MeshAPI destination where buy ops live, the doorway from a search result to a seller).
  SPEC §7 adds consumer guidance: schema bonus caps, date sanity, type cross-check,
  canonical dedupe.
- **MeshAPI `rnshop` service** (shipped): catalog.list/get · cart.* · order.submit/status ·
  entitlement.check · delivery.get · pay.link/pay.xmr · profile.get/set, self-documenting
  via `__manifest__`.

## 3 · Architecture (all shipped)

```
catalog source (yaml | medusa:// | squarespace://)
      -> shopd, MeshAPI service: carts, orders, entitlements, payments, profiles
      -> render, micron storefront w/ MeshData product records (+ exec checkout pages)
      -> ⛺ NomadNet node (own identity)  <- buyers (NomadNet / MeshChat)
      -> LXMF worker, invoices, receipts, merchant messages; inbox auto-discovery
      -> Beacon indexes every listing (freshness + shopping-intent ranking)
      -> admin portal (Authentik-gated web), orders, revenue, messaging, catalog editor
```

Three separable layers: **catalog** (the schema is the API), **storefront** (static browse +
exec checkout), **discovery** (Beacon, zero shop-side code).

## 4 · Payment rails (provider interface, one class + one registry line per rail)

- **Invoice** (shipped), LXMF invoice, merchant marks paid; worker entitles + receipts.
- **Link / hybrid web checkout** (mechanism + mock processor proven end-to-end), any
  processor via URL template; webhook bridge flips orders paid. Rule learned: buyer-visible
  URLs must be public, never internal addresses.
- **Monero watch-only** (code, dormant), per-order subaddresses, watcher confirms; no
  spend keys near the mesh, ever.

## 5 · Milestones, final scorecard

| Milestone | Status |
|---|---|
| M0 MeshData product spec | shipped |
| M1 Catalog on the mesh (Beacon-visible) | shipped |
| M2 Orders end-to-end + LXMF | shipped (first order ever: `7b66ec61`, $5 zine) |
| M3 Digital delivery + link rail | shipped (mock processor completes the full loop) |
| M4 XMR + entitlements | code shipped, awaits a view-only wallet |
| M5 Connectors + merchant suite | Squarespace connector, shipping estimates, carts, admin portal, catalog editor, buyer messaging |

Beyond the plan: buyer profiles + greeted storefront, fail-closed ships-to, cart UI,
region-aware everything, four micron client-compat laws learned in live fire (no literal
link-vars · no decorative brackets · no `*` submit-all · no field named `name`).

## 6 · Risks that proved real

- Micron client diversity: four compatibility scars, each found by a real client.
- Shared mutable deploy dirs across concurrent build sessions: bit twice; house rule now, deploy from a fresh pull of origin/main, sync /opt after.
- MeshData as a ranking input is an adversarial surface: bounded per SPEC §7.
- Mesh latency: solved architecturally (static browse pages, few small dynamic hops).

*The rest is history, see the repo README and the memory of whoever maintains this next.*
