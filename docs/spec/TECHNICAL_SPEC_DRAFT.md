# BoteraMaster Technical Spec Draft

Status: draft technical specification on 2026-09-09, requires explicit approval before implementation.

Last updated: 2026-09-09.

This document turns the approved product requirements, domain model, and architecture decisions into a technical blueprint. It intentionally does not create application code, Django apps, models, migrations, dependencies, AGENTS.md, project skills, or final implementation architecture.

Primary sources:

- `docs/spec/PRODUCT_REQUIREMENTS.md`
- `docs/spec/DOMAIN_MODEL.md`
- `docs/spec/ARCHITECTURE_DECISIONS.md`
- `docs/discovery/OPEN_QUESTIONS.md`

## Technical Posture

Recommended direction:

- Build BoteraMaster as a modular monolith first.
- Keep tenant isolation explicit through `partner`.
- Keep bot runtime, background jobs, admin, and domain services separated by responsibility.
- Prefer boring, inspectable business services over hidden side effects.
- Reuse TableOS ideas where they fit, but do not copy hospitality business logic or staff-complexity into MVP.
- Treat this MVP as solo-master first, future-studio aware.

Not approved by this draft:

- Final framework/package list.
- Final Django app names.
- Final database schema.
- Final async/job stack.
- Final deployment topology.
- Final AGENTS.md or project skill.

## TableOS Reference Notes

Useful TableOS patterns observed:

- TableOS uses Django + aiogram 3.x for the Telegram runtime; this is the preferred reference stack for BoteraMaster's MVP bot runtime unless implementation reveals a concrete blocker.
- `core.database.models.PartnerBoundModel` gives a simple abstract pattern for records owned by one partner.
- `bot.services.runtime.load_active_bot_configs()` validates runtime eligibility before polling: partner active, polling mode, decryptable token, valid token shape, and duplicate-token safety.
- `bot.management.commands.runbot` and `bot.main.run_polling()` provide a simple multi-bot polling runner pattern: load active configs at startup, register runtime configs by token, start aiogram polling for all active bots, and close bot sessions on shutdown.
- `bot.middlewares.partner_runtime.PartnerRuntimeMiddleware` gates handler execution by current bot/partner runtime state.
- `bot.services.context.resolve_partner_for_bot_token()` re-checks bot instance and partner state per update; this is useful for live paused/suspended protection even when runner config only reloads on restart.
- `bot.services.content.BotContent` centralizes default bot labels/messages, partner overrides, safe template rendering, and graceful runtime fallback.
- `bot.filters.content.PartnerButtonFilter` shows a useful pattern for partner-customized reply button labels.
- TableOS keyboard builders are useful as a style reference, but BoteraMaster needs appointment-specific inline pagination and booking flows.
- `apps.partners.forms.BotInstanceAdminForm` validates bot username/token before saving and stores token encrypted.
- `core.security.crypto` isolates encryption/decryption for bot tokens.
- `core.admin_mixins.ScopedAdminMixin` is a useful reference for partner-scoped admin querysets, partner FK filtering, partner assignment on save, and inline safety checks.
- `apps.users.TelegramAccount` plus partner-specific profile shows the correct identity split direction, but TableOS global `is_blocked` must not be copied as-is because BoteraMaster block/reachability is partner-bot scoped.
- TableOS tests around runtime config eligibility and duplicate active tokens are useful examples of focused tests worth carrying forward conceptually.

BoteraMaster adaptations:

- Bot content must become multilingual: system defaults in `uk`, `ru`, `en`, partner overrides by key and language.
- Do not copy TableOS' single large `PartnerBotSettings` content structure unchanged; BoteraMaster should use the previously approved template-by-key/language model.
- Appointment scheduling requires stronger time-range invariants than TableOS.
- Background jobs must have idempotency and delivery records; handler-only flow is not enough.
- Partner admin isolation should reuse the partner-bound idea, but not TableOS staff permissions for MVP.
- Do not copy hospitality-specific handlers for menu/cart/orders/tables/billing; BoteraMaster needs service catalog, schedule, appointments, reminders, penalties, client cabinet, messaging, and broadcasts.
- Do not copy TableOS `AdminAccessProfile` / section-permission complexity for MVP because BoteraMaster partner-side access is owner/master-only for now.
- Do not copy TableOS logging-to-per-partner-files as the MVP default; BoteraMaster already chose Docker stdout/stderr plus durable audit/domain records and masked diagnostics.

## Proposed Module Boundaries

These are logical boundaries, not final Django app names.

## Recommended App / Package Names

Status: confirmed on 2026-09-08.

Confirmed top-level application boundaries:

- `core` - shared primitives: UUID/timestamps, partner-bound base, money helpers, timezone helpers, masking, retention helpers, common validation, platform diagnostics foundations.
- `partners` - partner tenant, partner lifecycle, setup-required state, onboarding, partner-level defaults, suspension.
- `accounts` - back-office user model/access, superuser vs partner owner access, admin scoping glue.
- `bot` - Telegram runtime, polling runner, middleware, handlers, keyboards, content rendering, bot pagination, bot health checks.
- `clients` - Telegram identities, partner-scoped client profiles, manual clients, client linking/merge suggestions, partner-bot communication flags.
- `catalog` - partner-defined service categories and services.
- `schedule` - availability windows, blocked time, availability templates, booking horizon, slot generation, schedule conflicts.
- `appointments` - appointment records, appointment service item snapshots, booking/reschedule/cancel/status flows, confirmation concurrency rules.
- `notifications` - scheduled reminders, delivery attempts, failed delivery markers, morning digest, stale requested reminders.
- `broadcasts` - broadcast campaigns, templates, recipient delivery records, broadcast filtering and pacing.
- `messaging` - client-master bot-mediated conversations and message delivery.
- `finance` - penalties, penalty applications, expenses, revenue/expected revenue report services.
- `audit` - partner-visible activity log and platform/system diagnostic events.

Naming rationale:

- Use `catalog` instead of `services` as an app name so code can still have ordinary `services.py` files without awkward names such as `services.services`.
- Keep `accounts` and `clients` separate instead of merging both into a generic `users` app.
- `accounts` should mean people who can log into the platform/admin: superuser and partner owner/master.
- `clients` should mean partner customers and Telegram-facing identity/profile data.
- This avoids the TableOS complexity where back-office users, Telegram accounts, guest profiles, and admin access live too close together and become hard to reason about.
- Keep `schedule` separate from `appointments`: schedule answers "when can someone book?", appointments answer "what was booked?".
- Keep `notifications` separate from `broadcasts`: reminders are service/appointment delivery, broadcasts are marketing campaign delivery.
- Keep `messaging` separate from `notifications`: manual client-master conversation has different privacy and retention behavior.
- Keep `finance` small in MVP, but give penalties, expenses, and revenue calculations one place to grow before future payments/loyalty arrive.
- Keep `audit` explicit because partner-visible activity and platform diagnostics are cross-cutting and must not be hidden inside random model save hooks.

Potential later splits:

- If studios arrive, `partners` may grow organization/staff concepts or a future `studios` module may be introduced.
- If online payments arrive, `finance` may split payment processing into a separate module.
- If messaging grows moderation, attachments, or support workflows, `messaging` may need stronger substructure.

Recommended internal file pattern per business app:

- `models.py` for persistence definitions.
- `selectors.py` for read/query use cases.
- `services.py` for write/business operations.
- `admin.py` and `forms.py` for admin-specific presentation/validation.
- `tasks.py` for background jobs when the app owns asynchronous work.
- `interfaces.py` only when another module needs a stable narrow contract and direct imports would become messy.

Rule:

- Cross-app writes should go through service functions rather than importing and mutating foreign models casually.
- Cross-app reads should prefer selectors when query rules are non-trivial or tenant-sensitive.
- This `selectors.py` / `services.py` convention is confirmed as the intended project rule, subject to later AGENTS.md/project-skill enforcement.

### Core / Platform

Responsibilities:

- UUID/timestamp base records.
- Partner-bound base concepts.
- Timezone helpers.
- Money minor-unit helpers.
- Sensitive-data masking helpers.
- `retain_until` cleanup policy helpers.
- Shared validation primitives.
- Platform logging and diagnostics primitives.

Avoid:

- Bot handler logic.
- Appointment business rules.
- Partner-specific business workflows.

### Partners

Responsibilities:

- Partner tenant record.
- Partner lifecycle: draft/setup-required, active, suspended.
- Partner timezone/language defaults.
- Partner onboarding state.
- Partner admin ownership.
- Superuser-created partner account flow.
- Partner-level setup readiness checks.

Key invariants:

- A partner cannot expose a bot to clients until minimum setup is complete.
- Suspension stops client-facing bot actions without deleting data.
- Partner-scoped records cannot be created or edited outside current partner scope by non-superusers.

### Accounts / Admin Access

Responsibilities:

- Back-office users.
- Superuser vs partner owner/master access.
- MVP owner/master role only.
- Admin scoping and permission gates.

MVP simplification:

- Do not implement granular partner staff roles yet.
- Keep future role expansion possible.

### Bots

Responsibilities:

- Bot Instance.
- Bot Settings.
- Bot runtime config loading.
- Polling runner.
- Partner runtime middleware.
- Telegram update routing.
- Bot health/status.
- Token validation and encrypted storage.
- Bot content resolution.
- Bot pagination utilities.

Runtime shape:

- A simple runner loads eligible active bot instances on startup.
- Token/status changes may require runner restart/redeploy in MVP.
- Runtime must refuse unsafe configs: suspended partner, missing/invalid token, unsupported mode, duplicate token.
- Runtime middleware must resolve current partner from bot token/config and block processing when partner/bot is paused, suspended, or invalid.

Content shape:

- System content defaults are represented conceptually as `SystemTemplate(key, language)`.
- Partner overrides are represented conceptually as `PartnerTemplateOverride(partner, key, language)`.
- `SystemTemplate` stores the content key, language, default text/label, content type, allowed placeholders, and whether the template is required.
- `PartnerTemplateOverride` stores a partner-specific override for one content key and one language.
- Empty or missing partner override means fallback to the system default in the user's language.
- If a partner customizes one language, other languages continue to use their own system defaults until explicitly overridden.
- If a system default for the requested language is unexpectedly missing, runtime should fall back to Ukrainian before failing.
- Admin content UI should support resetting one partner override/language back to the system default.
- Partner-authored service/category names are not auto-translated.
- Template rendering must validate placeholders at save time and stay defensive at runtime.
- Unknown placeholders and render errors block saving.
- Save-time validation should render against a sample context for the selected content key.
- Initial editable content groups: start/welcome, booking, reschedule/cancellation, reminders, client profile, penalties/no-show/late-arrival, client-master messaging, and broadcast buttons.
- Common navigation buttons and system error/service texts are system-only localized in MVP.
- Initial system-only `common/navigation` keys:
  - `common.back`: no placeholders.
  - `common.next`: no placeholders.
  - `common.cancel`: no placeholders.
  - `common.confirm`: no placeholders.
  - `common.done`: no placeholders.
  - `common.clear`: no placeholders.
  - `common.yes`: no placeholders.
  - `common.no`: no placeholders.
  - `common.open_profile`: no placeholders.
  - `common.book`: no placeholders.
  - `common.my_appointments`: no placeholders.
  - `common.contact_master`: no placeholders.
- Initial system-only `error/service` keys:
  - `error.generic`: no placeholders.
  - `error.invalid_action`: no placeholders.
  - `error.expired_action`: no placeholders.
  - `error.slot_taken`: no placeholders.
  - `error.no_slots`: no placeholders.
  - `error.required_field_missing`: `{missing_fields}`.
  - `error.bot_temporarily_unavailable`: no placeholders.
  - `error.too_many_items_paginated`: no placeholders.
  - `error.delivery_failed`: `{failure_reason}`.
- Broadcast marketing CTA labels are handled separately through editable broadcast button keys.
- Partner-editable start/welcome message is included and can be overridden per language.
- Initial editable `start/welcome` keys:
  - `start.welcome`: `{partner_name}`.
  - `start.choose_language`: no placeholders.
  - `start.profile_incomplete`: `{missing_fields}`.
- Initial editable `booking` keys:
  - `booking.category_select`: no placeholders.
  - `booking.service_select`: `{category_name}`.
  - `booking.summary`: `{services}`, `{duration}`, `{service_total}`, `{currency}`.
  - `booking.date_select`: `{booking_horizon_days}`.
  - `booking.time_select`: `{date}`, `{services}`, `{duration}`.
  - `booking.near_fit_notice`: `{tolerance_minutes}`.
  - `booking.comment_prompt`: `{max_length}`.
  - `booking.final_confirm`: `{services}`, `{date}`, `{time}`, `{duration}`, `{service_total}`, `{currency}`, `{comment}`.
  - `booking.requested_created`: `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `booking.confirmed_created`: `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `booking.request_cancelled_by_client`: `{date}`, `{time}`.
  - `booking.request_confirmed_by_master`: `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `booking.request_declined_by_master`: `{partner_name}`, `{date}`, `{time}`, `{reason}`.
- `booking.slot_taken`, `booking.selection_changed`, `booking.month_select`,
  and "no available slots" remain system-only localized service texts in MVP.
- Ordinary client booking summary/final confirmation does not show penalties unless penalties have already been explicitly applied to that appointment.
- Initial editable `cancellation/reschedule` keys:
  - `appointment.cancel_confirm_prompt`: `{date}`, `{time}`, `{services}`.
  - `appointment.cancelled_by_client`: `{date}`, `{time}`, `{services}`.
  - `appointment.cancelled_by_master`: `{partner_name}`, `{date}`, `{time}`, `{services}`, `{reason}`.
  - `appointment.late_cancel_penalty_warning`: `{penalty_amount}`, `{currency}`, `{deadline_hours}`.
  - `appointment.late_cancelled_with_penalty`: `{penalty_amount}`, `{currency}`.
  - `appointment.reschedule_start`: `{date}`, `{time}`, `{services}`.
  - `appointment.reschedule_requested`: `{old_date}`, `{old_time}`, `{new_date}`, `{new_time}`, `{services}`.
  - `appointment.reschedule_auto_confirmed`: `{old_date}`, `{old_time}`, `{new_date}`, `{new_time}`, `{services}`.
  - `appointment.reschedule_proposed`: `{partner_name}`, `{date}`, `{time}`, `{services}`, `{proposal_expires_at}`.
  - `appointment.reschedule_proposal_accepted`: `{new_date}`, `{new_time}`, `{services}`.
  - `appointment.reschedule_proposal_declined`: `{date}`, `{time}`, `{services}`.
  - `appointment.reschedule_proposal_expired`: `{date}`, `{time}`, `{services}`.
- `appointment.late_cancel_blocked` remains system-only localized in MVP.
- Initial editable master notification keys for this group: `master.client_cancelled_appointment`, `master.client_requested_reschedule`, `master.client_accepted_reschedule`, `master.client_declined_reschedule`, and `master.appointment_proposal_expired`.
- Cancellation/reschedule `{reason}` is optional; templates must render cleanly when it is empty.
- Initial editable `reminders` keys:
  - `reminder.client_before_appointment`: `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `reminder.master_before_appointment`: `{client_name}`, `{client_phone}`, `{date}`, `{time}`, `{services}`.
  - `reminder.master_morning_digest_empty`: `{date}`.
  - `reminder.master_morning_digest`: `{date}`, `{appointments}`.
  - `reminder.stale_requested`: `{client_name}`, `{date}`, `{time}`, `{services}`, `{request_age}`.
  - `reminder.failed_delivery_to_master`: `{client_name}`, `{date}`, `{time}`, `{services}`, `{failure_reason}`.
