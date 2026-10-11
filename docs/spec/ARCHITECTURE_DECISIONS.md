# BoteraMaster Architecture Decisions

Status: finalized architecture-decision backlog, last updated on 2026-09-09; not final implementation architecture.

This document records approved architecture-preparation decisions and deferred technical decisions for BoteraMaster MVP. It does not approve final code structure, framework boundaries, database schema, migrations, dependencies, AGENTS.md, project skills, or application code.

## Approved Direction

### AD-001 Tenant Boundary

- `partner` is the main tenant boundary.
- Client-facing language can say "master"; internal/platform language uses `partner`.
- MVP partner has one owner/master role with full access inside their partner scope.
- Partner-owned records must be explicitly partner-scoped.
- Non-superuser admin access must query and mutate data only through the current partner scope.
- Superuser can access all partner scopes.
- Future studios and employee roles must remain possible, but are outside MVP.

### AD-002 Partner Onboarding

- In MVP, partners are created by superuser manually.
- Superuser creates the partner admin account and gives the partner login/password.
- Public self-service partner registration is outside MVP.
- Partner must complete minimum setup before the bot can run for clients: bot token, language, timezone, at least one service category/service, at least one availability window, and booking confirmation mode.
- Other settings can use defaults on first launch.
- Partner/setup state should support a draft/setup-required state so an incomplete bot is not exposed to clients.
- Partner admin should expose an onboarding checklist/setup guide for the minimum launch steps.
- While setup is incomplete, client booking/profile flows must be unavailable; the bot may show a short system-only localized "being configured" message.
- After minimum setup is complete, the partner can launch the bot without final superuser approval.
- Superuser should have a dashboard/view/filter for setup-required partners and missing setup steps.
- If an active partner later has no services or availability windows, the bot stays active and shows proper empty states rather than being automatically deactivated.
- If partner is suspended, bot/client actions stop, data is preserved, and partner admin access is limited to viewing/recovery/payment/problem-fix flows.

### AD-002A Partner Admin UX

- MVP partner admin main navigation is Dashboard, Calendar, Appointments, Clients, Catalog, Broadcasts, Finance, Bot, and Activity Log.
- Dashboard is the first screen and should surface today's/tomorrow's appointments, pending requests, failed notifications, active conflicts/problems, and compact stats/revenue.
- Calendar is the primary day-to-day working screen for the master.
- Appointments list is a secondary searchable/filterable operational view.
- Partner Activity Log is a standalone screen, while appointment and client cards may show short related local history.
- Superuser admin can remain Django-admin-like/pragmatic in MVP, with dedicated operational views/filters for health, setup-required partners, and problems/incidents.

### AD-003 Bot Instance And Bot Settings

- Each partner has their own Telegram bot.
- Partner provides the bot token.
- MVP uses polling.
- TableOS is a reference for bot instance/token handling, partner runtime context, middleware ideas, and partner-scoped admin patterns.
- Separate Bot Instance from Bot Settings conceptually.
- Bot Instance covers technical connection/runtime state: encrypted token, status, runtime eligibility, last error, health/activity.
- Bot Settings covers partner-controlled behavior/content: languages, texts, buttons, reminders, quiet hours, booking rules, required fields, cancellation rules, and penalties.
- Bot settings are managed through admin only in MVP, not through Telegram bot commands.
- Admin should expose one `Bot` section with tabs: Instance, Settings, Content, Notifications, Booking, and Penalties.
- Bot statuses are `active`, `paused`, `suspended`, and `error`.
- Partner can switch own bot between `active` and `paused`.
- `suspended` is superuser-only.
- Partner can replace their own bot token.
- Full token is not shown after save; UI shows masked/status representation.
- Invalid token at save time should block saving.
- Token/configuration that later fails at runtime should move bot to `error`, show the problem to partner, and make it visible to superuser.
- Partner can trigger a check/revalidate action from admin after fixing token/configuration.
- Critical settings require a normal confirmation modal before save; typed confirmation is not required in MVP.

### AD-004 Bot Runtime

