# BoteraMaster Domain Model

Status: finalized MVP domain model on 2026-08-20, not final database schema.

Last reviewed: 2026-08-20.

This document describes MVP domain concepts, responsibilities, lifecycles, states, and business invariants. It does not define database tables, Django models, migrations, or final architecture.

## Core Concepts

### Partner

The tenant boundary for MVP. In MVP, partner equals solo master.

Owns:

- Bot configuration.
- Services.
- Schedule.
- Appointments.
- Clients.
- Broadcasts.
- Audit events.
- Partner-scoped settings.

Most partner-owned domain records should carry an explicit partner scope. TableOS uses a useful reference pattern with partner-bound models and partner-scoped admin filtering; BoteraMaster should preserve that idea conceptually without copying unrelated TableOS staff complexity.

Future studios may require expanding this concept without breaking the MVP language.

Partner admin workspace:

- MVP partner admin main navigation should include Dashboard, Calendar, Appointments, Clients, Catalog, Broadcasts, Finance, Bot, and Activity Log.
- The default partner admin screen should act as an operational dashboard for a solo master.
- It should prioritize today's and tomorrow's calendar/appointments, pending `requested` appointments, failed reminders/notifications, active conflicts or problems, and compact basic stats/revenue.
- Calendar should be the primary day-to-day working screen for the master.
- Appointments list is a secondary searchable/filterable operational view.
- Appointment filters in MVP should remain operationally useful and limited: date/period, status, client search, service/category, penalty presence/unpaid penalty, and requested/near-fit markers.
- Partner Activity Log should exist as a standalone screen, while appointment and client cards may show short related local history.
- Superuser admin can remain Django-admin-like/pragmatic in MVP, with dedicated operational views/filters for health, setup-required partners, and problems/incidents.
- Income reporting in MVP should support simple day/week/month views with service revenue, penalties as one separate combined line, and total.
- Reporting should distinguish actual revenue from `completed` appointments and expected revenue from future `confirmed` appointments.
- Actual revenue is calculated from `completed` appointments using the current appointment state, including paid/settled penalties applied to those appointments.
- Expected revenue is calculated from future `confirmed` appointments and includes only penalties already applied to those appointments.
- If a `completed` appointment is corrected and its amount changes, reports recalculate from the appointment's current corrected state.
- Reporting periods use the partner's timezone.
- MVP should support simple partner-scoped expense records with amount, date, and description/reason.
- Expense categories are not required in MVP; description/title search can cover the first simple use cases.
- Expenses are partner-only data and are not client-visible.
- Client card in partner admin should show client data, future appointments, appointment history, unpaid penalties, visit count, and total amount spent with the partner.

Partner onboarding:

- MVP partner onboarding is controlled by superuser.
- Public/self-service partner registration is not part of MVP.
- Superuser creates the partner and partner admin account, then gives the partner login/password.
- Partner must complete minimum setup before the bot is considered ready for clients.
- Minimum setup before launch: bot token, language, timezone, at least one service category/service, at least one availability window, and booking confirmation mode.
- Other partner settings may start from defaults.
- Partner/setup state should support draft/setup-required so the bot is not runnable for clients before required setup is complete.
- Partner admin should expose an onboarding checklist/setup guide for the minimum launch steps.
- While setup is incomplete, booking/profile flows are unavailable to clients; the bot may show a short system-only "being configured" message.
- After the minimum checklist is complete, the partner can launch the bot without final superuser approval.
- Superuser should be able to see setup-required partners and the missing setup steps.
- If an active partner later has no services or availability windows, this creates client-facing empty states rather than automatically deactivating the bot.

### Bot Instance

Partner-owned Telegram bot operated by BoteraMaster.

Key requirements:

- One primary bot per partner in MVP.
- Token is sensitive.
- Full token is not shown after save.
- Bot has an operational status so the platform can understand whether it is active, paused/stopped, suspended, or in error.
- Partner suspension stops bot while preserving data.
    
Approved MVP status vocabulary:

- `active`
- `paused`
- `suspended`
- `error`

Meaning:

- `active` means the bot should be running normally.
- `paused` means the bot was voluntarily/manually stopped.
- `suspended` means the platform stopped the bot because of payment, administrative, or policy reasons.
- `error` means the bot cannot run because of invalid token, Telegram error, or another runtime/configuration problem.

Runtime eligibility:

- Bot status allows running.
- Partner is active.
- Token is valid and decryptable.
- Bot is configured for polling.
- Token is not duplicated across running bots.

Runtime reload:

- MVP uses a simple TableOS-like runner that loads eligible active bots at startup.
- Token/status changes require runner restart/redeploy to affect live polling in MVP.
- Hot reload or periodic config reload is a future improvement, not MVP.

Health/status visibility:

- Partner should see bot health/status in admin.
- Visible health data should include operational status, last startup/runtime error when present, and last successful polling/activity timestamp when available.
- Full token is never shown as part of health/status.
- Partner can switch their own bot between `active` and `paused`.
- `suspended` is controlled only by superuser/platform admin.
- When status is `error`, partner can trigger a connection/configuration check from admin to revalidate the token/configuration and see the current error.
- In MVP, because runtime does not hot-reload, a successful check may still require runner restart/redeploy before live polling actually changes.

### Bot Settings

Partner-owned behavior and content settings for the partner bot.

Purpose:

- Keep technical bot connection/runtime state separate from configurable partner behavior.
- Let the partner control their own bot experience without managing infrastructure or server/runtime details.

Scope:

- Languages.
- Bot texts and button labels.
- Auto-confirm/manual-confirm booking mode.
- Reminder timings and notification behavior.
- Quiet hours.
- Booking rules and required client fields.
- Cancellation rules.
- Penalties.

Rules:

- Bot settings are managed in admin only in MVP, not through the Telegram bot.
- Admin UX should expose one main `Bot` section with tabs: Instance, Settings, Content, Notifications, Booking, and Penalties.
- Critical settings require warning/confirmation before saving.
- Examples of critical settings include bot token, auto-confirm/manual-confirm mode, cancellation deadline, penalties, reminder timings, quiet hours, and required booking fields.
- Critical settings use a normal confirmation modal in MVP; typed confirmations are not required.
- Settings screens may show short local change history for convenience.
- The complete settings history belongs to the partner-visible activity/audit log.