- Repeated stale requested reminders reuse `reminder.stale_requested`; no separate repeated template is needed in MVP.
- `reminder.manual_retry_sent` and `reminder.manual_retry_failed` remain system-only localized in MVP.
- `{failure_reason}` shown to partners must be business-friendly and must not expose raw technical payloads.
- Initial editable `client profile` keys:
  - `profile.main`: `{client_name}`, `{phone}`, `{language}`, `{visit_count}`, `{extra_text}`.
  - `profile.missing_fields_prompt`: `{missing_fields}`.
  - `profile.future_appointments_empty`: no placeholders.
  - `profile.future_appointments_list`: `{appointments}`.
  - `profile.history_empty`: no placeholders.
  - `profile.history_list`: `{appointments}`.
  - `profile.appointment_details`: `{date}`, `{time}`, `{services}`, `{status}`, `{service_total}`, `{penalties_total}`, `{total}`, `{currency}`, `{comment}`, `{reason}`.
  - `profile.penalties_empty`: no placeholders.
  - `profile.penalties_list`: `{penalties}`, `{total_penalties}`, `{currency}`.
  - `profile.contact_master_prompt`: `{partner_name}`.
- `profile.updated_success`, `profile.marketing_unsubscribed`, `profile.marketing_subscribed`, and `profile.contact_master_sent` remain system-only localized in MVP.
- `profile.appointment_details` must be able to show penalties and total when penalties are applied to that appointment.
- Profile detail `{comment}` and `{reason}` are optional; templates must render cleanly when either is empty.
- Initial editable `penalties/no-show/late-arrival` keys:
  - `penalty.created_late_cancel`: `{penalty_amount}`, `{currency}`, `{appointment_date}`, `{appointment_time}`.
  - `penalty.created_no_show`: `{penalty_amount}`, `{currency}`, `{appointment_date}`, `{appointment_time}`.
  - `penalty.created_late_arrival`: `{penalty_amount}`, `{currency}`, `{late_minutes}`, `{appointment_date}`, `{appointment_time}`.
  - `penalty.applied_to_appointment`: `{penalty_amount}`, `{currency}`, `{appointment_date}`, `{appointment_time}`, `{appointment_total}`.
  - `penalty.paid`: `{penalty_amount}`, `{currency}`.
  - `penalty.waived`: `{penalty_amount}`, `{currency}`.
- Initial editable master-facing penalty notification keys:
  - `master.penalty_created`: `{client_name}`, `{penalty_reason}`, `{penalty_amount}`, `{currency}`.
  - `master.penalty_paid`: `{client_name}`, `{penalty_amount}`, `{currency}`.
  - `master.penalty_waived`: `{client_name}`, `{penalty_amount}`, `{currency}`.
- Internal penalty status labels such as `unpaid`, `applied_to_appointment`, `paid`, and `waived` remain system-only localized.
- Technical penalty validation errors, "penalties disabled" explanations, and admin-only correction confirmations remain system-only localized in MVP.
- `{penalty_reason}` in partner-editable templates must receive a localized business label, not a raw enum value.
- Initial editable `client-master messaging` keys:
  - `messaging.contact_master_prompt`: `{partner_name}`.
  - `messaging.client_message_received_for_master`: `{client_name}`, `{client_phone}`, `{message_preview}`.
  - `messaging.master_reply_received_for_client`: `{partner_name}`, `{message_text}`.
- Client confirmation after sending a message to the master remains system-only localized in MVP.
- Delivery failure texts for client-master messaging remain system-only localized in MVP.
- Messaging delivery failure reasons shown to partner/client must be business-friendly and must not expose raw Telegram payloads.
- Master-facing new-message notifications may include client phone when available.
- Initial editable `broadcast buttons` keys:
  - `broadcast.default_cta_book`: no placeholders.
  - `broadcast.default_cta_my_appointments`: no placeholders.
  - `broadcast.default_cta_external_link`: no placeholders.
- Broadcast message body is partner-authored content, either typed manually for a campaign or reused from the partner's saved broadcast templates.
- The system does not provide generic marketing body defaults in MVP; system defaults are used for reusable button labels and service messages.
- Broadcast button labels are language-specific partner overrides with the same fallback rule as other editable button labels.
- If a partner leaves a broadcast button label override empty for a language, the system default for that language is used.
- `broadcast.unsubscribe_confirmed` remains system-only localized in MVP.
- Broadcast delivery/statistics status labels and service errors remain system-only localized in MVP.

Registry validation and extensibility rules:

- The MVP registry above is the approved initial content-key registry.
- Every registry key must have system defaults for `uk`, `ru`, and `en` before it can be used in runtime.
- Ukrainian is the fallback language if another system default is unexpectedly missing.
- Every editable key must define an allowed placeholder list and a sample validation context.
- System-only keys can still have placeholder allowlists when needed, but partners cannot override them.
- Adding new content keys later is allowed when the key has system defaults, placeholder allowlist, sample validation context, and an explicit editable/system-only classification.
- Exact default localized copy wording for `uk`, `ru`, and `en` is a seed/copywriting task and does not reopen the MVP registry decision.

### Telegram Users / Clients

Responsibilities:

- Global Telegram identity.
- Partner-scoped client profile.
- Manual client profile without Telegram identity.
- Suggested link/merge between manual client and Telegram-linked client.
- Partner-bot scoped communication flags.

Recommended data split:

- Global Telegram identity: Telegram user id, username, names, Telegram Premium flag, language hint, last seen.
- Partner client profile: partner, optional Telegram identity, name, phone, selected language, private master note, additional text, profile metadata, and lightweight partner-bot communication flags.
- Communication flags stay on `ClientProfile` in MVP: `marketing_opt_out`, `bot_unreachable`, `last_delivery_failure_code`, and `last_delivery_failure_at`.
- Marketing opt-out affects broadcasts only; service notifications and manual master messages remain allowed.
- `bot_unreachable` is set only for terminal Telegram delivery failures such as blocked/deactivated/chat-not-found.
- Raw Telegram failure payloads do not live on `ClientProfile`; detailed delivery attempts/diagnostics are stored separately with masking and retention.
- A new incoming message/update from this client to the partner bot should clear `bot_unreachable`.
- Manual clients without Telegram identity are displayed as having no Telegram connection, not as blocked/unreachable.