- MVP starts with a simple TableOS-like bot runner.
- One runner process/command loads eligible active bots at startup and starts polling for them.
- Bot token/status changes may require runner restart/redeploy to affect live polling in MVP.
- Hot reload and periodic runtime config reload are future improvements, not MVP requirements.
- Bot runner and handlers must preserve partner context for every partner-scoped operation.

### AD-005 Telegram Identity And Client Profile

- Telegram identity is global.
- Client profile is partner-scoped.
- One Telegram user can be a client of multiple partners without mixing partner-specific data.
- Partner-specific name, phone, language, notes, history, penalties, marketing opt-out, and bot reachability/block state stay separate per partner.
- MVP stores partner-bot communication flags directly on partner-scoped `ClientProfile`, not in a separate `ClientCommunicationState` model.
- `ClientProfile` communication flags include `marketing_opt_out`, `bot_unreachable`, `last_delivery_failure_code`, and `last_delivery_failure_at`.
- Marketing opt-out affects broadcasts only; service notifications and manual master messages are separate.
- Raw Telegram failure payloads are not stored on `ClientProfile`; detailed delivery attempts/diagnostics are stored separately with masking and retention.
- New incoming activity from the client in the partner bot should clear `bot_unreachable`.
- Manual clients without Telegram identity are shown as having no Telegram connection, not as blocked/unreachable.
- Client profiles may exist without Telegram identity for manual clients.
- Manual clients can later be linked/merged with Telegram identity after master confirmation.
- Phone match can suggest a link/merge, but the system must not silently merge clients.

### AD-006 Services, Appointment Items, And Money

- BoteraMaster does not provide a global service catalog.
- Each partner creates their own service categories and services.
- Services can have category, title, optional description, price, duration, active/inactive status, and display order.
- Multi-service appointments are part of MVP.
- Multi-service appointment snapshots should be modeled conceptually as appointment service item/line records under the appointment.
- Appointment service item/line records should be used even when an appointment has only one service.
- Appointment service item lines store per-line snapshots and may reference a partner-defined service or represent a one-off custom service.
- Manual appointments may use one-off services and may override price/duration for a specific appointment.
- One-off custom/manual services do not automatically enter the partner service catalog; they remain appointment item lines unless the partner separately creates a catalog service.
- Manual overrides shown to the client should have a "changed by master" marker.
- Manual appointment creation from Calendar starts with selected time/range, then client and service details.
- Partner can create a manual client inline when no matching client exists.
- Manual appointments are `confirmed` by default.
- Manual appointments for Telegram-linked clients should send a Telegram notification.
- Manual appointments may be created outside availability windows with a warning, but still cannot overlap another `confirmed` appointment.
- Future confirmed appointment changes visible to the client require client confirmation.
- Client-visible future appointment changes include time/date, service set, price, duration, and client-visible comment/description.
- Internal-only changes do not require client confirmation.
- The current appointment remains unchanged while a client-visible change proposal is pending.
- Client-visible change proposals use accept/decline actions and expire after 48 hours by default.
- Appointment proposal storage starts with one unified `AppointmentProposal` concept, covering master-initiated reschedule proposals, requested-appointment alternative slot proposals, and client-visible appointment change proposals.
- The unified proposal model must remain typed/structured enough to avoid becoming an unstructured payload bucket.
- MVP proposal types are `reschedule_confirmed`, `requested_alternatives`, and `appointment_change`.
- Changes to services, price, duration, or client-visible appointment text without a time change are covered by `appointment_change`.
- Proposal statuses are `pending`, `accepted`, `declined`, `expired`, `cancelled`, and `superseded`.
- Creating a new proposal for an appointment should supersede any older pending proposal for that appointment.
- Proposal options/slots should be separate structured records such as `AppointmentProposalOption`, not only JSON embedded inside the proposal.
- Proposal metadata belongs on `AppointmentProposal`; proposed variants belong on `AppointmentProposalOption`; proposed service/price/duration changes belong on typed option item rows such as `AppointmentProposalOptionItem`.
- Business-critical proposed values should be stored in typed fields/rows, with small structured metadata allowed only for non-critical display/runtime convenience.
- If client confirmation is impossible, such as for a manual client without Telegram identity, the master can save directly with audit logging and a clear indication that the client was not notified/confirmed.
- Money is stored as integer minor units with explicit currency.
- MVP currency is UAH.
- Example: `500.00 UAH` is stored as `50000`.
- Discounts, bonuses, promotions, and loyalty mechanics are expected later but are outside the current MVP formula.

