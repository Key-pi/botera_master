# BoteraMaster Schema Draft

Status: draft data map, not Django models or migrations.
Last updated: 2026-10-02.

This document captures the intended data shape for implementation planning. It is allowed to change during development, but changes should preserve the product decisions already approved in discovery/spec docs.

## General Rules

- Production database target is PostgreSQL.
- Use UTC datetimes for stored timestamps and partner timezone only for display, scheduling rules, and local-time settings.
- Store money in minor units: `500.00 UAH` is stored as `50000`.
- Partner-owned business records carry `partner_id` directly unless there is a strong reason not to.
- Partner data isolation is a first-class invariant: a partner sees only records in their own area.
- Business history is preferred over destructive edits. Inactive/archived records stay visible in old appointments/history.
- Retention cleanup uses `retain_until` only for high-volume/low-value operational records, not core business history.
- Sensitive data must use shared masking/redaction helpers in logs and diagnostics.

## Partners, Accounts, Bot

### Partner

Represents the platform tenant. In user-facing client text this is usually "master"; internally and in platform/admin docs this is `partner`.

Key fields:
- `name`
- `status`: setup_required, active, suspended, archived
- `timezone`
- `default_language`: default `uk`, selectable `uk`, `ru`, `en`
- `created_at`, `updated_at`

Notes:
- `suspended` is platform-controlled.
- Partner can still access admin in limited mode when suspended to view/fix/pay.
- partner-level `paused` is not required in MVP; voluntary bot stop is represented by `BotInstance.status=paused`.

### Account

Admin/auth user account for platform and partner admin.

Key fields:
- auth identity fields
- `is_superuser` or platform role
- partner membership relation for owner access

MVP partner role:
- one partner role: owner/master.
- complex staff roles are post-MVP.

### BookingResource

The resource whose calendar cannot have overlapping confirmed appointments.

MVP:
- each solo partner has one default booking resource.
- it can be invisible in UI at first.

Future:
- studios/salons can add multiple resources/masters under one partner.

Key fields:
- `partner`
- `name`
- `type`
- `is_default`
- `is_active`
- `sort_order`
- `advisory_lock_key`

Important invariant:
- confirmed appointments must not overlap for the same booking resource.

Notes:
- `advisory_lock_key` is an internal immutable integer key used only for PostgreSQL advisory locks.
- it must be unique across booking resources.
- it should not be shown or edited in partner UI.

### BotInstance

Telegram bot owned by a partner.

Key fields:
- `partner`
- `token_encrypted`
- `token_encryption_key_id`
- `token_fingerprint`
- `token_masked`
- `telegram_bot_id`
- `telegram_username`
- `status`: active, paused, suspended, error
- `last_error_code`
- `last_error_message_masked`
- `last_validated_at`
- `last_polling_seen_at`

Rules:
- raw token is visible only at input/replacement time and runtime decrypt.
- after save, UI shows status and masked token only.
- `token_fingerprint` is a keyed non-reversible HMAC/fingerprint for duplicate-token detection.
- `token_encryption_key_id` identifies which configured encryption key encrypted the token, so future key rotation is possible.
- partner can switch active/paused.
- superuser controls suspended.
- invalid token blocks saving when possible; existing invalid runtime token moves bot to error.

### BotSettings

Partner-scoped behavior settings for the partner bot.

Key fields:
- `partner`
- `confirmation_mode`: auto/manual
- `slot_step_minutes`: allowed 5, 10, 15, 20, 30, 60; default 15
- `booking_horizon_days`: min 1, max 365; default 30
- `near_fit_tolerance_minutes`: min 0, max 30; default 10
- `cancellation_deadline_hours`: min 0, max 168; default 6
- `quiet_hours_enabled`
- `quiet_hours_start_local`: default 22:00
- `quiet_hours_end_local`: default 08:00
- `morning_digest_enabled`
- `morning_digest_time_local`: default 08:00
- `master_reminder_enabled`
- `master_reminder_offset_minutes`: min 5 minutes, max 7 days; default 45
- `stale_requested_base_interval_minutes`: min 10 minutes, max 72 hours; default 180
- `penalties_enabled`: default false
- `late_cancel_penalty_amount`: default 5000 UAH
- `no_show_penalty_amount`: default 5000 UAH
- `late_arrival_threshold_minutes`: default 15
- `late_arrival_penalty_amount`: default 2000 UAH