Critical rule:

- Never mix client profile data between partners, even when Telegram identity is shared.

### Services

Responsibilities:

- Partner-defined service categories.
- Partner-defined services.
- Display ordering.
- Active/inactive visibility.
- Price and duration configuration.

MVP service model direction:

- Category: partner, name, optional description, sort order, active/inactive.
- Service: partner, category, name, optional short description, price minor units, currency, duration minutes, sort order, active/inactive.
- No platform/global service catalog.

### Schedule / Availability

Responsibilities:

- Availability windows.
- Blocked time.
- Availability templates.
- Template application preview.
- Booking horizon.
- Slot step.
- Near-fit tolerance.
- Conflict detection.

Recommended records:

- Availability window: partner, start/end, source/template metadata when applicable.
- Blocked time: partner, start/end, optional reason.
- Availability template: partner, name, reusable window rules.
- Availability conflict: derived or stored depending on implementation complexity; must show partner actionable conflicts.
- Availability template window/rule: weekday, local start time, local end time for reusable weekly-style patterns.

Key rules:

- Clients see slots only inside availability windows.
- Availability windows are concrete UTC `start_at` / `end_at` intervals.
- Availability templates do not create client-visible slots by themselves; applying a template creates concrete availability windows for the selected period.
- Overlapping or adjacent availability windows are allowed and may be merged logically during slot generation/calendar display.
- Blocked time removes availability.
- Blocked time is a concrete UTC interval with optional reason.
- Blocked time may exist outside availability windows; it only affects booking where it intersects availability, but can still appear in the partner calendar.
- Confirmed appointments block time.
- Requested appointments do not block public availability, but confirmation must re-check conflicts.
- Near-fit tolerance only allows exceeding availability window end within configured minutes.
- Near-fit never allows overlap with confirmed appointment or blocked time in MVP.
- Calendar UI should support selecting a time range to create an availability window, blocked time, or manual appointment.
- Calendar UI should also support quick manual input for availability windows.
- Availability windows, blocked time, appointments, conflicts, and near-fit/request markers should be visually distinguishable by type.
- Clicking an appointment in Calendar should open a detail panel/modal with actions: edit, propose reschedule, cancel, mark completed, mark no-show, and message client.
- Calendar should support quick copy actions such as copy day and copy week in addition to saved availability templates.
- Calendar admin should be mobile-usable with adapted interactions, while dense editing may remain more comfortable on desktop/tablet in MVP.
- MVP availability templates store weekday/start/end rules. Date-specific template windows are not required in MVP.
- Availability windows and blocked time can be physically deleted in MVP. Existing confirmed appointments are not moved silently; conflicts are surfaced for partner action.

Validation and indexing direction:

- Add database-level `start_at < end_at` checks for time-range records such as availability windows, blocked time, appointments, and proposal options.
- Add `start_time < end_time` validation/checks for availability template window rules.
- Normal partner admin/service flows should block creating new availability windows or blocked time in the past; historical records remain stored and can be queried when needed.
- Limit one availability window to a maximum of 24 hours.
- Limit one blocked-time interval to a maximum of 31 days.
- MVP does not support overnight template window rules. Partners should split overnight availability into two day-local rules.
- Index partner-scoped time-range records for calendar/slot queries, starting with `partner_id + start_at` and `partner_id + end_at`.
- Appointments should also have a `partner_id + status + start_at` style index for calendar, status, and conflict queries.
- The exact PostgreSQL/Django index definitions remain implementation-design details, but the query shape is confirmed.

Partner setting bounds:

- `slot_step_minutes`: allowed values 5, 10, 15, 20, 30, 60; default 15.
- `booking_horizon_days`: min 1, max 365, default 30.
- Client reminder offsets: min 15 minutes, max 30 days, defaults 24 hours and 2 hours, max 5 rules per partner.
- Master reminder offset: min 5 minutes, max 7 days, default 45 minutes, can be disabled.
- `near_fit_tolerance_minutes`: min 0, max 30, default 10.
- `cancellation_deadline_hours`: min 0, max 168, default 6. `0` means clients may cancel until appointment start without late-cancel penalty.
- `require_phone_for_booking`: boolean, default false. When enabled, client booking pauses until the partner-scoped client profile has a valid normalized phone.
- Quiet hours: local start/end times in partner timezone, default 22:00-08:00, can be disabled, and overnight quiet hours are supported.
- Morning digest time: local time in partner timezone, any time from 00:00 to 23:59, default 08:00, can be disabled. If it falls inside quiet hours, still send at the configured digest time.
- Stale requested reminder base interval: min 10 minutes, max 72 hours, default 3 hours. Repeated reminders halve the interval down to a 5-minute floor.

Invalid saved settings policy:

- Prefer save-time validation for new values.
- If an older saved setting becomes invalid after a system change and a safe default exists, runtime should use the safe default and show a warning.
- Runtime blocks only when safe operation is impossible: invalid/missing bot token, missing timezone, missing language, corrupted encrypted token, missing system content default for fallback, or severely broken critical bot settings.
- Partner-visible warnings appear in Bot/settings/admin.
- Superuser sees setting/runtime warnings in problem/health views.
- Invalid legacy settings must not crash bot runtime or background jobs.

### Appointments

Responsibilities:

- Booking flow result.
- Manual appointments.
- Multi-service snapshots.
- Appointment statuses.
- Reschedule/cancellation flows.
- Outcome corrections.
- Conflict-safe confirmation.

Manual appointment rules:

- Calendar-created manual appointment flow starts with the selected time/range, then client and service details.
- Partner can create a manual client inline when no matching client exists.
- Manual appointments are `confirmed` by default.
- Manual appointments for Telegram-linked clients should trigger a Telegram notification.
- Manual appointments may be created outside availability windows with a warning, but still use confirmed-overlap protection.

Recommended records:

- Appointment.
- Appointment service item line.
- Unified appointment proposal.
- Appointment correction/audit reason linkage.

Appointment statuses:

- `requested`
- `confirmed`
- `cancelled_by_client`
- `cancelled_by_master`
- `completed`
- `no_show`

Core rules:

- Confirmed appointments for the same booking resource cannot overlap.
- Requested appointments may overlap but cannot both become confirmed for the same overlapping time.
- Confirming, auto-confirming, restoring, or accepting a reschedule must run the same conflict check.
- Service title, price, duration, currency, and manual override markers are snapshotted into appointment item lines.
- Appointment item lines are used for every appointment, including one-service appointments.
- One-off custom/manual services remain appointment item lines only and are not added to the partner service catalog automatically.
- Future confirmed appointment changes visible to the client require client confirmation.
- Client-visible future appointment changes include time/date, service set, price, duration, and client-visible comment/description.
- Internal-only changes such as master private note, internal-only status metadata, and internal comments do not require client confirmation.
- While a client-visible change proposal is pending, the existing appointment remains unchanged.
- Client-visible change proposals use accept/decline actions and expire after 48 hours by default.
- If a change proposal expires or is declined, the old appointment remains unchanged and both sides should be notified where possible.
- Proposal storage starts with one typed unified `AppointmentProposal` model covering master-initiated reschedule proposals, requested-appointment alternative slot proposals, and client-visible appointment change proposals.
- The proposal model should use explicit proposal type/status fields and structured snapshots rather than becoming an arbitrary untyped payload bucket.
- MVP proposal types are `reschedule_confirmed`, `requested_alternatives`, and `appointment_change`.
- Changes to services, price, duration, or client-visible appointment text without a time change are part of `appointment_change`.
- MVP proposal statuses are `pending`, `accepted`, `declined`, `expired`, `cancelled`, and `superseded`.
- Creating a new proposal for an appointment should supersede older pending proposals for that appointment.
- Proposed slots/options should use separate structured records such as `AppointmentProposalOption`.
- `AppointmentProposal` should own metadata: partner, appointment, type, status, creator/actor, expires_at, client-visible message/reason, and response timestamps.
- `AppointmentProposalOption` should own proposed variants: start/end when applicable, total duration snapshot, service total snapshot, penalty total snapshot when relevant, final total snapshot, currency, selected/accepted marker, and display ordering.
- `AppointmentProposalOptionItem` should own proposed service item snapshots when a proposal changes services, price, duration, or includes a one-off custom service.
- Time-only proposals can omit option item rows and reuse current appointment items conceptually.
- Business-critical proposed values should live in typed fields/rows rather than only in JSON payloads.
- If client confirmation is impossible, such as for a manual client without Telegram identity, the master can save directly with audit logging and a clear indication that the client was not notified/confirmed.
- Final statuses can be corrected with audit reason.
- Restoring a future final appointment to confirmed requires availability re-check and fresh reminders.

### Penalties

Responsibilities:

- Late cancellation penalties.
- No-show penalties.
- Late-arrival penalties.
- Applying penalties to appointments.
- Marking penalties paid or waived.

Recommended records:

- Penalty: partner, client profile, reason, amount, currency, status, source appointment, optional note/reason.
- Penalty application: penalty applied to an appointment, amount snapshot, settlement state if needed.

Statuses:

- `unpaid`
- `applied_to_appointment`
- `paid`
- `waived`

Rules:

- Penalties are created for concrete business reasons only.
- Default late-cancel and no-show penalty is `50.00 UAH`.
- Default late-arrival penalty is `20.00 UAH`.
- Default late-arrival threshold is 15 minutes.
- If an appointment with applied unpaid penalty is cancelled, the penalty returns to `unpaid`.
- The penalty application should be released historically rather than erased, so the system can explain why the penalty returned to `unpaid`.
- Partner can waive penalties.
- Client sees active unpaid penalties when penalties are enabled.

### Notifications / Background Jobs

Responsibilities:

- Client business event notifications.
- Client appointment reminders.
- Master reminders.
- Morning digest.
- Stale requested reminders.
- Failed reminder retries.
- Explicit resend actions.
- Quiet-hours handling.
- Delivery records.

Recommended records:

- Scheduled notification/reminder.
- Delivery attempt.
- Delivery failure marker visible to partner when retries are exhausted.

Rules:

- Client business event notifications are required for appointment created/confirmed, appointment change proposed, change accepted/declined/expired, appointment cancelled, appointment completed, no-show marked, and penalty created/paid/waived.
- For manual clients without Telegram identity, Telegram notifications are not created; admin should show that no Telegram connection exists.
- If Telegram reports that the client blocked the partner bot, the event is logged, the client is marked unavailable for that partner bot, and partner-visible delivery failure is shown where action is useful.
- Terminal delivery state is applied to the actual recipient class: a master-facing failure may update the master contact, but must never mark the appointment client unreachable merely because the notification also references that client for rendering context.
- Failed appointment change proposal delivery should expose a manual notify/resend action for the master.
- Reminder scheduling happens when appointment becomes confirmed.
- Creating a manual-confirmation `requested` appointment creates two independent durable
  master-facing records: an immediate actionable booking-request notification and the first
  delayed stale-request reminder. Both become stale when the request is resolved.
- Master confirm/decline Telegram callbacks resolve the active partner contact from trusted bot
  and Telegram context, then call the appointment owner service. Confirm still acquires the
  booking-resource advisory lock and runs a fresh overlap check.
- Confirming or declining a request creates a separate durable client notification after commit;
  Telegram delivery never runs inside the appointment transaction.
- Old pending reminders are cancelled/staled on reschedule, cancellation, completion, or no-show.
- The pilot admin exposes a service-backed time-proposal action for requested and confirmed appointments; proposal and option records remain read-only operational history.
- A time proposal may contain multiple alternatives. Client callbacks carry only opaque record identifiers and re-resolve partner/client ownership before calling the proposal owner service.
- Proposal creation, client response, and expiry notifications are durable `ScheduledNotification` records linked to the exact proposal. Superseded or otherwise non-matching proposal notifications become stale before delivery.
- Celery beat expires due proposals through the maintenance queue. Requested-alternative expiry may cancel the still-requested appointment, while confirmed appointments remain unchanged.
- Jobs are idempotent by business key, not just queue id.
- Quiet hours defer sending until quiet hours end.
- If deferred reminder becomes useless because appointment passed, skip it.
- Failed reminder gets 3 automatic attempts with `1 min -> 5 min -> 15 min` backoff.
- User-blocked bot is terminal failure.
- Master can explicitly resend failed reminder without a hard limit.

### Job Queue / Scheduler Operational Model

Status: stack confirmed on 2026-09-10; queue routing and production MVP process roles confirmed on 2026-09-16; MVP health direction and thresholds confirmed on 2026-09-17; Docker deployment direction, initial Compose topology, restart/logging/runbook direction confirmed on 2026-09-17; exact operational runbook commands still pending implementation design.

Confirmed stack:

- Use Celery for background task execution.
- Use Redis as the Celery broker for MVP, following the TableOS direction.
- Do not treat Celery result backend as the source of truth for business delivery state; durable outcomes live in PostgreSQL records.
- Use Celery Beat or an equivalent scheduler process for periodic dispatchers.
- Do not introduce a generic event bus/outbox system for MVP unless a concrete requirement appears.

Required runtime processes for production MVP:

- Web/admin process.
- Bot polling runner process.
- Celery Beat / scheduler process for due reminders, scheduled broadcasts, stale requested reminders, cleanup, and health checks.
- Notifications worker for reminders, failed-reminder retries, morning digest, stale requested reminders, and manual resend actions.
- Broadcast worker for broadcast recipient fan-out and Telegram rate-limit/backoff handling.
- Default worker for `default` and `maintenance` queues.
- PostgreSQL.
- Redis.

Confirmed queue routing direction:

- `default` for ordinary short background jobs.
- `notifications` for reminders, failed-reminder retries, morning digest, stale requested reminders, and manual resend actions.
- `broadcasts` for broadcast recipient fan-out and Telegram rate-limit/backoff handling.
- `maintenance` for cleanup and low-priority diagnostics, if keeping it separate remains useful during implementation.

MVP deployment policy:

- MVP may run on a single server/VPS with Docker Compose.
- PostgreSQL and Redis may run in Docker Compose for MVP.
- A single Celery worker may consume multiple queues during early development.
- Production MVP must isolate `broadcasts` from urgent reminders, preferably through a separate broadcast worker; strict queue routing/concurrency is the fallback if process count must stay smaller.
- Production MVP should also keep `default` / `maintenance` work off the notifications worker by using a small default worker.
- The scheduler/beat process must be explicitly run; a scheduled feature is not considered supported if no scheduler process exists.
- Broadcast queue lag can be acceptable when a campaign is large and progress is visible.
- Notifications queue lag is a health problem because reminders and service messages are time-sensitive.
- Superuser/admin operations should include a basic health dashboard for queue/worker/beat status; logs alone are not enough for MVP operations.
- Health status should be expressed as simple `OK`, `Warning`, and `Critical` states.
- Partner-facing admin should show only partner-relevant bot health/status, not internal queue/worker/beat infrastructure.
- External Telegram/email alerts are nice-to-have for MVP, not required for the first implementation.

