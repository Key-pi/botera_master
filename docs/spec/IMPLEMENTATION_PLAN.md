# BoteraMaster Implementation Plan

Status: approved implementation-planning document after `IMPLEMENTATION_PLAN_APPROVED` on 2026-10-08; early milestone implementation is in progress after explicit approvals.
Last updated: 2026-10-13.

This document translates the approved MVP specification into an implementation sequence. It does not create or approve future application code, migrations, dependencies, AGENTS.md changes, or project skills by itself.

## Approval Boundary

Approved for implementation planning:
- MVP product behavior in `PRODUCT_REQUIREMENTS.md`.
- Domain model direction in `DOMAIN_MODEL.md`.
- Data map in `SCHEMA_DRAFT.md`.
- Architecture decisions in `ARCHITECTURE_DECISIONS.md`.
- Operational runbook direction in `RUNBOOK_DRAFT.md`.
- Platform diagnostics taxonomy in `DIAGNOSTICS_TAXONOMY.md`.

Still requires explicit approval before future milestone work:
- project skills, if any
- dependencies or version changes not already approved
- migrations/models outside the currently approved milestone
- application code outside the currently approved milestone

## Recommended Stack

Backend:
- Django modular monolith.
- PostgreSQL production database.
- Redis broker.
- Celery workers plus scheduler/beat.
- aiogram 3.x for Telegram bot runtime, following the TableOS direction unless implementation discovers a concrete blocker.

Security/supporting libraries:
- `cryptography` for bot token encryption, preferably Fernet/MultiFernet through a small `core.security.crypto` wrapper.
- HMAC token fingerprinting through a dedicated secret from environment/secret storage.
- Environment-based configuration for secrets and deployment settings.

Frontend/admin:
- Start with Django admin or pragmatic server-rendered admin screens where possible.
- Add custom partner admin views for Calendar, Dashboard, Bot, and operational workflows that Django admin cannot make usable enough.
- Keep superuser admin pragmatic/Django-admin-like in MVP, with dedicated health/setup/problem views.

Deployment:
- Docker Compose MVP.
- Services: `web`, `bot-runner`, `celery-beat`, `notifications-worker`, `broadcast-worker`, `default-worker`, `postgres`, `redis`.

## Project Layout Direction

Recommended top-level Django apps:
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

Recommended per-app files:
- `models.py`
- `selectors.py`
- `services.py`
- `admin.py`
- `forms.py`
- `tasks.py` where the app owns async work
- `interfaces.py` only for stable cross-app contracts that would otherwise become messy

Rules:
- Non-trivial partner-scoped reads go through selectors.
- Business mutations go through services.
- Cross-app writes should call another app's service instead of casually mutating foreign models.
- Handlers/admin/actions stay thin and delegate to services/selectors.

## Integration Layer Direction

BoteraMaster should include a small `integrations` app/module early in implementation.

Purpose:
- Store and deliver integration events produced by BoteraMaster domain changes.
- Keep future Partnership/Discovery communication behind explicit boundaries.
- Prevent direct coupling to TableOS/BoteraMarket, Partnership, or Discovery models/tables.

Boundaries:
- `integrations` publishes facts, not partnership decisions.
- It must not contain Partnership rule-engine logic.
- It must not issue rewards, calculate cross-business conditions, or own partner-program state.
- It must not make core booking/appointment flows fail just because Partnership is unavailable.

Initial shape:
- Generic `IntegrationEvent`/outbox record.
- `event_id` UUID for idempotency.
- `event_type`, source product/business identifiers, integration-safe payload, status, attempts, timestamps, and retry timing.
- Service helper to create events from domain services.
- Celery task/worker path to deliver pending events later.
- Optional future subpackage such as `integrations/partnership/` for Partnership API client, schemas, and event mapping.

Operational rule:
- Domain services can create outbox events in the same local DB transaction as the source business change.
- Network delivery happens after commit through background jobs.
- No Telegram, HTTP, sleep, or retry loop runs inside the source business transaction.

## TableOS Reuse Policy

Direction:
- Start BoteraMaster as a clean Django project.
- Use TableOS as a reference implementation, not as a codebase to copy wholesale.
- Port small, proven patterns manually when they fit BoteraMaster's model.