### Telegram Identity

Shared technical representation of a Telegram user known to BoteraMaster.

The same Telegram user may interact with multiple partner/master bots. Their global Telegram identity can be shared technically, but their data as a client of each partner must remain separate.

Domain-level Telegram identity attributes to preserve:

- Telegram user id.
- Username, if available.
- First/last/display name from Telegram, if available.
- Telegram Premium status, if available.
- Language hint from Telegram, if available.
- Last seen/updated timestamp.

Do not treat bot blocking as a global Telegram identity field. Blocking is scoped to a specific partner bot: the same Telegram user may block one master's bot and still use another master's bot.

The TableOS shared Telegram account plus partner-specific guest profile pattern is a relevant reference, but BoteraMaster should keep partner-bot reachability/blocking scoped to the specific partner bot.

### Master Telegram Contact

Partner-scoped Telegram contact used for master-facing delivery.

Purpose:

- Give the system a real Telegram chat to send master reminders, stale requested reminders, morning digests, client messages, and failed-delivery notices.
- Keep the partner/admin web account separate from Telegram chat identity.
- Avoid treating the bot token as proof of who owns the bot.

MVP rules:

- The master contact is linked through a signed deep link opened in the partner's own bot.
- A normal client `/start` does not create a master contact binding.
- A manually submitted Telegram user id or chat id is not trusted by itself.
- MVP can start with one primary owner/master contact per partner.
- The model should allow additional active recipients later for salons, assistants, or alternate Telegram accounts.

### Client Profile

Partner-scoped representation of a person as a client of one specific partner/master.

Purpose:

- Keep one client's relationship with one master separate from the same person's relationship with another master.
- Store partner-specific client data, master notes, visits, penalties, and booking requirements.
- Prevent client history/notes/phone/settings from leaking across partner boundaries.
- Support both Telegram-linked clients and manually created clients without Telegram identity.

Identity rule:

- Telegram identity is optional for a client profile.
- One Telegram identity can have multiple partner-scoped client profiles.
- A manual client profile may start with only name and/or phone.
- Each partner profile keeps its own name, phone, language, private notes, appointment history, penalties, marketing opt-out, and partner-bot reachability state.
- If a Telegram user later provides a phone matching a manual client profile for the same partner, the system may suggest linking/merging to the master.
- Linking/merging manual client data with a Telegram identity requires master confirmation; do not auto-merge silently.
- If linked, previous manual appointments and visit history become part of that partner-scoped client profile.
- Manual client profiles without phone are not auto-linked later.

Default fields:

- Partner.
- Telegram identity, optional.
- Name.
- Phone.
- Language.
- Private master note.
- Additional free-text field.

Language rule:

- Partner/client language defaults to Ukrainian if not explicitly chosen.
- System text is rendered in the user's selected language.
- Partner-authored content remains as written and is not automatically translated.
- System defaults are conceptually stored by content key and language.
- Partner template overrides are language-specific and should only apply to users with the same language. Missing overrides fall back to system defaults for the user's language.
- If a system default for the requested language is unexpectedly missing, Ukrainian is the fallback language.
- Empty partner overrides mean "use system default".
- Initial editable content groups are start/welcome, booking, reschedule/cancellation, reminders, client profile, penalties/no-show/late-arrival, client-master messaging, and broadcast buttons.
- Common navigation buttons and system error/service texts are system-only localized in MVP.
- Initial editable `start/welcome` and `booking` keys are confirmed as the first content-key group.
- `booking.slot_taken` and "no available slots" remain system-only localized service texts in MVP.
- Ordinary client booking summary/final confirmation does not show penalties unless penalties have already been explicitly applied to that appointment.
- Initial editable cancellation/reschedule keys are confirmed as the second content-key group.
- `appointment.late_cancel_blocked` remains system-only localized in MVP.
- Master notifications for cancellation/reschedule events are partner-editable in MVP.
- Cancellation/reschedule reason text is optional and templates must handle an empty reason.
- Initial editable reminders keys are confirmed as the third content-key group.
- Repeated stale requested reminders reuse one `reminder.stale_requested` template.
- Manual retry sent/failed reminder texts remain system-only localized in MVP.
- Reminder failure reasons shown to partners must be business-friendly and must not expose raw technical payloads.
- Initial editable client profile keys are confirmed as the fourth content-key group.
- Profile updated success, marketing subscribe/unsubscribe confirmations, and contact-master sent confirmation remain system-only localized in MVP.
- Profile appointment details must be able to show penalties and total when penalties are applied to that appointment.
- Profile detail comment/reason are optional and templates must handle empty values.
- Initial editable penalty/no-show/late-arrival keys are confirmed as the fifth content-key group.
- Master-facing penalty notifications are partner-editable in MVP.
- Internal penalty status labels remain system-only localized.
- Penalty templates use a localized business label for penalty reason instead of exposing raw enum values.
- Initial editable client-master messaging keys are confirmed as the sixth content-key group.
- Client confirmation after sending a message and messaging delivery failure texts remain system-only localized in MVP.
- Messaging failure reasons shown to users must be business-friendly and must not expose raw Telegram payloads.
- Initial editable broadcast button keys are confirmed as the seventh content-key group.
- Broadcast message bodies are partner-authored manually or via partner-saved templates, not system-provided marketing defaults.
- Broadcast unsubscribe confirmation and broadcast service/status labels remain system-only localized in MVP.
- The initial MVP multilingual content registry is approved at the key, editable/system-only, placeholder allowlist, and sample validation context level.
- Every runtime content key must have `uk`, `ru`, and `en` system defaults; Ukrainian is the emergency fallback language.
- New content keys can be added later when they define system defaults, placeholder allowlist, sample validation context, and editable/system-only classification.
- Exact default localized wording is a seed/copywriting task, not a reopened registry decision.