Notes:
- schedule-specific settings are implemented in `PartnerScheduleSettings` under the `schedule` module. A future Bot settings admin UI may expose these fields, but the schedule domain owns their persistence and validation.
- client reminder offsets should be separate rules, with default 24h and 2h before appointment.
- max 5 client reminder rules per partner.
- invalid saved settings use safe defaults plus warnings when possible.

### PartnerTelegramContact

Partner-scoped Telegram recipient for master-facing delivery.

Key fields:
- `partner`
- `telegram_identity`
- `user`: optional linked admin account
- `role`: owner/master/admin recipient, initially owner/master
- `is_primary`
- `is_active`
- `linked_at`
- `last_delivery_failure_code`
- `last_delivery_failure_at`

Rules:
- bot token validation identifies the bot only, not the master's personal Telegram account.
- contact binding is created through a signed deep link opened in the partner's own bot.
- a normal client `/start` or arbitrary callback payload must not create or grant a master contact binding.
- MVP UI may expose one primary owner/master contact, but the schema should not prevent extra recipients later.
- master-facing notifications, stale requested reminders, morning digest, failed-delivery notices, and client-master messages target this contact.

### SystemTemplate

Platform-defined localized bot/admin text template.

Key fields:
- `key`
- `language`: uk, ru, en
- `body`
- `editable`: whether partner override is allowed
- `placeholder_allowlist`
- `sample_context`

Rules:
- every runtime key must have system defaults for uk, ru, en.
- Ukrainian is emergency fallback.
- common navigation and system/service errors are system-only localized.

### PartnerTemplateOverride

Partner-provided localized override for editable templates.

Key fields:
- `partner`
- `key`
- `language`
- `body`
- `is_active`

Rules:
- empty/missing override falls back to system default for that language.
- changing uk does not affect ru/en.
- validation blocks unknown placeholders and render errors.
- admin should support "reset to default" per key/language.

## Clients

### TelegramIdentity

Global technical identity for one Telegram user across BoteraMaster partner bots.

Key fields:
- `telegram_user_id`
- `username`
- `first_name`
- `last_name`
- `is_premium`
- `language_code`
- `last_seen_at`

Notes:
- this is not partner-specific client data.
- the same Telegram user can be a client of many partners.
- bot blocking/reachability is not stored here. It is partner-bot scoped and belongs on `ClientProfile`.

### ClientProfile

Partner-scoped profile of a person as a client of one partner.

Key fields:
- `partner`
- `telegram_identity`, nullable for manual clients
- `name`
- `phone`
- `language`
- `private_master_note`
- `extra_client_text`
- `marketing_opt_out`: default false
- `bot_unreachable`: default false
- `last_delivery_failure_code`
- `last_delivery_failure_at`
- `created_at`, `updated_at`

Rules:
- one Telegram identity may have separate ClientProfile records under different partners.
- phone/name/language/notes/history/penalties are partner-scoped and must not mix.
- marketing opt-out blocks marketing broadcasts only, not service notifications or manual master messages.
- `bot_unreachable` is set only for terminal Telegram delivery errors for this partner bot.
- when the client writes to the partner bot again, `bot_unreachable` resets to false.
- manual clients without Telegram identity show "no Telegram connection", not "bot blocked".

Suggested constraints:
- unique `partner + telegram_identity` where telegram_identity is not null.
- indexes on `partner`, `phone`, `telegram_identity`.

## Catalog

### ServiceCategory

Partner-created category for services.

Key fields:
- `partner`
- `name`
- `description`
- `sort_order`
- `is_active`

Rules:
- partner manually sorts categories.
- inactive category hides its services from booking flow, but old appointment history remains visible.

### Service

Partner-created service. There is no platform-provided service catalog.

Key fields:
- `partner`
- `category`
- `name`
- `description`
- `price_amount`
- `currency`: MVP UAH
- `duration_minutes`
- `sort_order`
- `is_active`

Rules:
- price may be `0.00`.
- inactive service is hidden from new client booking, but old appointments remain visible.
- partner manually sorts services.

## Schedule

### PartnerScheduleSettings

Partner-owned schedule behavior settings.

Implemented fields:
- `partner`
- `confirmation_mode`: blank/setup-required, auto, manual
- `slot_step_minutes`: allowed 5, 10, 15, 20, 30, 60; default 15
- `booking_horizon_days`: min 1, max 365; default 30
- `near_fit_tolerance_minutes`: min 0, max 30; default 10

Rules:
- `confirmation_mode` completes the booking confirmation setup step only when set to auto or manual.
- schedule settings may be shown in a Bot settings UI later, but persistence and validation belong to the schedule module.

### AvailabilityWindow