Periodic dispatcher direction:

- Store durable schedule state in the database for reminders, broadcasts, and cleanup-eligible records.
- Use periodic dispatcher tasks to find due work instead of relying only on long-future broker ETA messages.
- Due reminder dispatcher cadence: about once per minute.
- Stale requested appointment dispatcher cadence: about once per minute, while respecting the per-request next-reminder time and partner quiet hours.
- Morning digest dispatcher cadence: about once per minute, checking partner timezone and configured digest time.
- Scheduled broadcast dispatcher cadence: about once per minute.
- Cleanup dispatcher cadence: daily is enough for MVP.
- Bot/runtime health check cadence: every few minutes is enough unless operational issues suggest otherwise.

Health / supervision direction:

- Maintain durable platform health records for runtime components such as `beat`, `notifications-worker`, `broadcast-worker`, and key queues.
- Health records should represent current component state and be overwritten/updated, not append an infinite stream of heartbeat rows.
- Suggested health fields: `component`, `status`, `last_seen_at`, `last_success_at`, `last_error_at`, masked `last_error_message`, and small JSON metadata.
- Use internal ping/heartbeat tasks so the system verifies that queues are actually moving, not only that a process exists.
- MVP status thresholds:
  - `beat`: `Warning` if stale for more than 3 minutes, `Critical` if stale for more than 5 minutes.
  - `notifications` ping: `Warning` if not processed for more than 3 minutes, `Critical` if more than 5 minutes.
  - due reminder lag: `Warning` if due work is more than 2 minutes late, `Critical` if more than 5 minutes late.
  - `broadcasts` ping: `Warning` if not processed for more than 10 minutes, `Critical` if more than 30 minutes.
  - active broadcast without progress: `Warning` after 15 minutes, `Critical` after 60 minutes.
- Noisy health history, ping attempts, and diagnostic records should be cleanup-eligible with `retain_until`; current health rows should stay small and current.
- Docker is the confirmed deployment direction for MVP.
- Process supervision is required for production MVP and should start with Docker Compose restart policies / healthchecks unless the deployment target later demands a heavier orchestrator.
- Kubernetes or other heavy orchestration is not required for MVP.
- Initial Docker Compose service direction: `web`, `bot-runner`, `celery-beat`, `notifications-worker`, `broadcast-worker`, `default-worker`, `postgres`, and `redis`.
- Initial Celery concurrency direction: `notifications-worker` concurrency around 4, `broadcast-worker` concurrency 1, and `default-worker` concurrency 1.
- A dedicated `maintenance-worker` is not required in MVP unless cleanup/diagnostics become heavy enough to justify separating it later.
- Bot runner hot reload is not part of MVP; bot token/status changes take effect through runner restart/redeploy.
- MVP restart/deploy procedure direction: rebuild/pull containers, run required migration/deploy steps when implementation exists, restart services through Docker Compose, then verify the superuser health dashboard.
- MVP logging direction: Docker stdout/stderr plus application-level sensitive-data masking, durable audit/domain records for business events, and no dedicated ELK/Loki/Sentry requirement for first implementation.
- Sentry or an equivalent error-monitoring service is nice-to-have/post-MVP, not required for MVP.
- A short operational runbook should exist before implementation starts, covering what to check/restart for reminders, broadcasts, bot runner, invalid bot token, PostgreSQL/Redis availability, growing queues, and disk/log growth.
- External Telegram/email alerts are not required for MVP, but the health model should not block adding them later.

Post-commit rule:

- Business services must persist durable intent/state first.
- Background jobs are enqueued with an after-commit mechanism so workers cannot observe work that later rolls back.
- Telegram/network I/O must not run inside the source business transaction.
- Long sleeps/retry loops must not run inside database transactions.

Task contract:

- Persist intent: create or update a durable source record, delivery record, or job record.
- Enqueue after commit: schedule the task only after the business transaction commits.
- Claim work: worker claims a due unit with a lease/idempotency key in a short transaction.
- Deliver: Telegram/network I/O happens outside the claim transaction.
- Record result: worker stores attempt/outcome/error/counters in a short follow-up transaction.
- Retry safely: retry uses stored state and must not recreate an already completed delivery.
- Recover: crashed workers leave observable state that another worker or cleanup/recovery job can handle.

Delivery guarantee:

- Internally target effectively-once business effects per stable delivery key.
- Telegram itself does not provide a perfect exactly-once delivery guarantee.
- Crash after Telegram accepts a message but before BoteraMaster records success may still create duplicate-send risk.
- MVP should minimize that risk with short tasks, durable delivery records, leases, attempt history, and retry rules.
- Partner-facing stats should reflect BoteraMaster's known send outcome, not promise Telegram read/delivered status.

Broadcast pacing direction:

- Start with per-partner-bot pacing and never send faster than one message every 0.5 seconds per bot.
- Check recipient eligibility again at delivery time.
- Handle Telegram `429` by recording backoff/next attempt time and continuing later.
- Use a simple configurable global broadcast safety limit / worker concurrency cap so broadcasts cannot overload BoteraMaster itself as the number of active bots grows.
- Avoid one huge long-running transaction or one task that blindly loops through all recipients without durable recipient state.
- Large broadcasts may remain in progress for a long time; this is acceptable if stats and progress are visible.
- Partner-facing progress should show recipient counters/statuses (`queued`, `sent`, `failed`, `skipped`), percentage or equivalent progress, and that the campaign is still sending.
- If the broadcast worker crashes and restarts, sending must resume from durable unsent recipient records without duplicating recipients that were already recorded as sent/skipped/failed.

Operational alerts:

- Superuser/platform diagnostics should surface repeated task failure.
- Superuser/platform diagnostics should surface whether scheduler/beat, notifications worker, and broadcast worker appear alive.
- Alert/diagnose stranded leases.
- Alert/diagnose campaign counter mismatches.
- Alert/diagnose repeated Telegram `429` or invalid bot token failures.
- Partner-visible errors should be business-friendly; raw technical payloads stay platform-only and masked.

### Broadcasts

Responsibilities:

- Campaign creation.
- Filters.
- Immediate/scheduled send.
- Text/photo/buttons.
- Marketing unsubscribe.
- Per-recipient status stats.
- Pacing and Telegram `429` handling.

Recommended records:

- Broadcast campaign.
- Broadcast template.
- Broadcast recipient delivery record with `retain_until`.
- Broadcast delivery attempt with `retain_until`.

Statuses:

- Campaign: draft/scheduled/queued/sending/completed/cancelled/failed-partial, exact names still need design.
- Recipient: `queued`, `sent`, `failed`, `skipped`.

Rules:

- Only clients who have not opted out of marketing and are reachable are eligible.
- Recipient eligibility is checked again at send time.
- Telegram delivered status is not promised.
- Detailed recipient records are cleanup-eligible.
- Manual retry of failed recipients is post-MVP.

### Messaging

Responsibilities:

- Client writes to master from client personal area.
- Master replies through bot.
- Delivery failure handling.
- Conversation history for partner/client scope.