Derived values:

- Visit count should be calculated from appointments/visits rather than stored as a basic mutable field.

Lightweight partner/bot-scoped communication flags are stored on the partner-scoped `ClientProfile` in MVP:

- `marketing_opt_out`, default `false`.
- `bot_unreachable`, meaning this client is currently unreachable through this partner bot.
- `last_delivery_failure_code`, using a business-friendly failure code rather than a raw Telegram payload.
- `last_delivery_failure_at`.

Rules:

- Marketing opt-out blocks partner marketing broadcasts only.
- Service notifications and manual master messages remain separate from marketing opt-out.
- `bot_unreachable` is set when Telegram returns a terminal delivery error such as blocked/deactivated/chat-not-found.
- Raw Telegram failure payloads are not stored on the client profile. Detailed attempts/diagnostics live in delivery attempt or platform diagnostic records with masking and retention.
- If the client later sends a message/start/update to this partner bot, `bot_unreachable` should be reset because the channel is reachable again.
- Manual clients without Telegram identity are shown as "no Telegram connection" rather than "bot unreachable".

### Client Communication State

Rejected/deferred as a separate MVP entity for now.

The behavior still exists, because broadcasts require opt-out and Telegram can report blocked/unreachable users. For MVP modeling, keep these as lightweight `ClientProfile` fields and revisit a separate communication-state model only if the logic grows.

### Service

Partner-owned bookable service.

BoteraMaster does not provide a shared/global service catalog for masters. Each partner defines their own services, categories, descriptions, prices, durations, active state, and display order.

Important fields:

- Name.
- Category.
- Optional short description.
- Price.
- Duration.
- Sort/display order.
- Active/inactive state.

Existing appointments snapshot service price and duration.

Price `0.00` is allowed, for example for a free consultation.

Inactive services are hidden from client booking flow, but existing appointments/history that reference them remain visible.

Client-facing service lists in the bot must be paginated when they grow large.

Deferred service fields:

- Photo.
- Buffer/pause after service.
- Calendar color.

These can be added later if partner workflows require them.

### Service Category

Partner-owned category used to group services in the client booking flow.

Important fields:

- Name.
- Optional description.
- Sort/display order.
- Active/inactive state.

Rules:

- Categories are manually sorted by the partner.
- Client selects a category before selecting services inside it.
- Inactive categories are hidden from client booking flow together with their services.
- Existing appointments and history remain visible even if the category later becomes inactive.

### Schedule / Availability

Partner-owned definition of when the partner can accept appointments.

Includes:

- Availability windows.
- Availability templates.
- Days off / unavailable dates.
- Blocked time.
- Timezone.
- Configurable slot step.
- Configurable booking horizon.

Important product nuance:

- Some MVP partners may not have a stable weekly work schedule because beauty services can be a second job.
- For those partners, the calendar may be driven more by manually opened free days/hours than by a classic weekly schedule.
- The primary MVP workflow should allow the partner to open a calendar and draw available windows directly.
- Main calendar view defaults to week view and supports day/week/month switching.
- Availability windows can be created by selecting a time range or through quick manual input: date, start time, end time.
- Calendar range selection should also support creating blocked time and manual appointments.
- When selecting a range, partner chooses whether the new item is an availability window, blocked time, or manual appointment.
- Availability windows, blocked time, appointments, conflicts, and near-fit/request markers should be visually distinguishable by type in the calendar.
- Clicking an appointment should open a detail panel/modal with key actions: edit, propose reschedule, cancel, mark completed, mark no-show, and message client.
- Calendar should support quick copy actions such as copy day and copy week in addition to reusable templates.
- Calendar admin should be usable on mobile with adapted interactions, while dense editing may remain more comfortable on desktop/tablet in MVP.
- Clients can see bookable slots only inside partner-created availability windows.
- Partners should be able to save multiple availability templates and reuse/apply them later.
- Templates can be applied to future weeks or date ranges, with conflict preview before applying.
- A repeatable weekly schedule can be represented as a template/preset, not as the only source of availability.
- Partner can block/unavailable time inside available windows. Blocked time reason text is optional.
- Real client bookable slots are calculated only from concrete `AvailabilityWindow` intervals.
- Availability templates do not create client-visible slots by themselves; applying a template creates concrete availability windows for the selected period.
- Availability windows are stored as concrete `start_at` / `end_at` intervals in UTC.
- Overlapping or adjacent availability windows are allowed. Slot generation and calendar display may merge them logically so the partner is not punished for drawing/copying overlapping windows.
- Blocked time is stored as a concrete `start_at` / `end_at` interval in UTC with optional reason.
- Blocked time may exist inside or outside an availability window. It only removes bookable time where it intersects availability, but can still be shown in the partner calendar for planning.
- Time-range records must have `start_at < end_at`.
- Partners should not create new availability windows or blocked time in the past through normal admin UI/service flows, while historical records remain stored.
- A single availability window should be limited to 24 hours.
- A single blocked-time interval should be limited to 31 days, so vacations/long unavailability are possible without allowing accidental year-long blocks.
- Schedule UX still requires detailed design. It should support both reusable templates and irregular manually opened availability without making either path painful.

Configurable slot step:

- Partner should be able to configure the booking slot step.
- Client chooses a start time from available slots generated with that step.
- The total duration of selected service(s) determines the occupied interval.
- Allowed MVP slot step values are 5, 10, 15, 20, 30, and 60 minutes.
- Default slot step is 15 minutes.

Configurable booking horizon:

- Partner should be able to configure how far ahead clients can book.
- Long booking horizons should be possible if a partner wants clients to book far in advance.
- Booking horizon minimum is 1 day.
- Booking horizon maximum is 365 days.
- Default booking horizon is 30 days.

Availability calculation must account for the total selected service duration, availability windows, blocked time, and existing appointments.

### Booking Resource

Future-aware owner of a bookable schedule and confirmed-overlap rule.

MVP direction:

- Solo-master MVP has one default booking resource per partner.
- In MVP, this default booking resource represents the partner/master.
- Availability windows, blocked time, and appointments belong conceptually to a booking resource, even if the first implementation can derive the single default resource from the partner.
- Confirmed appointment overlap is prevented per booking resource, not globally per partner.
- This keeps the MVP compatible with future studios, where one partner may have multiple masters/resources and appointments for different resources may overlap.
- The exact persisted shape of `BookingResource` can stay implementation-design detail for MVP, but the concurrency and availability model should use this concept.

Near-fit tolerance:

- Near-fit tolerance is partner-configurable and included in MVP.
- Default near-fit tolerance is 10 minutes.
- Near-fit tolerance minimum is 0 minutes.
- Near-fit tolerance maximum is 30 minutes.
- It applies only when the appointment exceeds the end of an availability window by the allowed tolerance.
- It does not allow overlap with blocked time or another `confirmed` appointment in MVP.
- Near-fit slots are shown to the client with a marker that they require master confirmation.
- Near-fit bookings always start as `requested`, even if the partner uses auto-confirmation.

### Availability Template

Reusable partner-defined pattern for opening availability in the calendar.

Templates are not directly bookable. They are reusable presets that create concrete availability windows when applied to future weeks/date ranges.

Recommended MVP structure:

- Template name.
- Template window rows with weekday, start time, and end time.
- Optional metadata for display/order later if needed.

Date-specific template windows are not required in MVP.

Template window validation:

- Template window rows must have `start_time < end_time`.
- Overnight template windows are not supported in MVP.
- If a partner needs overnight availability, it should be represented as two template windows split across days.

Possible examples:

- Regular weekly pattern.
- A few evening windows for a second-job schedule.
- A weekend-only pattern.
- A reusable one-off style pattern that the partner applies to selected dates.

### Appointment

A booking between client and partner for one or more services at a time.

For MVP, selected service order is not business-significant. Service prices and durations are summed for availability and total calculation.

An appointment may be linked to a partner-scoped client profile with or without Telegram identity.

Manual appointment client data can start from a minimal manual client profile, for example name and/or phone, when the client is not yet known in Telegram.

Snapshots:

- Selected service references.
- Service names at booking time.
- Service prices at booking time.
- Service durations at booking time.
- Total service duration at booking time.
- Appointment base price.
- Appointment total, including penalties applied to this appointment if applicable.
- Manual client name/phone snapshot when appointment is created without a Telegram-linked client profile.
- Client/request comment visible to client and partner.
- Private master note.

Multi-service rules:

- One appointment can include multiple partner-defined services.
- Selected services may come from different service categories.
- MVP has no hard service-count limit per appointment, but bot UX and Telegram message/keyboard limits still require pagination or compact rendering when needed.
- Client booking should use a multi-select/checkbox-like service selection UX.
- Service selection screen needs actions: done, clear selection, and back.
- If the client goes back from summary/service selection, the current service selection is reset.
- Unfinished booking selections are not restored later in MVP; when the client starts booking again after abandoning the flow, they start from scratch.
- Client sees a summary of selected services, total duration, and total amount before choosing a time; categories do not need to be shown in the summary.
- Client can remove selected services from the summary before choosing a time.
- If a selected service becomes inactive or changes while the client is still in the booking flow, the bot revalidates the selection and asks the client to review/reselect before proceeding.

Date/time selection rules:

- After summary, client selects a date first, then a time slot for that date.
- Date selection uses a hybrid Telegram UX: nearest available dates list plus month selection for farther booking.
- Nearest available dates list shows 7 dates at a time and supports moving to the next dates.
- Month selection shows only dates with available slots.
- If the selected date has no slots for the selected services, bot shows a no-slots message and actions: choose another date, write to master, or change services.
- Time slots for a selected day are paginated when there are many slots, roughly 8-10 slots per page.
- Near-fit slots are shown in the same slot list as normal slots, with a marker that master confirmation is required.

Final booking confirmation rules:

- Selecting a slot does not create an appointment immediately.
- After selecting a slot, client sees a final confirmation summary with selected services, date/time, duration, total amount, and relevant markers such as near-fit or penalties.
- The appointment is created only after the client explicitly confirms the final summary.
- Client booking comment is optional and must not block booking when empty.
- At final confirmation, the system re-checks slot availability before creating the appointment.
- If the slot became unavailable during final confirmation, bot shows that the slot is no longer available and returns the client to time selection.

Post-create booking messages/actions:

- Auto-confirmed booking creates a `confirmed` appointment; client immediately receives a confirmed message, and master receives a notification about the new confirmed appointment.
- Manual-confirmation booking creates a `requested` appointment; client receives a message that the request was sent and is waiting for master confirmation.
- For a `requested` appointment, the master receives inline actions: confirm, decline/cancel, or propose another time.
- After creating a `requested` appointment, client immediately has an action to cancel the request.
- When master confirms a `requested` appointment, appointment becomes `confirmed`, client receives a confirmed message, and reminders are created.
- When master declines/cancels a `requested` appointment, client-facing reason is optional.
- When master proposes another time for a `requested` appointment, master may propose multiple alternative slots.
- The MVP Telegram adapter exposes up to five alternatives per proposal while the proposal model remains adapter-neutral and extensible.
- When master proposes alternative slots for a `requested` appointment, the original appointment remains `requested` until the client chooses a slot, cancels the request, or the proposal expires.
- While such a proposal is pending, stale-request reminders are paused because the next action belongs to the client; decline/cancel resumes the reminder sequence.
- If the client chooses one of the master-proposed slots, the system re-checks availability, then the appointment becomes `confirmed` and reminders are created.
- If none of the proposed slots works for the client, the client may choose another available time; that choice follows the partner's normal auto-confirm/manual-confirm mode.
- A client-selected replacement time supersedes the pending master proposal. Auto-confirm uses the booking-resource lock before confirming; manual-confirm or near-fit updates the same request and starts a fresh actionable request/reminder cycle for the master.
- Requested appointment alternative proposals expire after 48 hours.
- If a requested appointment alternative proposal expires, the appointment is automatically cancelled and both client and master are notified.
- If client cancels a `requested` appointment, master receives a notification that the request was cancelled by the client.
- Near-fit bookings always create `requested` appointments, even when partner auto-confirmation is enabled.
- Near-fit client-facing text explains that the selected slot requires master confirmation.