Concepts worth reusing:
- Django modular-monolith shape with `apps`, `bot`, and `core`.
- aiogram polling runner direction.
- Runtime bot config validation before polling starts.
- Partner runtime middleware/context pattern.
- Partner-bound base model/query convention.
- Bot token encryption wrapper idea, expanded for key ids/fingerprints/key rotation.
- Partner-scoped admin filtering.
- Template rendering fallback idea, but implemented through BoteraMaster's multilingual registry/override design.
- Focused tests around bot runtime, navigation/pagination, and partner scoping.

Do not copy blindly:
- Hospitality business logic: tables, menu/cart/orders/billing/staff-call flows.
- TableOS `apps/users` access model with staff roles, section permissions, and presets.
- TableOS thick `PartnerBotSettings` content model, because BoteraMaster needs `SystemTemplate` + `PartnerTemplateOverride` per key/language.
- TableOS global Telegram blocked flag as-is; BoteraMaster communication state is partner-scoped on `ClientProfile`.
- Any TableOS shortcut that assumes cafe/shop workflows instead of appointment time safety.

Rationale:
- The TableOS stack is a good proof that Django + aiogram + Celery + Docker Compose works for this product family.
- A clean start avoids dragging cafe-specific coupling into BoteraMaster and keeps the solo-master MVP understandable.

## Foundation Milestone

Goal:
- Create the project skeleton and core conventions without implementing large business workflows yet.

Deliverables:
- Django project/app skeleton.
- Environment/settings split.
- PostgreSQL/Redis local Docker Compose.
- Base UUID/timestamp model primitives.
- Money helper for minor units.
- Timezone helper conventions.
- Partner-bound base/query conventions.
- Shared masking/redaction helpers.
- Token crypto wrapper and token fingerprint helper.
- Platform diagnostic skeleton.
- Audit/diagnostics/retention contracts without full UI polish.
- Minimal integration/outbox contracts and app skeleton.

High-value tests:
- token encryption/decryption
- key-ring decrypt behavior
- token fingerprint stability/non-reversibility expectation
- masking/redaction helpers
- partner-bound queryset/service guard basics
- integration event idempotency/status basics

Exit criteria:
- Project boots locally.
- Tests run.
- No business workflow depends on unscoped partner data access.
- Later milestones have a lightweight way to emit audit/diagnostic events from the start.
- Later milestones have a lightweight way to record integration events without network I/O in domain transactions.

## Tenant And Admin Milestone

Goal:
- Establish partner setup, admin identity, and tenant isolation before bot and booking logic.

Deliverables:
- Partner model and lifecycle states.
- Account/owner access.
- Superuser-created partner onboarding.
- Setup-required checks.
- Partner admin scoping.
- Basic partner dashboard placeholder.

High-value tests:
- partner owner sees only own partner data
- superuser can access all partner scopes
- setup-required state blocks client-facing bot eligibility

Exit criteria:
- A superuser can create a partner and owner account.
- Partner owner can log in and see only their area.
- Incomplete partner cannot be treated as bot-runtime eligible.

## Bot Runtime Milestone

Goal:
- Connect partner bot instances safely before building full booking flows.

Deliverables:
- BotInstance model with encrypted token, key id, token fingerprint, masked display.
- Bot status handling: active, paused, suspended, error.
- Token validation at save/check time where possible.
- Duplicate-token safety.
- Simple runner loading eligible active bots at startup.
- Runtime partner context middleware.
- Bot health/status fields.

High-value tests:
- invalid token handling
- duplicate active token detection
- encrypted token missing/corrupted key failure
- runtime eligibility filtering
- partner/bot paused/suspended blocks handling

Exit criteria:
- Active eligible bot can run through polling in development.
- Unsafe/incomplete bot config is skipped with masked diagnostics.
- Token/status changes require runner restart/redeploy in MVP and docs/admin text reflect this.

## Identity And Catalog Milestone

Status: implemented on 2026-10-12.

Goal:
- Establish client identity/profile separation and partner-defined service catalog.

Deliverables:
- TelegramIdentity.
- ClientProfile with partner-scoped communication flags.
- Manual client profile support.
- Basic linking suggestion by phone for manual profile + Telegram identity.
- ServiceCategory and Service.
- Partner admin CRUD for categories/services.
- Bot pagination primitives for lists.