Concrete available time interval for a booking resource.

Key fields:
- `partner`
- `booking_resource`
- `start_at`
- `end_at`
- `source_template`
- `created_by`

Rules:
- clients see slots only inside concrete availability windows.
- templates do not create bookable slots until applied.
- overlapping or adjacent availability windows are allowed; slot generation/calendar display can merge them logically.
- normal partner UI cannot create new windows in the past.
- one availability window max length: 24 hours.

Suggested constraints/indexes:
- check `start_at < end_at`.
- index `partner + start_at`.
- index `partner + end_at`.
- index `booking_resource + start_at`.

### BlockedTime

Concrete unavailable interval for a booking resource.

Key fields:
- `partner`
- `booking_resource`
- `start_at`
- `end_at`
- `reason`, optional
- `created_by`

Rules:
- can exist inside or outside availability.
- blocks generated client slots.
- normal partner UI cannot create new blocked time in the past.
- one blocked interval max length: 31 days.

Suggested constraints/indexes:
- check `start_at < end_at`.
- index `partner + start_at`.
- index `partner + end_at`.
- index `booking_resource + start_at`.

### AvailabilityTemplate

Reusable partner-defined template for applying availability windows.

Key fields:
- `partner`
- `name`
- `is_active`
- `sort_order`

### AvailabilityTemplateWindow

Weekly rule inside a template.

Key fields:
- `template`
- `weekday`
- `start_time`
- `end_time`

Rules:
- MVP template rules are weekly only, not date-specific.
- check `start_time < end_time`.
- no overnight rules in MVP. Overnight availability must be split into two rules.

## Appointments

### Appointment

Business record for a client visit/request.

Key fields:
- `partner`
- `booking_resource`
- `client_profile`
- `start_at`
- `end_at`
- `status`: requested, confirmed, cancelled_by_client, cancelled_by_master, completed, no_show
- `source`: client_bot, partner_admin/manual, proposal
- `confirmation_mode_snapshot`
- `is_near_fit`
- `client_comment`
- `private_master_note`
- `client_visible_reason`
- `service_total_amount`
- `penalties_total_amount`
- `total_amount`
- `currency`: MVP UAH
- `created_by`
- status timestamps as needed

Rules:
- requested records do not block a booking resource.
- confirmed records block the booking resource.
- final statuses do not block slots.
- service price and duration are snapshotted at booking time.
- changing future confirmed time/services/price/duration requires client confirmation unless client has no Telegram connection.
- completed/no_show/cancelled can be corrected by master with required correction reason.
- old reminders are not automatically restored by correction unless explicitly rescheduled by service logic.

Concurrency invariant:
- no overlapping confirmed appointments for the same booking resource.
- MVP protection uses transactional service check plus PostgreSQL advisory lock keyed by booking resource.
- no Telegram/network I/O inside the lock transaction.

Suggested indexes:
- `partner + status + start_at`
- `booking_resource + status + start_at`
- `client_profile + start_at`

### AppointmentServiceItem

Snapshotted service line inside an appointment. Used even when the appointment has one service.

Key fields:
- `appointment`
- `service`, nullable for one-off/manual service
- `category_name_snapshot`
- `service_name_snapshot`
- `description_snapshot`
- `price_amount`
- `currency`
- `duration_minutes`
- `is_custom`
- `is_overridden`
- `sort_order`

Rules:
- manual one-off services do not automatically enter partner catalog.
- manual price/duration override is visible to client as changed by master.
- service order is not business-critical, but `sort_order` keeps stable display.

### AppointmentProposal

Unified proposal entity for reschedule/alternative/change flows.

Types:
- `reschedule_confirmed`
- `requested_alternatives`
- `appointment_change`

Statuses:
- `pending`
- `accepted`
- `declined`
- `expired`
- `cancelled`
- `superseded`

Key fields:
- `partner`
- `appointment`
- `type`
- `status`
- `created_by`
- `expires_at`: default 48 hours for relevant flows
- `message`
- `reason`
- `responded_at`
- `selected_option`

Rules:
- if a new pending proposal is created for the same appointment, older pending proposal becomes superseded.
- if proposal expires, both sides are notified when relevant.
- requested alternatives expire by automatically cancelling the requested appointment.

### AppointmentProposalOption

One selectable option inside a proposal.

Key fields:
- `proposal`
- `start_at`
- `end_at`
- `service_total_amount`
- `penalties_total_amount`
- `total_amount`
- `currency`
- `is_near_fit`: whether this option extends past availability within configured tolerance
- `sort_order`
- `selected_at`