### AD-007 Availability And Booking

- Availability windows are the main source of bookable time.
- Partner can draw/open available windows in a calendar and also use quick manual input.
- Week view is the default calendar view, with day/week/month switching.
- Calendar range selection supports creating availability windows, blocked time, and manual appointments.
- Calendar event/layer types such as availability windows, blocked time, appointments, conflicts, and near-fit/request markers should be visually distinguishable.
- Clicking an appointment opens a detail panel/modal with actions to edit, propose reschedule, cancel, mark completed, mark no-show, and message client.
- Calendar supports quick copy day/week actions in addition to saved availability templates.
- Calendar admin should be usable on mobile with adapted interactions, while dense editing can remain more comfortable on desktop/tablet in MVP.
- Partner can create multiple reusable availability templates and apply them to future weeks/date ranges.
- Weekly schedules are treated as reusable templates, not the only scheduling model.
- Partner can block unavailable intervals inside availability windows; reason text is optional.
- Availability windows are concrete UTC `start_at` / `end_at` intervals; templates do not create client-visible slots until applied into concrete windows.
- Overlapping or adjacent availability windows are allowed, with logical merging during slot generation/calendar display.
- Blocked time is a concrete UTC interval with optional reason and may exist inside or outside availability windows.
- Availability templates store reusable weekday/start/end patterns for MVP; date-specific template windows are not required.
- Availability windows and blocked time can be physically deleted in MVP, while confirmed appointment conflicts are handled through conflict lists rather than moving appointments silently.
- Time-range records with intervals require `start_at < end_at`; template window rules require `start_time < end_time`.
- Partner-created past availability/blocked records are blocked in normal admin/service flows, while historical records remain stored.
- Maximum single availability window length is 24 hours.
- Maximum single blocked-time length is 31 days.
- Overnight template window rules are not supported in MVP; split them into two day-local rules if needed.
- Booking horizon is configurable per partner.
- Changing availability does not silently move confirmed appointments; conflicts are shown for partner resolution.
- Confirmed appointment overlap prevention is a critical invariant and must be enforced transactionally/concurrency-safely, not only through UI filtering.
- Confirmed appointment overlap protection is scoped to a booking resource, not directly to the whole partner.
- Solo-master MVP has one default booking resource per partner; future studios can add multiple booking resources under one partner.
- MVP confirmed appointment overlap protection starts with a transactional service-layer check plus a PostgreSQL transaction-level advisory lock keyed by booking resource during create/confirm operations.
- Advisory lock is preferred over locking the partner row because the concurrency rule is explicit, resource-scoped, and does not couple appointment booking to unrelated partner-row updates.
- Recommended advisory-lock implementation uses `pg_advisory_xact_lock(namespace, advisory_lock_key)` with a fixed booking-resource namespace and an immutable unique integer `BookingResource.advisory_lock_key`.
- The helper must be called inside the same transaction as the fresh overlap check and appointment write.
- Booking-resource advisory lock is required for operations that can create or change a `confirmed` interval: auto-confirm booking, manual appointment creation, confirming `requested`, accepting proposals into `confirmed`, restoring/reopening to `confirmed`, and admin interval edits for confirmed appointments.
- Booking-resource advisory lock is not required for creating `requested`, cancellation, marking completed/no-show, private note edits, client-visible text-only edits, or creating availability/blocked time.
- Lock-protected operations run as: transaction begins, lock is acquired, fresh overlap query runs, save or conflict response happens, transaction commits, and notifications/reminders are enqueued after commit.
- MVP uses blocking transaction-level advisory lock behavior rather than try-lock behavior.
- Telegram/network I/O, sleeps, and retry loops must not happen inside lock-protected transactions.
- PostgreSQL range/exclusion constraints are deferred as a later hardening option unless they stay simple during final schema design.
- `requested` appointments may overlap as requests, but accepting/confirming a request must re-check availability and prevent confirmed overlap.
- Near-fit tolerance is in MVP.
- Default near-fit tolerance is 10 minutes.
- Near-fit tolerance applies only to exceeding the end of an availability window.
- Near-fit does not allow overlap with blocked time or another confirmed appointment in MVP.
- Near-fit booking always starts as `requested`, even if auto-confirm is enabled.