High-value tests:
- one Telegram identity can have multiple partner client profiles
- manual client without Telegram identity works
- marketing opt-out and bot_unreachable are partner-scoped
- inactive category/service is hidden from booking but historical data remains displayable

Exit criteria:
- Partner can create categories/services.
- Bot/admin can resolve or create client profile in partner scope.

## Schedule Milestone

Status: implemented on 2026-10-12.

Goal:
- Build the time availability engine before appointments rely on it.

Deliverables:
- BookingResource with immutable advisory_lock_key.
- Default booking resource per solo partner.
- AvailabilityWindow.
- BlockedTime.
- AvailabilityTemplate and template windows.
- Slot generation for selected service durations.
- Booking horizon and slot step settings.
- Near-fit tolerance.
- Conflict preview/records where needed.

High-value tests:
- slot generation with availability windows
- blocked time removes slots
- booking horizon limits slots
- multi-service duration
- near-fit only extends past availability window end and never overlaps blocked/confirmed time
- no past availability/block creation through normal service

Exit criteria:
- Given partner, booking resource, selected services, and date range, service can return valid slots.
- Schedule data is partner/resource scoped and indexed in the planned query shape.

## Appointment Core Milestone

Status: implemented on 2026-10-12.

Goal:
- Implement appointments, snapshots, statuses, and confirmed overlap safety.

Deliverables:
- Appointment.
- AppointmentServiceItem.
- Appointment status transitions.
- Confirmed-overlap service with transaction + `pg_advisory_xact_lock(namespace, advisory_lock_key)`.
- Requested appointment creation without slot blocking.
- Manual appointment creation.
- Basic appointment admin views/actions.

High-value tests:
- confirmed appointments cannot overlap for one booking resource
- requested appointments may overlap but confirmation re-checks availability
- concurrent confirmation/create is protected by advisory lock helper
- service item snapshots remain stable after service edits
- final statuses do not block slots

Exit criteria:
- System can create requested and confirmed appointments safely.
- Manual appointments can be created with manual clients and custom service item lines.

## Client Booking Flow Milestone

Status: implemented on 2026-10-12.

Goal:
- Build the client-facing happy path before advanced reschedule/penalty flows.

Deliverables:
- Start/profile language handling.
- Category selection.
- Multi-service selection.
- Booking summary.
- Date and slot selection.
- Final confirmation.
- Auto/manual confirmation mode.
- Requested cancellation by client.
- Basic appointment history/profile views with pagination.

High-value tests:
- booking flow revalidates selected services and slot before creation
- auto-confirm creates confirmed appointment and blocks slot
- manual-confirm creates requested appointment
- near-fit always becomes requested
- Telegram pagination does not exceed practical message/keyboard limits

Exit criteria:
- First real pilot client can book through a partner bot.
- Partner receives new confirmed/requested appointment notification path.

## Notifications Milestone

Status: implemented on 2026-10-12.

Goal:
- Make reminders and service notifications durable/idempotent.

Deliverables:
- ReminderRule.
- ScheduledNotification.
- DeliveryAttempt.
- Celery/beat dispatchers.
- Client reminders for confirmed appointments.
- Master reminders.
- Morning digest.
- Stale requested reminders.
- Failed reminder marker and manual retry action.
- Quiet hours behavior.

High-value tests:
- reminder creation only after confirmed
- old reminders stale/cancel on move/cancel/complete/no_show
- quiet-hours delayed reminders skip if obsolete
- idempotency prevents duplicate sends
- terminal Telegram failure marks partner-scoped client unreachable

Exit criteria:
- Confirmed appointments receive scheduled reminders.
- Failed reminders become visible to partner after retry exhaustion.

## Reschedule And Proposal Milestone

Status: implemented on 2026-10-13.

Goal:
- Add appointment change/reschedule flows using unified proposals.

Deliverables:
- AppointmentProposal.
- AppointmentProposalOption.
- AppointmentProposalOptionItem.
- Master proposed alternatives for requested appointment.
- Master proposed reschedule/change for confirmed appointment.
- Client-initiated reschedule following auto/manual mode.
- Proposal expiration handling.

High-value tests:
- new pending proposal supersedes previous pending proposal
- accepted option re-checks availability
- requested alternative proposal expiry cancels requested appointment
- confirmed appointment remains unchanged while proposal pending

Exit criteria:
- Partner and client can handle the normal reschedule/change paths without manual DB intervention.

