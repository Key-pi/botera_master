# Open Questions

Status: consistency pass on 2026-10-02 before implementation planning.

This file contains questions that remain open after discovery approval and technical-spec interviews. They are separated by whether they block implementation planning, are design details that can be resolved during implementation, or are explicitly post-MVP/future topics.

## Blocks Implementation Planning

These do not reopen the product direction, but they should be settled before creating code, models, migrations, or final architecture instructions.

No blocking open questions remain at the product/specification level before implementation planning. Exact code-level names, migration details, command names, and UI polish still need to be handled during implementation planning/build.

## Implementation Design Details

These are important for quality, but they should not block the high-level final MVP specification if we consciously leave them for detailed implementation design.

- What exact calendar visual treatment, component behavior, conflict UI, proposal UI, and mobile interactions should be used?
- What exact localized labels and visual/detail treatment should appointment history use for cancelled, rescheduled, completed, no-show, penalty, and failed-notification events?
- What exact localized copy should Telegram pagination controls use for each supported language?
- What exact default localized wording should be seeded for the approved multilingual content registry in `uk`, `ru`, and `en`?
- What exact visual/copy treatment should the partner onboarding checklist/setup guide use in admin?
- What exact broadcast backoff intervals, global broadcast safety limits, worker thresholds, and alert thresholds should be used beyond the approved 0.5 seconds per bot minimum pacing?
- What confirmed MVP features can be delayed if implementation gets too large, and what is the minimum pilot path for the first real partner?

## Resolved During Later Consistency Passes

- Master-facing Telegram delivery uses an explicit partner-scoped owner/master Telegram contact binding created through a signed deep link in the partner's own bot. The bot token is not treated as proof of owner identity. MVP can start with one primary contact while keeping the model expandable.
- Exact min/max values for slot step, booking horizon, reminders, quiet hours, cancellation deadline, near-fit tolerance, stale requested reminders, and morning digest are approved and belong in settings validation.
- Multilingual content registry is approved at key group/editable-vs-system-only/placeholder-validation level. Exact localized default wording remains an implementation/copy task.
- `ClientProfile` communication flags stay on the partner-scoped client profile in MVP; no separate communication-state model is required.
- Telegram delivery failures shown to partners use localized business-friendly labels derived from stable failure codes. Raw Telegram payloads and exception text remain masked diagnostics only.
- Confirmed appointment overlap protection is scoped to `BookingResource`, not directly to whole partner. Solo-master MVP has one default booking resource per partner.
- `docs/spec/SCHEMA_DRAFT.md` is approved as the implementation-planning data map. Exact Django model/field names and migration details can still be adjusted during implementation, but entity boundaries and approved invariants should not drift without an explicit decision.
- Booking-resource advisory-lock helper is approved: immutable unique `BookingResource.advisory_lock_key`, fixed PostgreSQL advisory-lock namespace, and `pg_advisory_xact_lock(namespace, advisory_lock_key)` inside the same transaction as the fresh overlap check and appointment write.
- Sensitive-data helper strategy is approved: encrypted bot tokens through a `cryptography`/Fernet-style key-ring wrapper, non-reversible HMAC token fingerprints for duplicate-token checks, normalized unencrypted phone/message/private-note business storage in MVP, and shared masking/redaction helpers for logs, diagnostics, audit payloads, exceptions, and raw external payloads.
- `docs/spec/RUNBOOK_DRAFT.md` is approved as the MVP operational runbook direction. Exact Docker Compose service names, Django management commands, URLs, and host log commands are finalized during implementation when those artifacts exist.
- `docs/spec/DIAGNOSTICS_TAXONOMY.md` is approved as the MVP platform-diagnostic taxonomy. Exact enum/class names, metadata schemas, aggregation/deduplication behavior, and superuser dashboard filters are finalized during implementation.
- Operational retention direction is approved: noisy delivery/broadcast/system diagnostic records default to 3 months, partner-visible audit to 6 months, and core business history is retained while partner data is retained.

## Product Validation And Risk

These should be tracked during MVP/pilot, but they do not block writing a coherent technical plan.

- How will the product problem be validated before or during MVP, given that the product origin is TableOS analogy and Botera ecosystem strategy rather than a concrete observed "before BoteraMaster" scenario from a master?
- What business metrics define success for the first release? Examples may include active partners, bookings created, repeat usage, reminder reliability, broadcast usage, and partner retention.
- What makes a solo master choose BoteraMaster instead of Google Calendar, Telegram notes, existing booking systems, or manual messages?

## Future And Post-MVP Design

These are explicitly not required to start the MVP implementation.

- How should future discounts, promo codes, bonuses, loyalty balances, and manual price adjustments extend appointment totals after MVP?
- For post-MVP manual prepayment by requisites, how should it be marked: requested, sent, confirmed, rejected, expired?
- For post-MVP manual prepayment, should the client send proof through the bot and the master approve it with inline buttons?
- How should the future Botera ecosystem layer, including cross-product promotions, bonuses, and partner programs, stay separate from BoteraMaster MVP?
- How should future studio/multi-master support extend the current partner model without breaking MVP assumptions?
- Which future external integrations, if any, should be prioritized after MVP: Google Calendar, online payments, Instagram, WhatsApp, SMS, email, or something else?
- What moderation/abuse/reporting controls should be added later if bot-mediated client-master messages create support or misuse cases?