### AD-008 Background Jobs And Delivery Guarantees

- Background jobs are foundational for reminders, morning digest, stale requested reminders, broadcasts, failed-send retries, cleanup, and operational checks.
- Confirmed MVP stack is Celery + Redis broker + a scheduler/beat process.
- Durable business delivery outcomes live in PostgreSQL records, not in Celery result backend.
- Confirmed queue names are `default`, `notifications`, `broadcasts`, and `maintenance`.
- Required production MVP process roles are web/admin, bot polling runner, scheduler/beat, notifications worker, broadcast worker, PostgreSQL, and Redis.
- Broadcast work must be isolated from urgent reminders in production MVP, preferably by a separate broadcast worker.
- Broadcast lag can be acceptable when progress is visible; notifications lag is a health problem.
- Superuser/admin operations should include a basic health dashboard for queue/worker/beat status.
- Health status should use simple `OK`, `Warning`, and `Critical` states.
- Partner-facing admin should show only partner-relevant bot health/status, not internal queue/worker/beat infrastructure.
- External Telegram/email alerts are nice-to-have for MVP, not required for first implementation.
- Platform health should be stored as durable current-state records, with noisy heartbeat/diagnostic history cleanup-eligible through `retain_until`.
- MVP health thresholds are 3/5 minutes for `beat` and `notifications` pings, 2/5 minutes for due reminder lag, 10/30 minutes for `broadcasts` ping, and 15/60 minutes for active broadcast no-progress warnings.
- Docker is the confirmed deployment direction for MVP.
- Production MVP requires process supervision through Docker Compose restart policies / healthchecks unless the deployment target later demands a heavier orchestrator.
- Kubernetes or other heavy orchestration is not required for MVP.
- MVP may run on a single server/VPS with Docker Compose, including PostgreSQL and Redis in Compose.
- Initial Docker Compose service direction is `web`, `bot-runner`, `celery-beat`, `notifications-worker`, `broadcast-worker`, `default-worker`, `postgres`, and `redis`.
- Initial Celery concurrency direction is `notifications-worker` around 4, `broadcast-worker` 1, and `default-worker` 1.
- `maintenance` jobs should be handled by the small `default-worker`; a dedicated `maintenance-worker` is not required in MVP.
- Bot runner hot reload is not part of MVP; bot token/status changes take effect through runner restart/redeploy.
- MVP logging uses Docker stdout/stderr plus application-level sensitive-data masking and durable audit/domain records; ELK/Loki/Sentry are not required for first implementation.
- Sentry or an equivalent error-monitoring service is nice-to-have/post-MVP.
- A short operational runbook should exist before implementation starts.
- Durable schedule/delivery state should live in the database; scheduler tasks discover due work.
- Business services enqueue jobs only after commit.
- Telegram/network I/O must not run inside the source business transaction.
- These jobs must not live only inside bot handler flow.
- Partner jobs operate within partner scope; system jobs may be global but must preserve tenant boundaries.
- Reminder jobs must be idempotent.
- Retrying the same scheduled reminder must not send duplicate reminders.
- Resending is allowed only after failed-send retry logic or explicit master action.
- Client business event notifications are required for appointment created/confirmed, appointment change proposed, change accepted/declined/expired, appointment cancelled, appointment completed, no-show marked, and penalty created/paid/waived.
- Manual clients without Telegram identity do not get Telegram notifications; partner admin should show that no Telegram connection exists.
- Failed client business event notifications are logged and shown to the master where action is useful.
- Failed appointment change proposal delivery supports explicit master notify/resend action.
- Client reminders are created only when an appointment becomes `confirmed`.
- Appointment reschedule/cancel/complete/no-show should make old pending reminders stale/cancelled.
- If a reminder delayed by quiet hours would be sent after the appointment has already passed, it is skipped.
- Failed reminder handling uses automatic retries before notifying the master when feasible.
- Default failed reminder retry count is 3.
- Retry backoff direction is `1 min -> 5 min -> 15 min`.
- If Telegram says the user blocked the bot, do not retry; mark that client unreachable for that partner bot.