## Penalties And Finance Milestone

Status: implemented on 2026-10-13.

Goal:
- Add penalties, no-show/late-arrival, basic reports, and expenses.

Deliverables:
- Penalty.
- PenaltyApplication.
- Late cancellation penalty creation.
- No-show and late-arrival penalty creation/suggestion.
- Apply unpaid penalties to appointment total.
- Paid/waived/released/settled flows.
- Expenses.
- Actual and expected revenue reports.

High-value tests:
- penalty lifecycle: unpaid, applied, settled, released, paid, waived
- cancelling appointment before settlement releases applied penalty
- money totals use minor units
- reports include penalties correctly and use partner timezone

Exit criteria:
- Partner can manage penalty debt and basic finance/reporting without spreadsheet fallback for MVP cases.

## Content, Messaging, Broadcasts Milestone

Status: implemented on 2026-10-13.

Goal:
- Complete flexibility and engagement features after booking/notification core is safe.

Deliverables:
- SystemTemplate and PartnerTemplateOverride.
- Template validation with placeholder allowlists/sample contexts.
- Partner content admin with language tabs and reset-to-default.
- Client-master messaging through bot.
- BroadcastCampaign, BroadcastTemplate, BroadcastRecipient.
- Broadcast filters and buttons.
- Broadcast pacing/backoff.
- Marketing opt-out.

High-value tests:
- template unknown placeholder blocks save
- fallback to system default per language
- message delivery terminal failure marks client unreachable
- broadcast excludes opted-out/unreachable clients
- broadcast resumes from unsent recipients after worker restart

Exit criteria:
- Partner can customize content, message clients, and send basic marketing broadcasts safely.

## Audit, Diagnostics, Cleanup Milestone

Goal:
- Finish operational visibility and data hygiene.

Note:
- The skeleton and emission contracts start in the Foundation milestone.
- This milestone finishes partner/superuser views, cleanup jobs, retention behavior, and health/problem polish.

Deliverables:
- Partner-visible AuditEvent.
- PlatformDiagnostic taxonomy implementation.
- WorkerHeartbeat/current health records.
- Superuser health/problem views.
- Cleanup jobs for retain_until records.
- Runbook command implementation and docs update.

High-value tests:
- partner audit visibility is partner-scoped
- diagnostics redact sensitive payloads
- cleanup deletes only cleanup-eligible expired records
- worker health thresholds map to OK/Warning/Critical

Exit criteria:
- Superuser can see operational health and problems.
- Partner can see business-friendly activity log.
- Noisy records clean up without touching business history.

## First Pilot Cut

If implementation gets too large, the smallest useful pilot should keep:
- partner/admin setup
- bot token/runtime
- client identity/profile
- catalog
- availability windows/basic calendar
- appointment creation/confirmation
- client booking flow
- basic reminders
- partner appointment/client admin
- audit/diagnostics enough to debug pilot issues

Features that may be delayed inside MVP implementation if needed:
- polished mobile calendar interactions
- multiple saved availability templates
- broadcasts
- penalties beyond no-show/late-cancel basics
- expenses/reports polish
- full partner content override breadth
- advanced proposal UI polish

Do not cut:
- tenant isolation
- encrypted bot tokens
- confirmed overlap protection
- appointment snapshots
- reminder idempotency
- partner-scoped client profiles

## Approval Checklist Review

Recommended approval state:
- Milestone order is approved as the implementation spine.
- Foundation must include small audit/diagnostic/retention contracts, while full visibility/cleanup remains late.
- First Pilot Cut is approved as the practical fallback if the full MVP grows too large.
- Start from a clean Django project and manually adapt TableOS concepts only where they fit.
- Keep the approved stack direction: Django, PostgreSQL, Redis, Celery/beat, aiogram 3.x, Docker Compose.
- Do not create AGENTS.md, project skills, app code, models, migrations, or dependencies until explicit implementation approval.

Recommended next step after approval:
- Draft `AGENTS.md` and project rules that encode the architectural guardrails before writing application code.

## Pre-Code Approval Checklist

Before writing code, explicitly approve:
- final Django project/package structure
- dependency list
- implementation milestone order
- minimum pilot cut
- AGENTS.md/project rules
- clean-project/TableOS-reference policy

Approval phrase recommendation:
- `IMPLEMENTATION_PLAN_APPROVED`
