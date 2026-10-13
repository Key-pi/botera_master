# BoteraMaster repository instructions

Status: approved project rules after `PROJECT_RULES_APPROVED` on 2026-10-11.
Foundation milestone implementation started on 2026-10-12 after explicit user
approval. Tenant And Admin milestone implementation started on 2026-10-12
after explicit user approval. Bot Runtime milestone implementation started on
2026-10-12 after explicit user approval. Identity And Catalog milestone
implementation started on 2026-10-12 after explicit user approval. Schedule
milestone implementation started on 2026-10-12 after explicit user approval.
Appointment Core milestone implementation started on 2026-10-12 after explicit
user approval. Client Booking Flow milestone implementation started on
2026-10-12 after explicit user approval. Notifications milestone
implementation started on 2026-10-12 after explicit user approval.
Reschedule And Proposal milestone implementation started on 2026-10-13
after explicit user approval. Penalties And Finance milestone implementation
started on 2026-10-13 after explicit user approval.
Application code is allowed only within the currently approved milestone scope.

## System summary

BoteraMaster is planned as a Django modular monolith for Telegram-first
scheduling, CRM, reminders, messaging, broadcasts, and lightweight finance for
solo masters and service professionals.

The MVP is a standalone product/project. TableOS/BoteraMarket is a reference
project, not a codebase to copy wholesale. Partnership and Discovery are future
separate products/projects. BoteraMaster may publish integration events for
them, but it must not own their business logic.

This repository contains planning/specification documents and approved early
implementation. Do not create application code, Django apps, models,
migrations, dependencies, project skills, or deployment files outside the
currently approved milestone scope.

## Canonical documents

Read these before implementation work:

- product behavior: `docs/spec/PRODUCT_REQUIREMENTS.md`
- domain model: `docs/spec/DOMAIN_MODEL.md`
- architecture decisions: `docs/spec/ARCHITECTURE_DECISIONS.md`
- data map: `docs/spec/SCHEMA_DRAFT.md`
- implementation sequence: `docs/spec/IMPLEMENTATION_PLAN.md`
- operational direction: `docs/spec/RUNBOOK_DRAFT.md`
- diagnostics direction: `docs/spec/DIAGNOSTICS_TAXONOMY.md`
- ecosystem guardrails: `docs/spec/ECOSYSTEM_GUARDRAILS_DRAFT.md`
- approval and open questions: `docs/discovery/DECISION_LOG.md`,
  `docs/discovery/OPEN_QUESTIONS.md`

`docs/spec/IMPLEMENTATION_PLAN.md` is approved as an implementation plan, but
future milestone implementation remains gated by explicit approval. Do not
treat draft or advisory text as stronger than approved decisions.

## Mandatory workflow

1. Identify the owning module and adapter boundary before editing.
2. Check relevant docs and existing decisions before making product or
   architecture assumptions.
3. Keep changes scoped to the requested milestone or bug.
4. Put business mutations in owner services and non-trivial reads in selectors.
5. Keep handlers, admin actions, Celery tasks, management commands, and bot
   routers thin. They call services/selectors; they do not reimplement
   invariants.
6. Review tenant scope, transaction boundaries, idempotency, and background
   delivery behavior before changing a workflow.
7. Update canonical docs when a rule, boundary, or approved product behavior
   changes.

## Project layout direction

The approved top-level Django app direction is:

- `core`
- `partners`
- `accounts`
- `bot`
- `clients`
- `catalog`
- `schedule`
- `appointments`
- `notifications`
- `broadcasts`
- `messaging`
- `finance`
- `audit`
- `integrations`

Per-app conventions:

- `models.py` owns local persistent data.
- `selectors.py` is read-only query logic.
- `services.py` owns use cases, writes, transactions, locks, and invariants.
- `admin.py`, bot handlers, API views, tasks, and commands are adapters.
- `interfaces.py` is only for stable cross-app contracts that would otherwise
  become messy.
- Extra layers such as repositories, policies, engines, or event buses require a
  concrete consumer and a clear reason.

## Tenant and access rules

- `partner` is the MVP tenant boundary.
- Client-facing text may say "master"; internal code and docs use `partner`.
- All partner-owned records must be explicitly partner-scoped.
- Derive partner scope from trusted actor/runtime context, not from callback
  payloads, hidden form fields, visible UI, or submitted foreign keys.
- Before a mutation, owner services must verify that actor, target, and related
  objects belong to the same partner unless the actor is superuser.
- Superuser may operate across partners, but sensitive data still requires
  masking/log hygiene.
- MVP has one owner/master role per partner. Future studios and employee roles
  must remain possible but are not MVP.

## Bot runtime rules

- Each partner has their own Telegram bot token.
- MVP uses a simple polling bot runner that loads eligible active bots at
  startup. Hot reload is not part of MVP.
- Bot token/status changes may require runner restart/redeploy in MVP.
- Bot handlers must always resolve and preserve partner/bot context before
  partner-scoped work.