### AD-009 Broadcasts

- Broadcasts are included in MVP.
- Partner can broadcast only to own clients who have not opted out of marketing and are reachable for that partner bot.
- MVP broadcast filters include all clients, long-time-no-visit, visited specific service, has future appointment, and no future appointment.
- Partner sets the period for "long-time-no-visit".
- Broadcasts can send now or be scheduled.
- MVP content supports text, photo, and buttons.
- MVP buttons are `Book`, `My appointments`, and external link.
- Templates may be saved for reuse, but partner writes broadcasts manually.
- Campaign state is separate from per-recipient delivery records.
- Per-recipient statuses are `queued`, `sent`, `failed`, and `skipped`; Telegram `delivered` is not promised.
- If a client unsubscribes or blocks the bot before their turn, delivery is `skipped`.
- Telegram `429` and temporary errors should back off and continue later instead of failing the whole campaign.
- Broadcast pacing is per partner bot and must not send faster than one message every 0.5 seconds per bot.
- A simple configurable global broadcast safety limit / worker concurrency cap should exist so broadcasts cannot overload BoteraMaster itself as active bots grow.
- Long-running broadcast progress should remain visible to the partner through counters/statuses and progress indication.
- Broadcast worker crashes must be recoverable from durable recipient records without duplicate sends for recipients already recorded as completed.
- Manual retry of failed broadcast recipients is post-MVP.

### AD-010 Audit, Retention, And Cleanup

- Partner-visible business audit is an MVP requirement.
- Partner admin should have a separate activity/journal screen; cards may also show local related history.
- Partner-visible audit includes appointments, penalties, expenses, broadcasts, reminders, client-master messages, and bot/settings changes.
- Partner-visible business audit logs are retained for 6 months by default.
- Platform/system diagnostics are superuser-only.
- System logs include bot runner/background job errors with sensitive data masked.
- Sensitive/destructive partner actions are audited, including appointment cancellation, no-show marking, penalty changes/payment/waiver, expense changes/deletion, bot token/settings changes, and corrections after final status.
- Partner-visible audit entries are business-friendly and do not expose raw technical payloads.
- Audit reason is required for correction-flow actions after a final appointment status.
- Reason is optional in MVP for master cancellation, penalty waiver, and expense deletion.
- Business records are not cleanup-eligible by default.
- Noisy technical records should use `retain_until`.
- Candidate cleanup-eligible records include detailed broadcast delivery records, delivery attempts, partner-visible technical logs, and platform/system logs.
- Default cleanup retention is 90 days: `retain_until = created_at + 90 days`.

### AD-011 Sensitive Data

