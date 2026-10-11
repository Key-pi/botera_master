# Botera Ecosystem Guardrails Draft

Status: draft discussion document, extracted from advisory ecosystem discussion on 2026-10-08. This is not a full approved architecture for TableOS/BoteraMarket, Partnership, or Discovery.

This document captures useful cross-product principles that can influence BoteraMaster without turning the MVP into a premature platform rewrite.

## Recommended Direction

- Keep BoteraMaster and TableOS/BoteraMarket as separate products/projects.
- Build Partnership and Discovery as separate projects when their time comes.
- Do not merge existing TableOS into a shared platform monolith just to make future ecosystem ideas look cleaner.
- Let products communicate through explicit integration boundaries: API calls, integration events, and minimal outbox-style delivery.
- Avoid direct model imports between products.
- Avoid shared-table coupling between products.
- Domain data stays inside the product that owns it.
- Cross-product identity matching can use Telegram user id as an external signal, but internal primary keys should remain product-owned UUIDs.
- Partnership and Discovery must not be required for BoteraMaster's core booking/reminder/admin flows to work.
- Start physically simple: one VPS, Docker Compose, PostgreSQL, Redis, HTTP/Celery are enough for the first stage.
- Keep the architecture logically separable so apps can later move to separate servers/databases when there is a real reason.

## What BoteraMaster Should Lay Down Now

These are lightweight foundations that fit the MVP and help future ecosystem work:

- Stable UUID identifiers for partner/business, client/profile, appointments, and integration events.
- Telegram identity stored as external identity data, not as the internal primary key.
- Clear partner/business public representation, even if Discovery is not implemented yet.
- Source/referral tracking fields for future deep links and attribution.
- A future-safe integration boundary, for example an `integrations` package/module, without implementing Partnership rule logic in MVP.
- Event-friendly domain service points, especially around appointment completed/cancelled/no-show, client created/linked, partner/business created/updated/disabled.
- A minimal local `IntegrationEvent`/outbox foundation early in the implementation, kept generic and small.
- Idempotency-friendly event identifiers for integration events.
- Source application/business identifiers in events so Partnership can distinguish BoteraMaster and BoteraMarket senders.

## Minimal Outbox Direction

BoteraMaster should be able to save an integration event in the same local transaction as the domain change that produced it, then deliver it later through a background worker.

Suggested module shape:

```text
integrations/
    __init__.py
    events.py
    models.py
    services.py
    selectors.py
    tasks.py
    partnership/
        __init__.py
        client.py
        events.py
        schemas.py
```

Recommended properties:
- `event_id` UUID for idempotency.
- `event_type`.
- `source_product`, for example `botera_master`.
- `source_business_id` / partner UUID.
- `payload` with only integration-safe data.
- `status`, attempts, timestamps, and `next_retry_at`.
- no blocking HTTP call to Partnership inside the core booking/appointment transaction.
- retries through Celery/Redis, with PostgreSQL as durable state.

Important boundary:
- The outbox stores facts produced by BoteraMaster.
- It does not evaluate Partnership conditions.
- It does not issue rewards.
- It does not own cross-business program state.

Recommended flow:

```text
domain service changes local state
        |
        v
same local DB transaction creates IntegrationEvent
        |
        v
commit succeeds and user/product flow completes
        |
        v
background worker delivers event to Partnership
        |
        v
delivery success marks event sent; temporary failure schedules retry
```

Rules:
- No Partnership HTTP call inside the domain transaction.
- No user-facing flow should fail only because Partnership is down.
- Event payloads should be stable, small, and integration-safe.
- Retry must be idempotency-friendly through `event_id`.
- Delivery state is operational/integration state, not core appointment state.

Initial BoteraMaster event candidates:
- `business.created`
- `business.updated`
- `business.disabled`
- `customer.registered`
- `customer.linked`
- `appointment.completed`
- `appointment.cancelled`
- `appointment.no_show`
- `penalty.created`
- `penalty.paid`

Exact event names/payloads are finalized during implementation, but they should stay generic enough for Partnership to consume without knowing BoteraMaster internals.

## What Not To Add To BoteraMaster MVP Yet

- Partnership rule engine.
- Discovery bot/catalog implementation.
- Kafka, RabbitMQ, Kubernetes, service mesh, or API gateway.
- Cross-product global customer table inside BoteraMaster.
- Hard dependency on Partnership or Discovery for appointment booking, reminders, payments, or admin workflows.
- Complex distributed auth across products.
- Full event sourcing.

## Partnership Panel Direction

BoteraMaster and BoteraMarket may eventually expose a partner-facing admin panel for cross-business programs.

The panel can show:
- open conditions from other partners/businesses.
- the current partner's own conditions.
- incoming/outgoing proposals.
- accepted active programs.
- basic stats, reward grants, and redemptions.

But the panel should behave as a client of the separate Partnership project:
- Program, condition, reward, proposal, agreement, grant, and redemption truth lives in Partnership.
- BoteraMaster/BoteraMarket call Partnership APIs or consume Partnership projections.
- BoteraMaster/BoteraMarket do not duplicate the Partnership rule engine locally.
- If Partnership is temporarily down, core product flows still work; the panel can show degraded/unavailable state.

## Future Product Boundaries

BoteraMaster:
- Owns masters/partners, bots, clients, appointments, schedule, reminders, broadcasts, penalties, and finance for masters.

TableOS/BoteraMarket:
- Owns venue/shop/cafe-specific domain data and bot flows.

Partnership:
- Owns cross-business programs, conditions, rewards, grants, redemptions, and cross-product loyalty rules.
- Owns proposal/accept/decline/agreement state for partner programs.
- Consumes product events and decides whether rewards should be issued.
- Does not own appointment/order business logic.

Discovery:
- Owns public directory/search UX and may later show rewards.
- Should read from a directory projection rather than live-querying every product for every user request.
- Should be a sales funnel and network-effect layer, not a blocker for product operation.

## Open Discussion Points

- Whether BoteraMaster should create a minimal public business listing model now, or defer it until Discovery work starts.
- Exact naming after TableOS is renamed to BoteraMarket.
- Whether Partnership and Discovery should be Django projects, smaller Python services, or another shape when their implementation begins.
- Exact MVP event types and payloads to publish from BoteraMaster first.
- Exact shape of the future Partnership admin panel inside BoteraMaster/BoteraMarket.