- Suspended/paused/error/setup-incomplete partners must not process normal
  client booking/profile flows.
- Telegram/network I/O must not happen inside source business transactions.

## Scheduling and appointment rules

- Availability is based on concrete availability windows, blocked time, booking
  horizon, slot step, selected service durations, and confirmed appointments.
- `requested` appointments may overlap as requests.
- `confirmed` appointments must not overlap for the same booking resource.
- Solo-master MVP starts with one default booking resource per partner; future
  studios can add more booking resources.
- Confirmed overlap protection uses a transactional service-layer check plus a
  PostgreSQL transaction-level advisory lock keyed by booking resource.
- Lock-protected operations must acquire the lock, run a fresh overlap query,
  write or return a conflict, then commit. Enqueue notifications/reminders after
  commit.
- No Telegram, HTTP, sleep, or retry loop runs inside a lock-protected
  transaction.
- Appointment service item rows store snapshots for every appointment, even for
  one-service appointments.
- Money is stored as integer minor units with explicit currency.

## Background jobs and delivery rules

- Celery + Redis + scheduler/beat is the MVP direction.
- Durable business delivery outcomes live in PostgreSQL records.
- Queue direction: `notifications`, `broadcasts`, `default`, and `maintenance`.
- Broadcast work must not delay urgent reminders.
- Reminder, notification, broadcast, and integration delivery must be
  idempotent.
- Business services enqueue jobs only after commit.
- Failed Telegram sends should update durable delivery/diagnostic records and
  partner-visible state when useful.
- Retention/cleanup applies only to cleanup-eligible noisy records and must not
  delete business history.

## Integrations and ecosystem rules

- BoteraMaster, BoteraMarket/TableOS, Partnership, and Discovery remain separate
  products/projects.
- Do not import models across products or read another product's tables.
- Cross-product communication goes through explicit APIs/events/projections.
- BoteraMaster should include a small `integrations` app/module early.
- `integrations` publishes facts through a minimal local `IntegrationEvent`
  outbox. It does not evaluate Partnership conditions, issue rewards, or own
  partner-program state.
- Domain services may create an integration event in the same local transaction
  as the source business change. Delivery to Partnership happens later through
  background workers.
- If Partnership or Discovery is unavailable, core BoteraMaster booking,
  reminder, messaging, and admin workflows must keep working.
- Future partner-program panels inside BoteraMaster/BoteraMarket are clients of
  the separate Partnership project.

## Security and sensitive data rules

- Bot tokens are encrypted at rest and never shown raw after save.
- Duplicate active token detection uses a keyed non-reversible fingerprint, not
  raw token comparison or deterministic ciphertext.
- Client phone, client-master messages, and private master notes are
  partner-scoped business data. They are not app-field-encrypted in MVP, but
  require strict access control and log masking.
- Technical logs, diagnostics, exceptions, audit payloads, and raw external
  payload persistence must redact or mask bot tokens, phones, message text,
  private notes, and other sensitive values.
- Shared masking/redaction helpers belong in `core`.
- Raw Telegram payloads must not be persisted without filtering/masking.

## TableOS reuse policy

Use TableOS conceptually for:

- Django modular-monolith shape.
- aiogram polling runner direction.
- runtime config validation and duplicate-token safety.
- partner runtime middleware/context.
- encrypted bot token handling, expanded for key ids/fingerprints.
- partner-scoped admin filtering.
- template fallback ideas.
- focused tests around runtime, navigation/pagination, and partner scoping.

Do not copy blindly:

- hospitality business logic: tables, menu, cart, orders, billing, staff calls.
- TableOS `apps/users` role/section-permission complexity.
- TableOS thick `PartnerBotSettings` content model.
- global Telegram blocked flags.
- cafe/shop assumptions that break appointment time safety.

## Tests and definition of done

Test coverage should focus on behavior that can hurt the product:

- tenant isolation and partner-scoped access.
- token encryption/fingerprint/masking.
- advisory lock and confirmed-overlap safety.
- appointment status transitions and snapshots.
- reminder/notification idempotency.
- integration outbox idempotency and retry behavior.
- sensitive-data redaction.
- bot pagination/message-limit safety.

A change is done only when the relevant owner/adapters, tenant checks,
transactions/locks, background jobs, tests, and docs have been reviewed.

## Prohibited without explicit approval

- Creating application code before implementation approval.
- Adding dependencies or pinning versions before dependency approval.
- Creating migrations/models outside the approved milestone.
- Broad rewrites, module moves, formatting sweeps, or dependency upgrades
  bundled into unrelated work.
- Direct cross-product imports or shared-table coupling.
- Partnership rule engine inside BoteraMaster.
- Discovery catalog/bot implementation inside BoteraMaster.
- Kafka, RabbitMQ, Kubernetes, service mesh, or distributed auth for MVP unless
  explicitly approved.
- Destructive data repair, cross-tenant cleanup, or history deletion without an
  approved audit/migration plan.