- Bot tokens, client phone numbers, client-master messages, and master private notes are sensitive.
- Bot tokens are stored only encrypted at rest. The raw token is available only during input/replacement and runtime decrypt.
- Bot-token encryption should use a small `core.security.crypto` wrapper over authenticated symmetric encryption from `cryptography`, preferably `Fernet`/`MultiFernet` unless implementation finds a concrete blocker, with keys loaded from environment/secret storage rather than the database or repository.
- Encryption should use a key-ring approach: first configured key encrypts new values, all configured keys can decrypt old values, and encrypted rows store `token_encryption_key_id` for future rotation clarity.
- Duplicate active bot token detection should use a keyed non-reversible token fingerprint/HMAC, not raw token comparison or deterministic ciphertext. The HMAC key should come from environment/secret storage as a separate setting such as `TOKEN_FINGERPRINT_KEY`.
- Full bot token is not shown in UI after save; UI shows only status/masked representation such as a safe prefix/suffix.
- Client phone is stored in a normalized usable form because the owning master needs it for real work.
- Client phone numbers are not app-field-encrypted in MVP; masking/access control protect broad UI, logs, diagnostics, and audit payloads.
- Client phone can be shown fully to the owning master in client cards, appointment details, and communication contexts, but should be masked/shortened in broad lists where full display is not needed.
- Client phone is always masked/redacted in technical logs and diagnostics.
- Client-master message text is stored as partner-scoped business data for conversation history/support.
- Raw client-master message text must not be written into technical logs.
- Partner-visible audit may record a business event for a message without storing the full raw message text in the audit entry; a short masked/preview value is acceptable when useful.
- Master private notes are partner-scoped sensitive business data visible to the partner/master.
- Superuser should not have a casual/default UI for reading master private notes, but technical/support access may exist for incident handling.
- Raw private note text must not be written into technical logs or audit payloads.
- Client-master messages and master private notes are not app-field-encrypted in MVP; future stricter privacy/support requirements may revisit field-level encryption.
- Sensitive values may appear in logs/errors only in masked/redacted form.
- A shared masking/redaction helper should cover bot tokens, phone numbers, message text, private notes, raw external payloads, exception rendering, and recursive diagnostic metadata before they reach platform diagnostics/errors.
- Raw Telegram payloads should not be stored without filtering/masking.
- Superuser can technically access sensitive partner data for support/incident handling, but it is not a casual/default working screen.
- MVP does not require audit events for every superuser conversation view.
- Future sensitive-data export requires a separate approved decision.

### AD-012 Settings Validation And Recovery

- Prefer save-time validation for partner-editable settings.
- Invalid bot token and invalid template placeholders block saving.
- Required booking fields stop booking until the client fills the required data.
- Slot step allowed values are 5, 10, 15, 20, 30, and 60 minutes; default is 15 minutes.
- Booking horizon minimum is 1 day, maximum is 365 days, and default is 30 days.
- Client reminder offset minimum is 15 minutes, maximum is 30 days, default offsets are 24 hours and 2 hours, and a partner can have up to 5 client reminder rules.
- Master reminder offset minimum is 5 minutes, maximum is 7 days, default is 45 minutes, and the setting can be disabled.
- Near-fit tolerance minimum is 0 minutes, maximum is 30 minutes, and default is 10 minutes.
- Cancellation deadline minimum is 0 hours, maximum is 168 hours / 7 days, and default is 6 hours.
- Quiet hours are stored as local start/end times, default to 22:00-08:00, can be disabled, and may be overnight.
- Morning digest time is stored as local time, can be any time from 00:00 to 23:59, defaults to 08:00, and can be disabled.
- Morning digest is still sent at its configured time if that time falls inside quiet hours because it is a partner-controlled master-facing notification.
- Stale requested reminder base interval minimum is 10 minutes, maximum is 72 hours, and default is 3 hours; repeated reminders halve the interval down to a 5-minute floor.
- If an older saved setting becomes invalid after a system change and a safe default exists, runtime should use the safe default and show warnings instead of crashing.
- Runtime should block only when safe operation is impossible, such as invalid/missing bot token, missing timezone, missing language, corrupted encrypted token, missing system content default for fallback, or severely broken critical bot settings.
- Partner-visible warnings appear in Bot/settings/admin; superuser sees them in problem/health views.
- Invalid legacy settings must not crash bot runtime or background jobs.

### AD-013 Telegram Pagination

- Telegram-facing long lists must support pagination in MVP.
- Pagination applies to client history, appointment lists, services, slot choices, conflict lists, broadcast previews, and entity-bound inline keyboards.
- Pagination must respect Telegram message length and inline keyboard limits.
- Client history is newest-first, shown as short cards, with details opened through a button/action.
- Default client history page size is 5.
- Default navigation uses previous/next controls and a page indicator such as `1/4`, localized through content.

### AD-014 TableOS Reuse Boundary