Recommended records:

- Conversation thread scoped by partner and client profile, or message records scoped by partner/client if a separate thread is unnecessary.
- Message delivery attempt records when needed for retry/diagnostics.

Rules:

- MVP messaging is not tied to a specific appointment.
- Master and client both use the partner bot as the delivery channel.
- Client free-text composition may use short-lived in-memory bot state, but a
  message becomes business data only after validated text is stored through the
  messaging owner service.
- MVP text messages are limited to 3500 characters so localized/template
  wrappers remain within Telegram's practical message limit.
- Conversation records are immutable in partner admin. Master replies use a
  dedicated service-backed action instead of direct model creation/editing.
- If client blocks bot, master sees delivery failure and client becomes unreachable for this partner bot.
- Superuser access is support/incident-only.

### Reports / Expenses

Responsibilities:

- Actual revenue report.
- Expected revenue report.
- Penalties line.
- Manual expenses.

Rules:

- Actual revenue comes from completed appointments using current corrected appointment state.
- Paid/settled applied penalties on completed appointments are included.
- Expected revenue comes from future confirmed appointments and includes only penalties already applied to those appointments.
- Reports use partner timezone.
- Expenses are manual partner-only records: date, amount, currency, description/reason.
- Expense changes are audit logged.

### Audit / Activity

Responsibilities:

- Partner-visible business activity log.
- Platform-only technical diagnostics.
- Correction reasons.
- Sensitive-data masking.

Partner-visible audit includes:

- Appointments.
- Penalties.
- Expenses.
- Broadcasts.
- Reminders.
- Client-master messages.
- Bot/settings changes.
- Sensitive/destructive partner actions, including appointment cancellation, no-show marking, penalty changes/payment/waiver, expense changes/deletion, bot token/settings changes, and corrections after final status.
- Partner-visible audit entries must be business-friendly and must not expose raw technical payloads.
- Audit reason is required for correction-flow actions after a final appointment status.
- Reason is optional in MVP for master cancellation, penalty waiver, and expense deletion.

Retention:

- Partner-visible business audit: 6 months.
- Cleanup-eligible noisy records: 90 days by default via `retain_until`.

Sensitive-data rule:

- Bot tokens are stored only encrypted at rest. Raw token values are allowed only during input/replacement and inside runtime decrypt.
- Bot token UI shows only status/masked representation after save, never the full token.
- Recommended bot-token encryption uses a small `core.security.crypto` wrapper over authenticated symmetric encryption from `cryptography`, preferably `Fernet`/`MultiFernet` unless implementation finds a concrete blocker.
- Encryption keys are configured through environment/secret storage, never the database or repository files.
- Use a key-ring setting such as `FIELD_ENCRYPTION_KEYS`: the first key encrypts new values, all configured keys can decrypt old values, and encrypted rows store `token_encryption_key_id` for future rotation clarity.
- Runtime/config validation must fail clearly if required encryption keys are missing, malformed, or cannot decrypt an active bot token.
- Duplicate-token detection uses a non-reversible keyed HMAC/fingerprint such as `token_fingerprint`, not raw token comparison or deterministic ciphertext.
- The token fingerprint HMAC key should come from environment/secret storage as a separate setting such as `TOKEN_FINGERPRINT_KEY`.
- Client phone is stored in normalized usable form, because the owning master needs the full number in client/appointment/communication contexts.
- Client phone is not app-field-encrypted in MVP because search, manual-client linking, display to the owning master, and admin workflows need usable normalized phone data.
- Broad partner UI lists should avoid casually exposing full phone values where a masked/short display is enough.
- Client-master message text is stored as partner-scoped business data for conversation history/support, but raw message text must not enter technical logs.
- Master private notes are stored as partner-scoped sensitive business data visible to the partner/master, but raw private note text must not enter technical logs or audit payloads.
- Client-master messages and master private notes are not app-field-encrypted in MVP. They are protected by partner scoping, access control, and redaction from logs/diagnostics. Future stricter privacy requirements may revisit field-level encryption.
- Superuser should not have a casual/default UI for reading master private notes, though technical/support access may exist for incident handling.
- Partner-visible audit should record message/private-note related business events without embedding full raw message/private-note text; short masked/preview values are acceptable when useful.
- A shared masking/redaction helper should cover bot tokens, phone numbers, message text, private notes, and raw external payloads before they reach diagnostics/errors/logs.
- Recommended helper API includes `mask_bot_token`, `mask_phone`, `redact_sensitive_text`, recursive `redact_mapping`, and `safe_exception_message` for Telegram/runtime/crypto exceptions.
- Raw Telegram payloads should not be persisted without filtering/masking.
- Technical logs, platform diagnostics, audit payloads, and exception rendering must use redacted data by default.
- Focused tests should cover token encryption/decryption, key-ring decrypt behavior, duplicate-token fingerprinting, recursive redaction, phone/token masking, and absence of raw token/phone/message/private-note values in diagnostic payload helpers.

## Partner Admin UX

- MVP partner admin main navigation should include Dashboard, Calendar, Appointments, Clients, Catalog, Broadcasts, Finance, Bot, and Activity Log.
- Dashboard is the first partner admin screen and should surface today's/tomorrow's appointments, pending requests, failed notifications, active conflicts/problems, and compact stats/revenue.
- Calendar is the primary day-to-day working screen for the master because availability and appointment management are central to the product.
- Appointments list remains a secondary searchable/filterable operational view.
- Partner Activity Log should exist as a standalone screen, while appointment and client cards may show short related local history.
- Superuser admin in MVP can remain Django-admin-like/pragmatic, with dedicated operational views/filters for health, setup-required partners, and problems/incidents.

## Schema Strategy Draft

Status: PostgreSQL production target confirmed on 2026-09-08; MVP overlap protection strategy confirmed on 2026-09-09; final schema details still pending approval.

General schema rules:

- Use UUID primary keys for domain records.
- Use `created_at` and `updated_at` on persistent domain records.
- Use explicit `partner` foreign key on partner-owned records.
- Use database constraints for uniqueness and critical invariants where possible.
- Use application/service-layer checks for invariants that cannot be fully expressed as portable database constraints.
- Store money in integer minor units plus currency.
- Store all datetime values in UTC and render/filter/report in partner timezone.
- Keep denormalized snapshots when history must remain stable after partner edits source data.
- Use `retain_until` only for cleanup-eligible noisy records, not core business records by default.

Recommended database direction:

- PostgreSQL is the confirmed real MVP production database target because appointment overlap prevention and indexed time-range querying matter.
- SQLite can be acceptable only for early local experiments, not as the production target.
- Confirmed appointment overlap protection starts with a transactional service-layer check plus a PostgreSQL transaction-level advisory lock keyed by booking resource during operations that can create/confirm a `confirmed` appointment.
- PostgreSQL range/exclusion constraints are deferred as a later hardening option unless they prove simple to add during schema design.

Tenant strategy:

- All partner-owned tables should include `partner_id`.
- Common indexes should start with `partner_id` when queries are partner-scoped.
- Admin and bot-facing selectors must always receive partner context for partner-owned data.
- Unique constraints that are business-unique inside a partner should be scoped by `partner_id`.

Time-range strategy:

- Availability windows, blocked time, and appointments all need indexed start/end timestamps.
- Client date selection shows seven nearest available dates per page and a
  month picker containing only months/dates with valid slots inside the partner
  booking horizon.