Rules:
- time-only proposals may omit option item rows and reuse current appointment service items conceptually.
- if option changes services/price/duration, store proposed item snapshots in `AppointmentProposalOptionItem`.

Suggested constraints:
- check `start_at < end_at` when start/end are present.

### AppointmentProposalOptionItem

Snapshotted proposed service line for proposal options that change services/price/duration.

Key fields:
- `option`
- `service`, nullable
- `category_name_snapshot`
- `service_name_snapshot`
- `description_snapshot`
- `price_amount`
- `currency`
- `duration_minutes`
- `is_custom`
- `is_overridden`
- `sort_order`

## Penalties and Finance

### Penalty

Partner-scoped penalty attached to a client.

Reasons:
- late_cancel
- no_show
- late_arrival

Statuses:
- unpaid
- applied_to_appointment
- paid
- waived

Key fields:
- `partner`
- `client_profile`
- `source_appointment`
- `reason`
- `status`
- `amount`
- `currency`: MVP UAH
- `note`
- `created_by`
- `paid_at`
- `waived_at`

Rules:
- partner may waive/forgive manually.
- penalties are shown separately in reports as one line group.
- client can see active unpaid penalties if penalties are enabled.
- client can write master about penalties through generic contact master flow.

### PenaltyApplication

Historical link showing that a penalty amount was applied to an appointment total.

Key fields:
- `penalty`
- `appointment`
- `amount_snapshot`
- `currency`
- `status`: active, released, settled
- `created_at`
- `released_at`
- `settled_at`

Rules:
- master chooses which unpaid penalties to apply to an appointment.
- applying penalty increases appointment total.
- if the appointment is cancelled before payment/settlement, the application is released and the penalty returns to unpaid.
- marking penalty paid is a separate master action.
- when the applied penalty is paid through the appointment, the application becomes settled.

### Expense

Manual partner expense for finance reports.

Key fields:
- `partner`
- `date`
- `amount`
- `currency`: MVP UAH
- `description`
- `created_by`

Rules:
- no expense categories in MVP.
- visible only to partner/admin, never to client.

## Notifications and Delivery

### ReminderRule

Partner-configured reminder offset.

Key fields:
- `partner`
- `target`: client/master
- `offset_minutes`
- `is_active`

Rules:
- client reminders default to 24h and 2h.
- master reminder default is 45 minutes and can be disabled.
- client reminders are created only for confirmed appointments.

### ScheduledNotification

Durable record for service notification/reminder jobs.

Key fields:
- `partner`
- `appointment`
- `client_profile`
- `type`
- `due_at`
- `status`: queued, sent, failed, skipped, cancelled, stale
- `idempotency_key`
- `attempt_count`
- `last_failure_code`
- `last_failure_message_masked`
- `retain_until`

Rules:
- if appointment is moved/cancelled/completed/no_show, old pending reminders become cancelled/stale.
- if quiet hours delay makes a reminder useless because appointment already passed, skip it.
- failed reminder becomes visible to master only after configured retry exhaustion.
- manual retry is allowed from master UI/bot actions.

### DeliveryAttempt

Low-level delivery attempt record for diagnostics/statistics.

Key fields:
- `partner`
- `notification`
- `broadcast_recipient`
- `message`
- `channel`: telegram
- `status`: sent, failed, skipped
- `failure_code`
- `failure_message_masked`
- `attempted_at`
- `retain_until`

Rules:
- raw Telegram payload must not be stored without filtering/masking.
- default operational retention is 3 months unless a stricter group default is chosen.
- exactly one delivery source should be set for a row: notification, broadcast recipient, or conversation message.

## Broadcasts

### BroadcastCampaign

Partner-created marketing broadcast.

Key fields:
- `partner`
- `status`: draft, scheduled, queued, sending, completed, cancelled, failed_partial
- `text`
- `photo`
- `buttons`
- `filters`
- `scheduled_at`
- counters: queued/sent/failed/skipped
- `created_by`
- `started_at`
- `finished_at`

Rules:
- recipients are only clients who have not opted out of marketing and are not currently unreachable.
- can send now or later.
- delivery is paced per bot, no faster than about 1 message / 0.5 seconds / bot.
- Telegram 429 produces backoff, not full campaign failure.

### BroadcastRecipient

Per-client delivery state for one broadcast.

Key fields:
- `campaign`
- `client_profile`
- `status`: queued, sent, failed, skipped
- `next_attempt_at`
- `sent_at`
- `failure_code`
- `failure_message_masked`
- `retain_until`