- TableOS is a conceptual reference, not a source to copy blindly.
- TableOS Django + aiogram 3.x bot runtime is the preferred reference stack for BoteraMaster MVP unless implementation reveals a concrete blocker.
- Useful TableOS areas: partner setup, partner-bound model patterns, bot instance/token handling, multi-bot polling organization, middleware/runtime partner context, per-update partner/bot state checks, admin filtering, default texts/buttons, content rendering, button-label filters, safe template fallback, and bot token validation.
- TableOS runtime config validation and duplicate-token safety tests are useful examples for BoteraMaster's focused tests.
- Do not import hospitality-specific business logic.
- Do not inherit TableOS employee/staff complexity for MVP.
- Do not reuse TableOS content model unchanged; BoteraMaster needs multilingual defaults and partner overrides by template key/language.
- Do not copy TableOS `AdminAccessProfile` / section-permission complexity for MVP because partner-side access is owner/master-only.
- Do not copy TableOS per-partner file logging as the MVP default; BoteraMaster uses Docker stdout/stderr, masked diagnostics, and durable audit/domain records.

### AD-015 Multilingual Content Templates

- MVP content uses conceptual `SystemTemplate(key, language)` records plus `PartnerTemplateOverride(partner, key, language)` records.
- System templates store default text/label, content type, allowed placeholders, and whether the template is required.
- Partner overrides are optional and language-specific.
- Empty or missing partner overrides fall back to the system default for the user's language.
- If a partner customizes one language only, other languages continue to use their own system defaults until explicitly overridden.
- If the requested system default language is unexpectedly missing, Ukrainian is the fallback language.
- Partner-authored free text such as service names, broadcast text, manual messages, and comments is not automatically translated.
- Unknown placeholders or render errors block saving partner overrides.
- Save-time validation should render the template against a sample context for the selected key/language.
- Runtime rendering remains defensive and should fall back safely if invalid legacy content somehow exists.
- Admin content UI should show language tabs, system default text/label, allowed placeholders, preview/render test, and reset-to-default action per key/language.
- MVP editable content key groups start with start/welcome, booking, reschedule/cancellation, reminders, client profile, penalties/no-show/late-arrival, client-master messaging, and broadcast buttons.
- Common navigation buttons and system error/service texts remain system-only localized in MVP.
- Initial system-only common/navigation keys are `common.back`, `common.next`, `common.cancel`, `common.confirm`, `common.done`, `common.clear`, `common.yes`, `common.no`, `common.open_profile`, `common.book`, `common.my_appointments`, and `common.contact_master`.
- Initial system-only error/service keys are `error.generic`, `error.invalid_action`, `error.expired_action`, `error.slot_taken`, `error.no_slots`, `error.required_field_missing`, `error.bot_temporarily_unavailable`, `error.too_many_items_paginated`, and `error.delivery_failed`.
- Broadcast marketing CTA labels are handled separately through editable broadcast button keys, even though common navigation remains system-only.
- Partner-editable start/welcome message is included and can be overridden per language.
- Initial editable `start/welcome` keys are `start.welcome`, `start.choose_language`, and `start.profile_incomplete`.
- Initial editable `booking` keys include category/service selection, booking summary, date/time selection, near-fit notice, final confirmation, requested/confirmed creation, client request cancellation, master confirmation, and master decline.
- `booking.slot_taken` and "no available slots" remain system-only localized service texts in MVP.
- Ordinary client booking summary/final confirmation does not show penalties unless penalties have already been explicitly applied to that appointment.
- Initial editable cancellation/reschedule keys include cancellation confirmation/success, master cancellation, late-cancel penalty warning/success, client reschedule start/request/auto-confirmation, master reschedule proposal, proposal accepted/declined/expired, and master notifications for client cancellation/reschedule events.
- `appointment.late_cancel_blocked` remains system-only localized in MVP.
- Cancellation/reschedule `{reason}` is optional and templates must render cleanly when it is empty.
- Initial editable reminders keys include client before-appointment reminder, master before-appointment reminder, empty/non-empty morning digest, stale requested reminder, and failed-delivery-to-master notice.
- Repeated stale requested reminders reuse `reminder.stale_requested`; no separate repeated template is needed in MVP.
- Manual retry sent/failed texts remain system-only localized in MVP.
- Reminder `{failure_reason}` shown to partners must be business-friendly and must not expose raw technical payloads.
- Initial editable client profile keys include profile main, missing fields prompt, future appointments list/empty, history list/empty, appointment details, penalties list/empty, and contact-master prompt.
- Profile updated success, marketing subscribe/unsubscribe confirmations, and contact-master sent confirmation remain system-only localized in MVP.
- `profile.appointment_details` must be able to show penalties and total when penalties are applied to that appointment.
- Profile detail `{comment}` and `{reason}` are optional and templates must render cleanly when either is empty.
- Initial editable penalty/no-show/late-arrival keys include late-cancel penalty created, no-show penalty created, late-arrival penalty created, penalty applied to appointment, penalty paid, and penalty waived.
- Initial editable master-facing penalty notification keys are `master.penalty_created`, `master.penalty_paid`, and `master.penalty_waived`.
- Internal penalty status labels such as `unpaid`, `applied_to_appointment`, `paid`, and `waived` remain system-only localized.
- Technical penalty validation errors, "penalties disabled" explanations, and admin-only correction confirmations remain system-only localized in MVP.
- Penalty template placeholders should expose `{penalty_reason}` as a localized business label, not as a raw enum.
- Initial editable client-master messaging keys are `messaging.contact_master_prompt`, `messaging.client_message_received_for_master`, and `messaging.master_reply_received_for_client`.
- Client confirmation after sending a message and messaging delivery failure texts remain system-only localized in MVP.
- Messaging delivery failure reasons shown to partner/client must be business-friendly and must not expose raw Telegram payloads.
- Master-facing new-message notifications may include client phone when available.
- Initial editable broadcast button keys are `broadcast.default_cta_book`, `broadcast.default_cta_my_appointments`, and `broadcast.default_cta_external_link`.
- Broadcast message bodies are partner-authored manually or via partner-saved templates; the system does not provide generic marketing body defaults in MVP.
- Broadcast button labels are language-specific partner overrides with normal system-default fallback behavior.
- `broadcast.unsubscribe_confirmed`, delivery/statistics status labels, and broadcast service errors remain system-only localized in MVP.
- The initial MVP content-key registry is approved at the key/placeholder/editable-vs-system-only level.
- Every runtime content key must have system defaults for `uk`, `ru`, and `en`; Ukrainian is the emergency fallback language.
- Every editable key must define an allowed placeholder list and sample validation context.
- Adding new keys later is allowed when the key has system defaults, placeholder allowlist, sample validation context, and editable/system-only classification.
- Exact default localized copy wording is a seed/copywriting task and does not reopen the registry decision.

## Deferred Technical Decisions Before Implementation

- Exact Django field names and migration constraints from the approved `docs/spec/SCHEMA_DRAFT.md` data map.
- Exact default localized wording for the approved multilingual content registry.
- Exact broadcast backoff intervals, global concurrency thresholds, and alert thresholds.
- Exact command names/URLs for the approved `docs/spec/RUNBOOK_DRAFT.md` once Docker Compose and Django commands exist.
- Future hot reload path for bot runner.
- Exact enum/class names, metadata schemas, aggregation/deduplication behavior, and dashboard filters for the approved `docs/spec/DIAGNOSTICS_TAXONOMY.md`.
- Exact localized copy and formatting for paginated lists, appointment detail cards, completed/no-show/penalty events, and failed notifications.
- Exact calendar visual treatment, component behavior, and polished mobile interactions.

## Explicitly Deferred Product Areas

- Studios and multiple staff roles.
- Online payments.
- Google Calendar integration.
- Loyalty system.
- Reviews.
- Self-service client deletion.
- Repeat previous service.
- Export.
- Partner ecosystem cross-promotions and Botera-level partner program.
- Prepayment requirement after no-show.
- Manual retry of failed broadcast recipients.
- Runtime hot reload for bot configuration.