- Wide-horizon date discovery should preload availability, blocked time, and
  confirmed busy intervals for the requested range once, then generate each
  local date from that bounded in-memory context instead of issuing queries per
  day.
- Confirmed appointment overlap is the critical protected case and is explicitly confirmed as impossible for one booking resource.
- Solo-master MVP has one default booking resource per partner.
- Future studio support can add multiple booking resources under one partner, allowing parallel appointments for different masters/resources.
- Requested appointments may overlap and should not block public availability.
- Confirmation, auto-confirm booking, reschedule acceptance, manual appointment creation, and restore/reopen must share one conflict-checking service.
- The conflict-checking service must run inside a transaction when it can create or confirm an appointment.
- The transaction should serialize conflicting confirmation/create operations for the same booking resource with a PostgreSQL transaction-level advisory lock keyed by booking resource.
- Prefer advisory lock over locking the partner row because it makes this concurrency rule explicit and avoids coupling appointment booking to unrelated partner-row updates.
- Recommended lock key strategy: store an immutable unique integer `advisory_lock_key` on `BookingResource`.
- Recommended PostgreSQL helper: call `pg_advisory_xact_lock(namespace, advisory_lock_key)` inside `transaction.atomic`, where `namespace` is a fixed application constant for booking-resource locks and `advisory_lock_key` belongs to the target booking resource.
- This avoids hashing UUIDs into a single bigint, avoids theoretical hash collisions, and leaves room for future unrelated advisory-lock namespaces.
- The helper must use parameterized SQL and must fail clearly if called outside a transaction or on a non-PostgreSQL database.
- The service layer should return a clear business error when a slot is already occupied.
- Operations that require the booking-resource advisory lock are: auto-confirm booking, manual appointment creation, confirming a `requested` appointment, accepting a client/master proposal when the result becomes or remains `confirmed`, restoring/reopening a final appointment to `confirmed`, and admin edits to a confirmed appointment that change `start_at` / `end_at`.
- Operations that do not require this advisory lock are: creating a `requested` appointment, cancelling an appointment, marking `completed` / `no_show`, editing a private note, editing client-visible text without changing interval/duration, and creating availability or blocked time.
- Lock-protected operation order is: begin transaction, acquire booking-resource advisory lock, run fresh overlap query, save or return conflict, commit, then enqueue notifications/reminders after commit.
- Use a blocking transaction-level advisory lock for MVP, not try-lock. A competing operation for the same resource should wait briefly and then perform a fresh overlap check.
- Telegram/network I/O, sleeps, and retry loops must not happen inside the lock-protected transaction.
- Do not add heavy range/exclusion constraint complexity in MVP unless it stays clean in the final Django/PostgreSQL schema.
- If real usage exposes race-condition risk beyond the transactional lock/check approach, add PostgreSQL range/exclusion-style protection as the next hardening step.

Snapshot strategy:

- Appointment service item lines store service title, price, duration, currency, and override markers at booking time.
- Appointment totals are derived from service item snapshots plus applied penalty snapshots.
- Existing appointments remain understandable even if service/category is later inactive, renamed, repriced, or deleted/archived.

Deletion/archive strategy:

- Prefer active/inactive or archived states for business entities that appear in history.
- Avoid hard-deleting services/categories that have historical appointment references unless the final implementation has a clear archival/restrict policy.
- Cleanup jobs can hard-delete cleanup-eligible technical records after `retain_until`.

Audit strategy:

- Business changes should emit explicit audit events from service-layer operations.
- Avoid relying only on generic model save hooks for important audit events, because the audit reason and actor are business-context dependent.
- Audit event payloads must be structured enough for display, but must mask sensitive values.

Idempotency strategy:

- Scheduled reminders, delivery attempts, broadcast recipient delivery, and cleanup jobs need stable business keys or unique constraints that make retries safe.
- Bot callback actions that mutate state should re-check current state and be safe when clicked twice.
- User-facing "already handled" responses are preferable to duplicate mutations.

## Cross-Cutting Invariants

- Every partner-owned mutation must have partner context.
- Non-superuser reads and writes must be partner-scoped.
- Telegram identity is global; client profile is partner-scoped.
- Partner bot reachability/block state is partner-bot scoped.
- Confirmed appointments must not overlap for the same booking resource.
- Money is integer minor units plus explicit currency.
- Appointment service item snapshots are immutable by default after booking; later corrections are explicit and audited.
- Background job execution must be idempotent.
- Quiet hours must be respected for client and partner notifications.
- Cleanup jobs must not delete core business records by default.

## Candidate Implementation Order

This order is designed to reduce risk before building high-level UX polish.

1. Project foundation and shared core primitives.
2. Partner, admin access, tenant scoping, and setup-required state.
3. Bot instance, encrypted token validation, simple runner, and partner runtime middleware.
4. Telegram identity and partner-scoped client profiles.
5. Service categories and services.
6. Availability windows, blocked time, and basic slot generation.
7. Appointment creation, item snapshots, statuses, and confirmed-overlap protection.
8. Client booking bot flow with pagination.
9. Partner admin operational screens for services, calendar, appointments, and clients.
10. Reminder/background job foundation with idempotent delivery records.
11. Reschedule/cancellation flows and conflict handling.
12. Penalties and appointment outcomes.
13. Multilingual content overrides and placeholder validation.
14. Client-master messaging.
15. Broadcasts with pacing and recipient delivery stats.
16. Reports, expenses, audit, retention cleanup, and platform diagnostics.

## Tests Worth Prioritizing

Do not cover every trivial field at MVP start. Prioritize tests for logic that can break money, tenant isolation, time, or delivery reliability.

High-value test areas:

- Partner-scoped admin/query isolation.
- Runtime eligibility and invalid token handling.
- Telegram identity vs partner client profile separation.
- Appointment overlap prevention under concurrent confirmation.
- Slot generation with multi-service duration, blocked time, booking horizon, and near-fit tolerance.
- Appointment status transitions and reminder cancellation/staling.
- Penalty lifecycle: create, apply, return to unpaid, paid, waived.
- Money calculation in minor units.
- Reminder idempotency and retry/backoff terminal failures.
- Broadcast eligibility: marketing opt-out, blocked bot, skipped vs failed.
- Template placeholder validation and runtime fallback.
- Sensitive-data masking in logs/diagnostics.

## Decisions Still Needed Before Code

- Convert the approved `docs/spec/SCHEMA_DRAFT.md` data map into exact Django model/field names and migration constraints during implementation planning.
- Convert the approved `docs/spec/RUNBOOK_DRAFT.md` operational direction into exact Docker Compose service names, Django management commands, URLs, and host log commands during implementation.
- Confirm final bot framework/runtime library choices, with TableOS' Django + aiogram 3.x stack as the preferred reference unless implementation reveals a concrete blocker.
- Confirm exact default localized wording for the approved multilingual content registry.
- Confirm exact broadcast backoff intervals, global broadcast safety limits, worker thresholds, and alert thresholds.
- Confirm exact admin UI approach for calendar drawing, mobile calendar usage, conflict resolution, onboarding checklist, and appointment/proposal detail panels.
- Confirm implementation milestones and what can be cut if MVP scope gets too large.

## Approval Gate

Before implementation starts, this draft should be reviewed and converted into an approved technical specification.

Required explicit approval phrase before creating application code or project scaffolding:

`TECH_SPEC_APPROVED`