Rules:
- if client opts out or blocks bot after campaign creation but before delivery, mark skipped.
- failed recipient manual retry is post-MVP.
- retain recipient records for a limited period, default 3 months unless adjusted.

### BroadcastTemplate

Reusable partner-authored broadcast draft.

Key fields:
- `partner`
- `name`
- `text`
- `photo`
- `buttons`
- `created_by`

Rules:
- partner writes templates manually.
- templates are optional convenience, not system content defaults.

## Messaging

### ConversationMessage

Client-master message routed through the partner bot.

Key fields:
- `partner`
- `client_profile`
- `direction`: client_to_master, master_to_client
- `text`
- `delivery_status`
- `failure_code`
- `created_at`
- `delivered_at`

Rules:
- MVP messages are not tied to a specific appointment.
- master replies through bot; client receives from master bot.
- if client blocked bot, master sees delivery failed and client becomes unreachable for this partner bot.
- raw message text is business data but must not be written to technical logs.

## Audit, Diagnostics, Health

### AuditEvent

Business-friendly activity event.

Key fields:
- `partner`, nullable for platform-wide/system event
- `actor`
- `event_type`
- `target_type`
- `target_id`
- `message`
- `payload_masked`
- `reason`
- `retain_until`
- `created_at`

Rules:
- partner-visible audit includes only partner-area events.
- superuser can see platform-wide audit/system logs.
- partner-visible audit retention: 6 months.
- technical sensitive fields must be masked/redacted.
- required reason in MVP: correction after final appointment status.

Events to cover in MVP:
- appointment create/change/cancel/status changes
- penalties
- expenses
- broadcasts
- reminders/delivery failures
- bot token/settings changes
- correction after final status

### PlatformDiagnostic

Technical/system diagnostic event for superuser/developer.

Key fields:
- `component`
- `severity`
- `code`
- `message_masked`
- `metadata_masked`
- `retain_until`
- `created_at`

Rules:
- includes bot runner/background job errors.
- raw token, phone, private notes, message text, raw Telegram payloads must not be stored unmasked.
- default retention: 3 months.

### WorkerHeartbeat

Current health state for operational components.

Key fields:
- `component`
- `queue`
- `status`: ok, warning, critical
- `last_seen_at`
- `last_success_at`
- `last_error_at`
- `last_error_code`
- `metadata_masked`

Rules:
- this is current state, not a high-volume log.
- historical noisy records should go to diagnostics with retention.

## Retention Defaults

Core business records are retained while partner data is retained:
- partners
- accounts
- clients
- appointments
- appointment service items
- penalties
- penalty applications
- expenses
- client-master messages, unless later product/privacy rules change this

Cleanup-eligible operational records:
- delivery attempts: default 3 months
- broadcast recipients: default 3 months
- platform diagnostics/system logs: default 3 months
- partner-visible audit events: 6 months

Use `retain_until` on cleanup-eligible models so maintenance jobs can delete expired rows safely.

## Constraint and Index Summary

Hard checks:
- time intervals use `start_at < end_at`.
- template windows use `start_time < end_time`.
- no overnight template windows in MVP.
- money amounts should not be negative unless a later explicit feature requires credits/refunds.

Uniqueness:
- `TelegramIdentity.telegram_user_id` unique.
- `ClientProfile(partner, telegram_identity)` unique when telegram_identity is not null.

Performance-oriented indexes:
- partner-owned tables: `partner_id`.
- time-range tables: `partner_id + start_at`, `partner_id + end_at`.
- resource time-range tables: `booking_resource_id + start_at`.
- appointments: `partner_id + status + start_at`, `booking_resource_id + status + start_at`, `client_profile_id + start_at`.
- broadcasts: `campaign + status`, `partner + status`.
- notifications: `status + due_at`, `partner + status + due_at`.
- audit/diagnostics: `partner + created_at`, `component + created_at`, `retain_until`.

Confirmed overlap protection:
- implementation uses service-level transactional check plus PostgreSQL transaction-level advisory lock keyed by booking resource.
- recommended helper uses PostgreSQL two-key advisory locks: a fixed application namespace plus immutable `BookingResource.advisory_lock_key`.
- do not perform network I/O inside the transaction.

## Still To Finalize During Implementation Planning

- Exact Django model/field names.
- Exact migration-level checks and partial unique constraints.
- Exact enum/class names and metadata schemas for delivery failures and diagnostics.
- Exact command names/URLs for the approved operational runbook.
- Exact localized default copy for uk/ru/en.