Manual appointment service rules:

- Manual appointment creation from Calendar starts with the selected time/range first, then client and service details.
- If no existing client matches, the partner can quickly create a manual client in the appointment modal/panel.
- Manual appointments are `confirmed` by default.
- Manual appointments for Telegram-linked clients should send a Telegram notification.
- Manual appointments may be created outside availability windows with a warning, but still cannot overlap another `confirmed` appointment.
- Manual appointments may include one-off custom services that are not saved in the partner-defined service list.
- One-off custom services in manual appointments appear in client history as normal appointment service lines.
- In manual appointments, the master may override the price and/or duration of a selected partner-defined service only for that appointment.
- If price and/or duration was overridden by the master, the client sees a marker that the value was changed by the master.

### Appointment Service Item

Conceptual line item under an appointment.

Recommended direction for implementation:

- Store one line per selected service/custom service instead of one flat service snapshot on the appointment.
- This applies even when an appointment contains only one service.
- A line may reference a partner-defined service, or be a one-off custom service with no saved service reference.
- Each line stores the service name snapshot, price snapshot in minor units, duration snapshot, and whether it came from the partner-defined service list or was custom/overridden.
- Appointment base price and total duration are derived from the sum of item snapshots.
- This preserves future flexibility for discounts, bonuses, reporting by service, and manual adjustments.
- One-off custom/manual services do not automatically become partner catalog services. They remain appointment item lines only unless the partner separately creates a catalog service.

Client-visible history:

- Client history includes `completed`, `cancelled_by_client`, `cancelled_by_master`, and `no_show` appointments.
- If the master provides a cancellation or reschedule reason, the client can see it.
- MVP does not require a separate private cancellation/reschedule reason hidden from the client.
- `no_show` is visible to the client, not only to the partner.
- Applied penalties and the final appointment total are visible in client history.
- Manual client history remains partner/admin-only until the manual client profile is linked to a Telegram identity.
- Client history is shown from newest appointments to oldest.
- Bot history list uses short appointment cards with a button/action to open full details.
- Default client history page size is 5 records.

Client appointment detail includes service, date/time, status, service price, applied penalties, final total, client/request comment, and cancellation/reschedule reason when provided.

Master appointment detail includes the client-visible appointment information plus client phone where available and the private master note.

Client action rules:

- Future `confirmed` appointment: client can cancel, reschedule, or contact master.
- `requested` appointment: client can cancel the request before master response.
- `completed`, `cancelled_by_client`, `cancelled_by_master`, and `no_show`: client sees details only.

Master edit/correction rules:

- For future `confirmed` appointments, client-visible changes such as time, service set, price/duration, or client-visible comment should be proposed and require client confirmation before taking effect.
- Master private note can be edited directly because it is not client-visible.
- Internal-only changes such as master private note, internal-only status metadata, and internal comments do not require client confirmation.
- The current appointment remains unchanged while a client-visible change proposal is pending.
- Client-visible change proposals are sent to the client with accept/decline actions.
- Appointment change proposals expire after 48 hours by default.
- If the proposal expires or is declined, the old appointment remains unchanged and both sides should be notified where possible.
- If client confirmation is impossible, such as for a manual client without Telegram identity, the master can save directly with audit logging and a clear "client not notified/confirmed" indication.
- If the master changes price or duration for a future appointment, the client sees a marker that the value was changed by the master.
- Final statuses are not fully immutable in MVP: the master may correct mistakes after `completed`, `no_show`, or cancelled states.
- Corrections after finalization should be audit logged, and client-visible corrections should notify the client.
- Correction flow requires a master-provided reason for audit.
- Final status correction may change data or move between final statuses.
- Returning a final appointment back to `confirmed` is allowed only through an explicit restore/reopen action for a future appointment, with availability re-check and client notification or confirmation when client-visible changes are involved.
- Past final appointments should not be returned to `confirmed`; if another visit is needed, the partner creates or proposes a new appointment.
- Old reminders from before finalization are not restored automatically. If a future appointment is explicitly restored/reconfirmed, a fresh reminder schedule is created.

Completion/no-show/late-arrival:

- MVP does not auto-complete appointments.
- Master manually sets `completed` or `no_show` after the appointment time.
- When setting `completed`, master can mark applied penalties as paid.
- Setting `completed` does not change services, service snapshots, durations, prices, or final total; it confirms the appointment outcome using the data already on the appointment.
- Payment state for already-applied penalties may be updated separately while completing the appointment.
- When setting `no_show` and penalties are enabled, the system suggests creating a no-show penalty using the default amount.
- For late-arrival penalty, master manually marks lateness, enters minutes and optional reason, and the system suggests the default late-arrival penalty if lateness exceeds the configured threshold.
- Client is notified when master marks `completed`, marks `no_show`, or creates a penalty.

Reschedule rules:

- Master-initiated reschedule is a proposal to the client, not a silent time change.
- While a master proposal is pending, the existing appointment remains `confirmed`.
- Master can propose multiple concrete alternative slots.
- If proposed alternatives do not fit, client can choose another available slot or cancel.
- Client-initiated reschedule follows the partner confirmation mode, same as new booking.
- Auto-confirmation mode auto-confirms client-initiated reschedule after availability is re-checked.
- Manual confirmation mode requires master confirmation for client-initiated reschedule.
- Master receives a notification even when client-initiated reschedule is auto-confirmed.
- Master-initiated reschedule proposal lifetime defaults to 48 hours.
- If a master-initiated reschedule proposal expires, the old appointment remains `confirmed`; the client must request reschedule again if still needed.

MVP appointment total:

- `appointment_total = selected_service_price_snapshots_sum + penalties_applied_to_this_appointment`
- Money amounts are represented as integer minor units. Example: `500.00 UAH` is stored as `50000`.
- Discounts, promo codes, bonuses, and manual price adjustments are not part of MVP.
- The model should not make future discounts/promos/bonuses impossible, because similar mechanics are expected later in the Botera/TableOS ecosystem.

### Penalty

Monetary add-on caused by late cancellation.

Rules:

- Currency: UAH.
- Amount stored as integer minor units.
- Partner may enable or disable late-cancellation penalties.
- Late-cancellation penalties are disabled by default for a new partner.
- Default cancellation deadline for a new partner is 6 hours before appointment.
- Cancellation deadline minimum is 0 hours, meaning clients may cancel until appointment start without late-cancel penalty.
- Cancellation deadline maximum is 168 hours / 7 days.
- If enabled, default late-cancellation penalty amount is `50.00 UAH`, stored as `5000` minor units.
- Client self-service cancellation of a `confirmed` appointment is allowed only within the partner-configured cancellation deadline, unless the late-cancellation penalty flow applies.
- If the client tries to cancel after the deadline and penalties are enabled, the bot warns about the penalty and asks for confirmation before cancelling.
- If the client confirms, the appointment becomes `cancelled_by_client` and the penalty is created.
- Only one automatic late-cancellation penalty is created for one cancelled appointment.
- If the client tries to cancel after the deadline and penalties are disabled, self-service cancellation is blocked and the client is prompted to contact the master.
- Master can cancel after the deadline and can choose whether to create/apply a penalty.
- Penalties accumulate.
- Penalty statuses are `unpaid`, `applied_to_appointment`, `paid`, and `waived`.
- If a penalty was applied to an appointment and that appointment is later cancelled before the penalty is paid/settled, the penalty returns to `unpaid`.
- The cancelled application should remain available as historical/audit information, while the penalty itself becomes available to apply again.
- Penalties should be tied to a concrete business reason rather than arbitrary free-form charges.
- Confirmed MVP penalty reasons are late cancellation, no-show, and being late by more than partner-configured X minutes.
- Late-arrival penalty threshold is partner-configurable.
- Default late-arrival penalty threshold is 15 minutes.
- Default no-show penalty amount is `50.00 UAH`, stored as `5000` minor units.
- Default late-arrival penalty amount is `20.00 UAH`, stored as `2000` minor units.
- No-show penalty is suggested when master marks an appointment `no_show` and penalties are enabled.
- Late-arrival penalty is suggested when master manually marks lateness above the configured threshold.
- If penalties are enabled, client personal area shows active unpaid penalties separately from appointment history.
- The master chooses which unpaid penalties to apply to the current appointment.
- Applying a penalty to an appointment increases that appointment's total.
- Penalty payment/waiver is managed as part of the client penalty/debt record, while the appointment can show which penalties were applied to its total.
- Penalty-specific client messaging is not required in MVP; the general contact-master action is enough.
- Client-facing totals should be detailed: service price, penalties, and final total.
- Penalties are reported separately from service revenue as one combined penalties line.

### Penalty Application

Conceptual link between a penalty and the appointment where the master chose to include that penalty in the total.

Recommended direction for implementation:

- Store penalty applications separately from the penalty and the appointment.
- A penalty application references one penalty and one appointment.
- It stores the applied amount snapshot in minor units and currency.
- Applying a penalty increases the appointment total shown to the client and master.
- If the target appointment is cancelled before the penalty is paid/settled, the application is cancelled/released and the penalty returns to `unpaid`.
- Keep enough historical data to explain that the penalty was previously applied and then released because the appointment was cancelled.

### Appointment Proposal

Unified concept for appointment-related proposals that require a response or can expire.

Confirmed direction:

- Start with one general `AppointmentProposal` concept instead of separate `RescheduleProposal`, `RequestedAlternativeProposal`, and `AppointmentChangeProposal` models.
- The model must stay structured enough that it does not become an untyped dumping ground.
- Proposal type should distinguish:
  - `reschedule_confirmed`: master-initiated reschedule of a confirmed appointment.
  - `requested_alternatives`: alternative slots for a requested appointment.
  - `appointment_change`: client-visible appointment change, including service, price, duration, comment, or mixed changes with or without a time change.
- A proposal can contain one or more proposed time slots when the workflow requires alternatives.
- Proposed slots/options should be represented as separate `AppointmentProposalOption` records rather than embedded only as JSON on the proposal.
- A proposal keeps the original appointment unchanged while it is pending.
- Proposal lifecycle statuses are `pending`, `accepted`, `declined`, `expired`, `cancelled`, and `superseded`.
- If a new proposal is created for an appointment that already has a pending proposal, the older pending proposal should become `superseded`.
- Proposal lifetime defaults to 48 hours where previously confirmed for appointment proposals.
- Accepting a proposal must re-check availability before changing or confirming an appointment.
- Expired/declined behavior depends on proposal type: requested-appointment alternatives can auto-cancel the request on expiry, while master-initiated reschedule of a confirmed appointment leaves the old confirmed appointment unchanged.

Recommended data split:

- `AppointmentProposal` stores proposal metadata: partner, appointment, proposal type, status, creator/actor, created/updated timestamps, expiration time, client-visible message/reason when provided, and response timestamps.
- `AppointmentProposalOption` stores each proposed variant: start/end datetime when time is part of the proposal, total duration snapshot, service total snapshot, penalty total snapshot if relevant, final total snapshot, currency, selected/accepted marker, and option ordering.
- `AppointmentProposalOptionItem` stores proposed service item snapshots when the proposal changes services, price, duration, or uses a one-off custom service.
- If a proposal only changes time while keeping the appointment's services/price/duration, option item rows are not required; the option can reference the current appointment items conceptually.
- If a proposal changes services/price/duration, use option item rows so accepting the proposal can create/update appointment item snapshots without relying on opaque JSON.
- Small structured metadata may still exist for display/runtime convenience, but business-critical proposed values should be stored in typed fields/rows.

### Expense

Partner-scoped manual expense record for simple MVP reporting.

Purpose:

- Let a solo master record basic business expenses without turning MVP into full accounting.
- Support profit-like views later by combining actual revenue and partner-entered expenses.

Domain-level fields:

- Partner.
- Amount in minor units.
- Currency, MVP default `UAH`.
- Expense date.
- Description/reason.
- No category in MVP.
- Created/updated timestamps.

Rules:

- Expenses can be edited or deleted by the partner.
- Expense create/update/delete operations must be audit logged.

### Reminder

Scheduled notification to client or partner.

Types:

- Client appointment reminder.
- Partner appointment reminder.
- Morning partner digest.
- Stale `requested` appointment reminder.
- Failed reminder alert to partner.

Delivery invariant:

- Client business event notifications are required for appointment created/confirmed, appointment change proposed, change accepted/declined/expired, appointment cancelled, appointment completed, no-show marked, and penalty created/paid/waived.
- Manual clients without Telegram identity do not receive Telegram notifications; partner admin should show that there is no Telegram connection.
- Failed client business event notifications should be logged and shown to the master when action is useful.
- Failed appointment change proposal delivery should support a master-triggered notify/resend action.
- Reminder jobs must be idempotent.
- Retrying the same job must not send a duplicate reminder.
- Resending is allowed only for explicit failed-send retry or master-triggered resend.
- Client appointment reminders are scheduled only after an appointment becomes `confirmed`.
- Pending reminder jobs must be invalidated or skipped when their appointment is rescheduled, cancelled, completed, or marked `no_show`.
- Quiet-hours-delayed reminders must re-check whether they are still useful before sending; obsolete reminders, such as reminders for already-past appointments, are skipped.
- Client reminder offsets are configurable with minimum 15 minutes and maximum 30 days before appointment.
- Default client reminder offsets are 24 hours and 2 hours before appointment.
- A partner can have up to 5 client reminder rules.
- Master reminder before appointment is configurable, can be disabled, and defaults to 45 minutes before appointment.
- Master reminder offset minimum is 5 minutes and maximum is 7 days before appointment.
- Quiet hours are stored as local start/end times in the partner timezone.
- Default quiet hours are 22:00-08:00.
- Quiet hours can be disabled by the partner.
- Overnight quiet hours are supported because 22:00-08:00 is the normal default case.
- Morning digest time is stored as a local time in the partner timezone.
- Default morning digest time is 08:00.
- Morning digest time can be any local time from 00:00 to 23:59.
- Morning digest can be disabled by the partner.
- If morning digest time falls inside quiet hours, the digest is still sent at the configured time because the partner explicitly controls this master-facing notification.
- Stale `requested` appointment reminders continue until the request is processed, using decreasing intervals down to a 5-minute minimum and respecting partner quiet hours.
- Stale requested reminder base interval minimum is 10 minutes and maximum is 72 hours.
- Default stale requested reminder base interval is 3 hours.
- Failed reminder alerts to the partner should be created after automatic delivery retries are exhausted.
- Failed reminder delivery uses 3 automatic attempts with backoff of 1 minute, 5 minutes, then 15 minutes.
- Telegram "bot blocked by user" or equivalent unreachable-client signal is terminal for that delivery: no retry, mark the client unavailable for that specific partner bot, and retain profile/history.
- Master-triggered resend creates a new attempt while preserving delivery history. The failed marker remains until a resend succeeds or the partner handles the failure.

### Broadcast

Partner-created marketing message to selected clients.

Supports:

- Filters.
- Immediate or scheduled sending.
- Text.
- Photo.
- Buttons.
- Templates.
- Stats/statuses.

Delivery direction:

- Broadcasts are background jobs.
- A broadcast campaign represents the partner-created message and target/filter configuration.
- Broadcast recipient delivery records represent per-client send attempts and statistics.
- Immediate broadcasts start as queued and are sent gradually by background workers.
- Broadcast sending is queued and paced per partner bot.
- Initial pacing direction is no faster than one message every 0.5 seconds per bot.
- Telegram `429` should cause backoff and later continuation.
- Temporary Telegram errors should not fail the whole campaign.
- Durable recipient records must allow a crashed/restarted worker to continue from unsent recipients without duplicating recipients already recorded as completed.
- If a client becomes ineligible before their send turn, for example because they unsubscribed from marketing or blocked the bot, their delivery is skipped rather than failed.
- Detailed per-recipient delivery records use cleanup retention, defaulting to 90 days via `retain_until`.

### Audit Event

Partner-scoped record of meaningful business actions.

Partner sees own audit events. Superuser sees all.

UI/use:

- MVP should have a standalone partner-visible activity log/journal screen.
- Appointment and client cards may show related local history, but the standalone audit screen is the primary full audit view.

Partner-visible event scope:

- Appointment creation/update/cancellation/status changes.
- Penalty creation/update/payment/waiver.
- Expense creation/update/deletion.
- Broadcast creation/scheduling/start/cancellation/completion/failure.
- Reminder send/failure.
- Client-master messages.
- Partner bot/settings changes.
- Sensitive/destructive partner actions are audited, including appointment cancellation, no-show marking, penalty changes/payment/waiver, expense changes/deletion, bot token/settings changes, and corrections after final status.

Reason policy:

- Reason is required for correction-flow actions after a final appointment status.
- Reason is optional in MVP for master cancellation, penalty waiver, and expense deletion.
- Partner-visible Activity Log entries are business-friendly and do not expose raw technical payloads.

Retention:

- Partner-visible business audit logs are retained for 6 months by default.
- Platform/system logs follow cleanup retention unless a later approved policy overrides it.

Platform/system diagnostics:

- Superuser-visible system/audit logs include technical errors from bot runner and background jobs.
- Sensitive data must be masked in platform/system logs.

Reason requirements:

- Correction-flow actions after a final appointment status require a master-provided reason.
- Reason is optional in MVP for master cancellation, penalty waiver, and expense deletion.

## Appointment States

MVP appointment statuses:

- `requested`
- `confirmed`
- `cancelled_by_client`
- `cancelled_by_master`
- `completed`
- `no_show`

## Appointment State Transitions

Allowed MVP transitions:

- `requested -> confirmed`
- `requested -> cancelled_by_client`
- `requested -> cancelled_by_master`
- `confirmed -> completed`
- `confirmed -> no_show`
- `confirmed -> cancelled_by_client`
- `confirmed -> cancelled_by_master`

Manual confirmation mode:

- New client booking starts as `requested`.

Auto-confirmation mode:

- New client booking starts as `confirmed`.

No separate `rejected` status is required in MVP.

## Broadcast States

MVP broadcast/message delivery statuses:

- `queued`
- `sent`
- `failed`
- `skipped`

`delivered` is not promised unless a reliable Telegram-level signal exists.

## Bot Pagination

Telegram-facing lists and keyboards must be pageable when they can grow beyond a small fixed size.

This applies to client history, appointment lists, services, slot choices, conflict lists, broadcast previews, and any entity-bound inline keyboard.

Pagination is a product/runtime requirement, not only a UI nicety, because Telegram message length and inline keyboard limits can be reached by repeat clients or partners with many records.

Default pagination behavior:

- Use previous/next navigation.
- Show a page indicator such as `1/4`.
- Use localized button labels from the multilingual content system.

## Bot-Mediated Conversation

Partner-scoped client-master conversation through the partner bot.

MVP rules:

- Conversation is scoped to `partner + client`.
- Conversation is not tied to a specific appointment in MVP.
- Client initiates contact from the client personal area.
- Master receives the message through the partner's linked master Telegram contact.
- Master replies through the bot.
- Client receives the reply from the partner/master bot.
- If delivery to the client fails because the client blocked the bot, show "message not delivered" to the master and mark the client unavailable for this partner bot.
- No separate "close dialog / do not write me" client action is required in MVP.
- Marketing unsubscribe does not disable service/manual messages.

## Retention And Cleanup

Some records exist primarily to support delivery tracking, diagnostics, statistics, or short-lived technical state. These records should not be retained indefinitely.

Cleanup-eligible records should use `retain_until` to mark when the system may delete them. This is preferred over `expires_at` because the record may still be historically true after that timestamp; it is simply no longer useful enough to keep.

Default cleanup retention is 90 days. Unless a specific cleanup-eligible record type later receives a different approved policy, set `retain_until` to `created_at + 90 days`.

Initial cleanup-eligible categories:

- Detailed per-recipient broadcast delivery records.
- Delivery attempt records.
- Partner-visible technical logs.
- Platform/system logs.
- Other future high-volume technical records that are useful only for a limited window.

Business records such as clients, appointments, visit history, penalties, and partner data are not cleanup-eligible by default.

## Settings Validation

Partner-editable settings should be valid before they can affect runtime behavior.

Validation rules:

- Obviously invalid bot tokens are rejected at save time.
- Bot tokens that pass save-time validation but fail Telegram/runtime startup put the bot into `error`.
- Editable template overrides are validated before save against their expected placeholder context.
- Unknown placeholders and render errors block saving.
- Runtime rendering remains defensive and should fall back to safe defaults if invalid legacy content somehow exists.
- Editable button labels are validated for non-empty text, length, and routed-label collisions where relevant.
- Required booking fields block booking until the client provides the required data.
- Reminder and time-based settings use explicit min/max bounds.
- Previously saved settings that become invalid after a system change should fall back to safe defaults and show an admin warning instead of crashing runtime.

## Business Invariants

- Partner data must remain isolated by partner boundary.
- Client profile data is partner-scoped even if Telegram identity is global.
- Partner-owned records must not be visible or mutable across partner boundaries.
- MVP owner/master has full access to own partner-scoped data and settings.
- Two active accepted appointments for the same booking resource cannot overlap in time.
- In solo-master MVP, each partner has one default booking resource.
- `confirmed` appointments always block availability.
- Auto-confirmed bookings start as `confirmed` and therefore block availability immediately.
- Manual-confirmation `requested` appointments do not block availability in MVP.
- Multiple `requested` appointments may compete for the same slot.
- Confirming a `requested` appointment must re-check availability at confirmation time.
- If the slot is already occupied, confirmation must be rejected unless the partner changes the appointment time or resolves the conflict.
- Appointment price and duration are fixed at booking time.
- Schedule changes do not silently alter existing appointments.
- Client reminders are sent only for `confirmed` appointments.
- Client marketing broadcasts are sent only to clients who have not unsubscribed.
- Service notifications still work even if client unsubscribes from marketing.
- Bot outputs and inline keyboards that can grow large must be paginated and must respect Telegram limits.
- Bot token must not leak through UI, logs, or partner-visible audit records.
- Bot tokens are stored encrypted and are shown only as masked/status representation after save.
- Client phone is stored in usable normalized form and may be shown fully to the owning master in client/appointment/communication context.
- Client phone should be masked or shortened in broad UI lists where the full number is not needed.
- Client-master messages are stored as partner-scoped business data for conversation history/support, but raw message text must not enter technical logs.
- Master private notes are partner-scoped sensitive business data visible to the partner/master; raw note text must not enter technical logs or audit payloads.
- Superuser technical/support access to sensitive partner data may exist, but it is not a casual/default working screen.
- Platform diagnostics and errors must use masked/redacted sensitive values.
- Raw Telegram payloads should be filtered/masked before storage.
- Failed reminders must be visible to the partner.
- Penalties are distinct from service revenue.

## Conflict Handling

When schedule changes create conflicts:

- Existing appointment remains unchanged until partner acts.
- Partner sees conflict list.
- Conflict list shows client, service, current/old appointment time, and conflict reason/problem.
- Partner can leave appointment as is.
- Partner can propose concrete alternative slots.
- Partner can cancel appointment.
- Partner can message client.
- Partner can perform bulk conflict actions.

## Deferred Architecture And Design Questions

- Future studio support may require extending the partner concept, but this is outside MVP.
- Exact appointment total formula when future discounts/manual adjustments appear.
- Exact bot-mediated message thread/context model.
- Exact schedule model and UX for irregular availability versus repeatable weekly schedule.
- Exact broadcast backoff intervals, global concurrency thresholds, and alert thresholds.
- Exact platform-only technical audit event list.
