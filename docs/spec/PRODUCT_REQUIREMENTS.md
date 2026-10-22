# BoteraMaster Product Requirements

Status: finalized MVP product requirements on 2026-08-20, not final technical architecture.

This document describes product requirements for the BoteraMaster MVP. It is based on `docs/spec/MVP_SPEC_OVERVIEW.md`. It is not an implementation plan and does not choose a final technical architecture.

## Product Summary

BoteraMaster is a Telegram-first scheduling, client-management, communication, notification, and broadcast service for solo beauty masters.

The first MVP user is a solo appointment-based beauty master without a team. In user-facing language this person is a master. In platform/back-office/internal language the same tenant boundary is called a partner.

Each partner has their own Telegram bot. The partner provides a bot token, and BoteraMaster operates the bot. The partner does not manage servers, hosting, workers, deployment, or bot runtime.

BoteraMaster belongs to the broader Botera ecosystem idea. Future cross-product promotions, bonuses, and partner programs are out of scope for this MVP.

## MVP Goals

- Let a solo beauty master accept and manage client bookings through their own Telegram bot.
- Give clients a predictable bot flow for booking, cancellation, rescheduling, profile management, and contact with the master.
- Give partners control over their own services, schedule, clients, appointments, bot texts, buttons, language, reminders, cancellation rules, and broadcasts.
- Keep partner data isolated: partners only see their own data; superuser can see platform-wide data.
- Provide reliable reminder and notification behavior, including failure visibility.
- Support Ukrainian, Russian, and English from MVP, with Ukrainian as default.

## MVP Non-Goals

- Studios or multi-master organizations.
- Employee roles or granular partner-side permissions.
- Online payments.
- Manual prepayment by requisites.
- Reviews.
- Loyalty.
- Google Calendar sync.
- Self-service client data deletion through bot.
- Data export.
- Repeat previous service.
- Advanced no-show consequences beyond MVP penalties, such as prepayment requirements.
- Cross-product Botera partner program, bonuses, and promotions.

## Users And Roles

### Client

A Telegram user who interacts with a partner's bot to book and manage visits.

Client can:

- Open the partner bot.
- Select language.
- Enter or edit profile data.
- Book one or more services.
- View future appointments.
- Cancel appointments according to partner cancellation rules.
- Reschedule appointments.
- View full visit/appointment history.
- Contact the master through the bot.
- Unsubscribe from marketing broadcasts while still receiving service notifications.

### Partner / Master

The solo master who owns a partner area in BoteraMaster.

Partner can:

- Manage own bot token.
- Manage services, prices, and durations.
- Manage schedule, working time, days off, and blocked time.
- Manage appointments.
- Manage clients.
- Configure bot texts, button labels, languages, reminders, quiet hours, required booking fields, cancellation rules, and penalties.
- Send client broadcasts.
- View partner-scoped audit logs.
- See failed reminders and act on them.

In MVP, the partner role is a single owner/master role. The owner/master can manage all partner-scoped data and settings, including services, prices, calendar, appointments, clients, penalties, broadcasts, bot texts/buttons, reminders, quiet hours, required fields, and bot token.

### Superuser / Platform Admin

Platform operator with access across all partners.

Superuser can:

- Connect and manage partners.
- See platform-wide data and logs.
- Investigate support/incidents, including client-master conversations when required.
- Manage global settings that affect platform behavior or multiple partners.
- Technically access partner private notes if needed, but this is treated as operationally sensitive and should not be used casually.

## Functional Requirements

### Partner And Bot Setup

- MVP partner onboarding is controlled by superuser.
- Public/self-service partner registration is not part of MVP.
- Superuser creates the partner record.
- Superuser creates the partner admin account and gives the partner login/password.
- Partner must complete minimum setup before the bot is considered ready for clients.
- Minimum setup before bot launch: bot token, language, timezone, at least one service category/service, at least one availability window, and booking confirmation mode.
- Most other settings may use defaults at first launch, including texts/buttons, reminders, quiet hours, cancellation deadline, penalties off, and basic required fields.
- Setup should have a draft/setup-required state so the bot does not run for clients before required setup is complete.
- Partner admin should include an onboarding checklist/setup guide with the minimum launch steps: add bot token, choose language/timezone, create at least one category/service, open at least one availability window, choose booking confirmation mode, check bot connection, and launch bot.
- While setup is incomplete, client booking/profile flows must not be available.
- If a client opens an incomplete partner bot, the bot may answer with a short system-only localized "bot is being configured / soon available" message instead of silently doing nothing.
- After the minimum checklist is complete, the partner can launch their bot without a final superuser approval step.
- Superuser can still suspend a partner/bot if needed.
- If an already launched partner later removes all services or availability windows, the bot may remain active but should show clients a proper "no services" or "no available slots" state.
- Superuser admin should include a view or filters for new/setup-required partners, including what setup steps are missing.
- Each partner must have one primary Telegram bot in MVP.
- Bot configuration should be split conceptually into Bot Instance and Bot Settings.
- Bot Instance represents the technical Telegram bot connection/runtime identity: token, operational status, runtime eligibility, last error, and runtime health.
- Bot Settings represent partner-controlled behavior/content: languages, texts, buttons, reminders, quiet hours, booking rules, required fields, cancellation rules, and penalties.
- Partner bot/settings configuration is admin-only in MVP; partners do not configure these settings through the Telegram bot.
- Admin UX should expose one main `Bot` section with tabs: Instance, Settings, Content, Notifications, Booking, and Penalties.
- Partner may enter or replace their own bot token.
- Partner can switch their own bot between `active` and `paused`.
- `suspended` is superuser-only and cannot be set or cleared by the partner.
- After saving, the full token must not be shown back to the partner.
- The UI should show safe bot connection status, such as `active`, `paused`, `suspended`, or `error`.
- Partner should see bot health/status in admin, including operational status, last startup/runtime error when present, and last successful polling/activity timestamp when available.
- When partner replaces a bot token, the old bot runtime should stop and the new bot configuration should reconnect/start according to bot status and validation result.
- If a bot token is obviously invalid by format/required validation at save time, saving should be blocked with an error.
- If a bot token passes save-time validation but Telegram/runtime later refuses startup, bot status should become `error`, partner should see the problem in admin, and superuser should be notified or be able to see the problem in platform diagnostics.
- When bot status is `error`, partner can use an admin action such as "Check connection" / "Retry check" to revalidate the token/configuration and see the current error without necessarily changing the token.
- If the token/configuration is fixed, saving the corrected token/settings and/or retrying the connection check should update the visible bot health.
- In MVP, because the runner is simple and does not hot-reload, a successful check may still require runner restart/redeploy before live polling actually changes.
- Multiple bots per partner are not baseline MVP and may be treated as custom/paid future work.

### Bot Runtime

- MVP bot delivery channel is Telegram polling.
- Runtime must only start bots that are eligible to run, such as active bot, active partner, valid token, and polling mode.
- TableOS active bot config loading is a relevant reference: validate active partner, polling mode, decryptable/usable token, and duplicate token safety before startup.
- MVP runtime uses a simple TableOS-like bot runner: one runner process/command loads eligible active bots at startup and runs polling for them.
- In MVP, bot token/status changes are applied to runtime through runner restart/redeploy rather than hot reload.
- Hot reload or periodic config reload can be added later if operational pain justifies it.

### Background Jobs

- Background jobs are foundational for MVP.
- Client reminders, master reminders, morning digest, stale `requested` reminders, broadcasts, and failed-reminder retries should be processed outside bot handler flow.
- Background jobs that belong to a partner must operate inside that partner scope.
- System-level jobs may operate globally but must still preserve partner boundaries when touching partner data.
- Reminder sending must be idempotent: a retry of the same job must not send the same reminder twice.
- A reminder can be sent again only as an explicit failed-send retry or when the master presses a resend action.
- Failed reminder delivery uses 3 automatic attempts before notifying the partner.
- Automatic reminder retry backoff is 1 minute, then 5 minutes, then 15 minutes.
- If Telegram reports that the client blocked the partner bot, the failure is terminal: do not retry, mark the client unavailable for this partner bot, and keep their profile/history.
- Client business event notifications are required for appointment created/confirmed, appointment change proposed, change accepted/declined/expired, appointment cancelled, appointment completed, no-show marked, and penalty created/paid/waived.
- If a client business event notification cannot be delivered because the client blocked the bot, the event is logged, the client is marked unavailable for this partner bot, and the master sees a delivery failed marker/notification where action is useful.
- For manual clients without Telegram identity, Telegram notifications are not created; partner admin should clearly show that there is no Telegram connection for that client.
- For failed appointment change proposal delivery, the master should have a manual "notify/resend" action.
- Client appointment reminders are scheduled only when an appointment becomes `confirmed`.
- Pending reminders for an appointment become stale/invalid when the appointment is rescheduled, cancelled, completed, or marked `no_show`.
- Broadcast sending should be queued and paced per partner bot.
- MVP broadcast pacing direction: no faster than one message every 0.5 seconds per bot, with backoff when Telegram returns `429`.
- Large broadcasts may take a long time to complete, and the partner should see visible progress/status counters while sending continues.
- If broadcast sending is interrupted by a worker crash/restart, it should resume from unsent recipients without duplicate sends for recipients already recorded as completed.

### Partner-Scoped Admin

- Partner must see only their own data area.
- Superuser must be able to see all partners and all relevant platform data.
- Partner-editable settings are settings that affect only that partner.
- Global settings that affect root application behavior or multiple partners are superuser-only.
- Partner-owned records should be scoped by partner as a foundational rule, conceptually similar to TableOS partner-bound data patterns.
- Partner-scoped admin must filter data by partner and must prevent creating/changing/deleting records under another partner.
- Suspended partners may access admin only for limited purposes such as viewing data, payment/recovery, or fixing the suspension reason. New client-facing bot actions should be unavailable while suspended.
- MVP partner admin main navigation should include Dashboard, Calendar, Appointments, Clients, Catalog, Broadcasts, Finance, Bot, and Activity Log.
- Partner admin default landing screen should be an operational dashboard, not a marketing-style or analytics-heavy page.
- Dashboard should prioritize today's and tomorrow's calendar/appointments, pending `requested` appointments, failed reminders/notifications, active conflicts or problems, and compact basic stats/revenue.
- Calendar should be the primary day-to-day working screen for the master because availability and appointments are the product's core operational surface.
- Appointments list remains important as a searchable/filterable operational list, but it is secondary to Calendar as the default working surface.
- Appointment list MVP filters should stay limited to practical operational filters: date/period, status, client search, service/category, penalty presence/unpaid penalty, and requested/near-fit markers.
- MVP income report should be simple day/week/month reporting with service revenue, penalties as a separate combined line, and total.
- MVP reporting should also show expected revenue from future `confirmed` appointments separately from actual revenue.
- Actual revenue is calculated from `completed` appointments using the current appointment state; paid/settled penalties applied to those appointments are included.
- Expected revenue is calculated from future `confirmed` appointments and includes only penalties already applied to those appointments, not all unpaid client penalties.
- If a `completed` appointment is corrected and its amount changes, reports recalculate from the appointment's current corrected state.
- Reports must use the partner's timezone for day/week/month boundaries.
- MVP reporting should let the partner add simple expense records with amount, date, and description/reason.
- Expense categories are not required in MVP; partner can search/filter by description/title later if needed.
- Expenses are partner-only data and must not be shown to clients.
- Expenses can be edited or deleted by the partner, and these changes must be audit logged.
- Partner client card should show client data, future appointments, appointment history, unpaid penalties, visit count, and total amount spent with this partner.
- Partner Activity Log should exist as a standalone screen, while appointment and client cards may also show short related local history.
- Superuser admin in MVP can remain Django-admin-like/pragmatic rather than a polished separate product dashboard, with dedicated operational views/filters for health, setup-required partners, and problems/incidents.

### Services

- Partner can create, edit, disable/archive, and view services.
- Partner can create, edit, disable/archive, sort, and view service categories.
- A service category has at least name, optional description, sort order, and active/inactive state.
- A service has at least name, category, optional short description, price, duration, sort order, and active/inactive state.
- Services are fully partner-defined; BoteraMaster does not provide a shared/global service catalog for masters.
- Service price may be `0.00`, for example for a free consultation.
- Service categories need partner-controlled sort order for client-facing display.
- Services need partner-controlled sort order for client-facing display.
- If a service category is inactive, services inside it are hidden from client booking flow.
- Client-facing service lists in the bot must be paginated when they grow large.
- Existing appointments keep the service price and duration captured at booking time.
- Service price/duration changes affect only new appointments.
- Inactive services are hidden from client booking flow.
- Existing appointments and history that reference inactive services remain visible.
- Photo, buffer/pause after service, and calendar color are outside the minimum service fields for now.

### Schedule And Availability

- Partner can configure availability, days off/unavailable dates, blocked time, timezone, and booking slot step.
- Booking availability must respect total selected service duration, availability windows, blocked time, and existing appointments.
- Schedule UX must handle partners with irregular availability, including masters who work in this field as a second job and may open only specific free days/hours.
- Main calendar view defaults to week view.
- Calendar should support switching between day, week, and month views.
- The primary MVP calendar workflow should let the partner open a calendar and mark/draw available windows directly.
- Partner can create an availability window by selecting a time range with mouse/touch.
- Partner can also create an availability window through quick manual input: date, start time, end time.
- From the calendar, partner should also be able to create blocked/unavailable time and manual appointments through the same select/range workflow.
- Calendar selection should let the partner choose what they are creating: availability window, blocked time, or manual appointment.
- Availability windows, blocked time, appointments, conflicts, and near-fit/request markers should be visually distinguishable by type, such as color/treatment/label.
- Clicking an existing appointment in the calendar should open a detail panel or modal with key actions: edit, propose reschedule, cancel, mark completed, mark no-show, and message client.
- Calendar should support quick copy actions such as copy day and copy week in addition to saved availability templates.
- Calendar admin should be usable on mobile with adapted interactions, while dense schedule editing may remain more comfortable on desktop/tablet in MVP.
- Clients see bookable slots only inside partner-created availability windows.
- Partners should be able to save availability templates and reuse/apply them later.
- More than one availability template per partner should be supported in MVP.
- Partner can apply an availability template to a future week or date range.
- Applying a template should preview conflicts with existing confirmed appointments instead of silently applying a conflicting change.
- A repeatable weekly base schedule can be represented as a reusable template rather than being the only scheduling mode.
- Booking horizon must be partner-configurable so partners can decide how far ahead clients may book.
- Near-fit tolerance is part of MVP and is partner-configurable.
- Default near-fit tolerance is 10 minutes.
- Near-fit tolerance applies only when the appointment exceeds the end of an availability window by the allowed tolerance.
- Near-fit tolerance does not allow overlap with blocked time or another `confirmed` appointment in MVP.
- Partner can block/unavailable time inside an available window, such as a personal break or one-off unavailable interval.
- Blocked/unavailable time can be created similarly to availability windows by selecting a time range and choosing a blocked/unavailable type.
- Blocked/unavailable reason text is optional.
- If schedule changes create conflicts with existing appointments, the system must not silently move or invalidate them.
- Conflict list shows client, service, current/old appointment time, conflict reason/problem, and available actions.
- Available conflict actions: leave as is, propose reschedule, cancel, or message client.
- Partner can send concrete alternative slots to clients.
- If proposed slots do not fit, client can choose another available slot or cancel.
- Bulk actions for schedule conflicts are part of MVP.
- A convenient calendar-like admin experience for weekly/monthly planning is desired.

### Booking Flow

- Client opens partner bot and chooses booking.
- Client selects a service category first, then selects one or more services, then selects date and available time.
- Multiple services may be included in one appointment in MVP.
- Selected services may come from different service categories.
- Service order inside one appointment is not business-significant in MVP; selected service prices and durations are summed.
- There is no hard service-count limit per appointment in MVP, but practical UI/message limits and availability duration still apply.
- Service selection in the client booking flow should use a multi-select/checkbox-like UX.
- Service selection screen needs actions: done, clear selection, and back.
- If the client goes back from summary/service selection, the current service selection is reset.
- Unfinished booking selections are not restored later in MVP; when the client starts booking again after abandoning the flow, they start from scratch.
- Before selecting a time, client sees a booking summary with selected services, total duration, and total amount; categories do not need to be shown in the summary.
- From the booking summary, the client can remove selected services before choosing a time.
- If a selected service becomes inactive or changes while the client is still in the booking flow, the bot must revalidate the selection and ask the client to review/reselect before proceeding.
- After summary, client selects a date first, then a time slot for that date.
- Date selection uses a hybrid Telegram UX: show the nearest available dates as a list, plus an action to choose a month for farther booking.
- The nearest available dates list shows 7 dates at a time and has a "next dates" action.
- Month selection shows only dates with available slots; unavailable dates do not need to be shown.
- If the selected date has no slots for the selected services, show a no-slots message and actions: choose another date, write to master, or change services.
- Time slots for a day are paginated when there are many slots, roughly 8-10 slots per page.
- Near-fit slots are shown in the same slot list as normal slots, with a marker that master confirmation is required.
- Selecting a slot does not create an appointment immediately.
- After selecting a slot, client sees a final confirmation summary with selected services, date/time, duration, total amount, and relevant markers such as near-fit or penalties.
- The appointment is created only after the client explicitly confirms the final summary.
- Client booking comment is optional and must not block booking when empty.
- At final confirmation, the system re-checks slot availability before creating the appointment.
- If the slot became unavailable during final confirmation, bot shows that the slot is no longer available and returns the client to time selection.
- Bot requests missing required client data.
- Required booking fields are configurable per partner.
- If partner marks a field such as phone as required, booking must stop and ask the client to fill that field before continuing.
- If partner uses manual confirmation, booking starts as `requested`.
- If partner uses auto-confirmation, booking starts as `confirmed`.
- If auto-confirmation creates a `confirmed` appointment, client immediately receives a booking confirmed message, and the master receives a notification about the new confirmed appointment.
- If manual confirmation creates a `requested` appointment, client receives a message that the request was sent and is waiting for master confirmation.
- For a `requested` appointment, the master receives a booking request with inline actions: confirm, decline/cancel, or propose another time.
- After creating a `requested` appointment, client should immediately have an action to cancel the request.
- When master confirms a `requested` appointment, appointment becomes `confirmed`, client receives a confirmed message, and reminders are created.
- When master declines/cancels a `requested` appointment, client-facing reason is optional.
- When master proposes another time for a `requested` appointment, master may propose multiple alternative slots.
- The MVP Telegram master flow allows up to five alternatives in one proposal; proposal storage remains capable of supporting more options in future adapters.
- When master proposes alternative slots for a `requested` appointment, the original appointment remains `requested` until the client chooses a slot, cancels the request, or the proposal expires.
- Stale-request reminders pause while a requested-alternatives proposal is pending and resume on the next sequence if the client declines it or the master cancels it.
- If the client chooses one of the master-proposed slots, the system re-checks availability, then the appointment becomes `confirmed` and reminders are created.
- If none of the proposed slots works for the client, the client may choose another available time; that choice follows the partner's normal auto-confirm/manual-confirm mode.
- Choosing another time supersedes the pending master proposal. A normal slot in auto-confirm mode becomes `confirmed` after a lock-protected availability re-check; manual-confirm and near-fit choices remain `requested` and create a fresh actionable master notification.
- Requested appointment alternative proposals expire after 48 hours.
- If a requested appointment alternative proposal expires, the appointment is automatically cancelled and both client and master are notified.
- If client cancels a `requested` appointment, master receives a notification that the request was cancelled by the client.
- Master receives notification for manual booking requests with inline actions.
- Client receives confirmation/cancellation/reschedule information after master action.
- Double booking of accepted appointments must be prevented.
- A slot is available only if the full total duration of selected service(s) fits into an availability window and does not overlap a `confirmed` appointment or blocked time.
- A near-fit slot that exceeds the end of an availability window within partner-configured tolerance may be shown to the client with a "requires master confirmation" marker.
- A near-fit booking always starts as `requested`, even if the partner uses auto-confirmation.
- For near-fit bookings, client-facing text must explain that this slot requires master confirmation.
- `confirmed` appointments block availability.
- Auto-confirmed bookings start as `confirmed` and block availability immediately.
- Manual-confirmation `requested` appointments do not block public availability in MVP.
- Multiple `requested` appointments may exist for the same slot.
- When the partner tries to confirm a `requested` appointment, the system must re-check availability.
- If another appointment already occupies the slot, the partner cannot confirm the request as-is and must choose another action, such as propose reschedule, cancel, or adjust time.

### Appointment Management

- Partner can view today, tomorrow, and upcoming appointments.
- Partner can create an appointment manually.
- Manual appointment creation from Calendar starts with selected time/range first, then the partner chooses or creates the client and selects services.
- If the client is not found during manual appointment creation, the partner can quickly create a manual client inside the modal/panel.
- Manual appointments are `confirmed` by default.
- If a manual appointment is created for a client profile linked to Telegram identity, the client receives a Telegram notification.
- Manual appointments may be created outside availability windows with a warning, but they still must not overlap another `confirmed` appointment.
- Manual appointments may be created for clients without Telegram, using minimal name and/or phone data.
- Manual appointments may use a one-off custom service that is not saved in the partner-defined service list.
- In manual appointments, the master may override selected service price and/or duration only for that appointment.
- If a manual appointment uses overridden price and/or duration for a partner-defined service, the client sees a marker that the value was changed by the master.
- Manual appointments should create or use a partner-scoped client profile even when there is no Telegram identity yet.
- A manual client profile may have name and/or phone without Telegram identity.
- Telegram client reminders are not possible for manual appointments until the client profile is linked to a Telegram identity.
- Master reminders and morning digest still include manual appointments.
- If a Telegram user later appears with the same phone as an existing manual client profile for that partner, the system should suggest linking/merging to the master.
- Linking/merging manual client data to a Telegram client profile should not happen silently; master confirmation is required.
- When linked, previous manual appointment history and visit count should become visible in the partner-scoped client profile.
- Manual clients without phone, for example only name/note, are not auto-linked later.
- Partner can confirm, cancel, complete, reschedule, and mark no-show.
- For a future `confirmed` appointment, the master may propose client-visible changes such as time, service set, price/duration, or client-visible comment, but these changes should require client confirmation before taking effect.
- Master private note may be edited by the master without client confirmation because it is not client-visible.
- Internal-only changes such as master private note, internal-only status metadata, and internal comments do not require client confirmation.
- When a client-visible appointment change requires confirmation, the current appointment remains unchanged until the client accepts.
- Client receives a "master proposed appointment changes" message with accept and decline actions.
- Appointment change proposals expire after 48 hours by default.
- If a client-visible change proposal expires or is declined, the old appointment remains as it was and both sides should be notified where possible.
- If the client cannot be reached through Telegram, such as a manual client without Telegram identity, the master can save the change directly with audit logging; the system should make clear that client confirmation/notification was not possible.
- If a future appointment price or duration is changed by the master, the client must see a marker that the value was changed by the master.
- Client can cancel and reschedule appointments from the bot.
- Client can cancel a `requested` appointment before the master responds.
- Appointment history must include all appointment outcomes, including `completed`, `cancelled_by_client`, `cancelled_by_master`, and `no_show`.
- If the master provides a cancellation or reschedule reason, the client sees that reason in the bot.
- MVP does not require a separate private cancellation/reschedule reason hidden from the client.
- A client can see when an appointment is marked `no_show`; it is not only an internal master marker.
- If penalties were applied to an appointment, the client history shows the penalty line and final total.
- For a manual client without Telegram identity, appointment history is visible only to the partner/admin until that client profile is linked to a Telegram identity.
- Final statuses such as `completed`, `no_show`, and cancelled states are not fully immutable in MVP: the master may correct mistakes after finalization.
- Corrections after finalization should be audit logged, and if the correction changes client-visible information, the client should be notified.
- Correction flow must require a master-provided reason for audit.
- Final status correction can change data or move between final statuses.
- Returning a final appointment back to `confirmed` is allowed only through an explicit restore/reopen action for a future appointment, with availability re-check and client notification or confirmation when client-visible changes are involved.
- Past final appointments should not be returned to `confirmed`; if another visit is needed, the partner should create or propose a new appointment.
- Old reminders from before finalization must not be restored automatically. If a future appointment is explicitly restored/reconfirmed, the system creates a fresh reminder schedule.
- If no slots are available for the selected service(s), the bot should show that there are no suitable slots in the searched/nearest period and suggest contacting the master.
- Appointment should store partner, client or manual client snapshot, selected service references, service name snapshots, service price snapshots, service duration snapshots, total service duration snapshot, start time, end time, status, client/request comment, private master note, and total amount.
- For multi-service appointments, stored service snapshots preserve selected services without treating their order as a business rule.
- One-off custom services in manual appointments appear in client history as normal appointment service lines.
- Conceptually, selected partner-defined services and one-off custom services should be represented as appointment service items/lines under the appointment, with snapshots per line, so future discounts, bonuses, reporting, and manual adjustments can extend without rewriting the appointment concept.

Appointment completion/no-show/late-arrival handling:

- MVP does not auto-complete appointments.
- After the appointment time, master manually sets `completed` or `no_show`.
- When master sets `completed`, master can mark applied penalties as paid.
- Setting an appointment to `completed` fixes the appointment as it already exists: services, service snapshots, durations, prices, and final total are not edited as part of completion.
- Payment state for already-applied penalties may be marked separately while completing the appointment.
- When master sets `no_show` and penalties are enabled, the system suggests creating a no-show penalty with the default amount.
- For late-arrival penalty, master manually marks that the client was late, enters minutes and optional reason, and the system suggests the default late-arrival penalty if the lateness is greater than the partner-configured threshold.
- Client receives a notification when master marks an appointment `completed`.
- Client receives a notification when master marks an appointment `no_show`.
- Client receives a notification when a penalty is created.

### Client Personal Area

Client personal area must include:

- Profile/card.
- Editable client data.
- Visit count calculated from appointments/visits.
- Additional free-text field.
- Future appointments.
- Cancellation.
- Rescheduling.
- Full history.
- Contact master through bot-mediated messages.

Client history UX:

- Sort from newest appointments to oldest.
- Show short appointment cards in the list.
- Open full appointment details through a button/action.
- Default page size is 5 history records per page.

Client appointment detail must show:

- Service(s).
- Date/time.
- Status.
- Service price(s).
- Applied penalties.
- Final total.
- Client/request comment.
- Cancellation or reschedule reason when provided.

Master appointment detail must show the client-visible appointment information plus:

- Client phone when available and allowed in partner scope.
- Private master note.

Client actions by appointment status:

- Future `confirmed` appointment: cancel, reschedule, contact master.
- `requested` appointment: cancel request before master response.
- `completed`, `cancelled_by_client`, `cancelled_by_master`, and `no_show`: details only, no appointment-changing actions.

Reschedule flow:

- Master-initiated reschedule should be sent to the client as a proposal, not silently applied.
- When master proposes alternative slots, the existing appointment remains `confirmed` until the client selects a new slot or another final action happens.
- A master proposal may contain multiple concrete alternative slots.
- If proposed alternatives do not fit the client, the client can choose another available slot or cancel.
- Client-initiated reschedule follows the partner's confirmation mode, same as a new booking.
- If the partner uses auto-confirmation, a client-initiated reschedule is auto-confirmed after availability is re-checked.
- If the partner uses manual confirmation, a client-initiated reschedule requires master confirmation.
- Even when a client-initiated reschedule is auto-confirmed, the master receives a notification that the client moved the appointment.
- Master-initiated reschedule proposal lifetime defaults to 48 hours.
- If a master-initiated reschedule proposal expires, the old appointment remains `confirmed`, and the client must request reschedule again if they still want to move it.

Repeat previous service is outside MVP.

### Bot Pagination And Long Lists

Telegram bot flows must not assume that lists fit into one message or one inline keyboard.

Long or entity-based bot outputs should support pagination, including:

- Client appointment history.
- Future appointment lists.
- Service lists.
- Calendar slot lists.
- Conflict lists.
- Broadcast recipient/filter previews.
- Any inline keyboard where buttons are tied to domain entities.

The bot should respect Telegram message length and inline keyboard limits by splitting long text and button lists into pages with navigation controls.

Default pagination UX:

- Use previous/next navigation buttons.
- Show a page indicator, for example `1/4`.
- Use localized button labels through the multilingual content system.

### Client Data

Default client fields:

- Name.
- Phone.
- Language.
- Booking comment.

Automatically captured Telegram identity data may be shown in the client context:

- Telegram identity.
- Telegram username, if available.
- Telegram Premium status, if available.

Telegram bot reachability, such as whether the client blocked a specific partner bot, is partner/bot-scoped and should not be treated as a global Telegram identity field.

Date of birth is not required by default in MVP.

The partner can require a minimal configurable set of fields for booking. If data is missing, the client fills it during booking or in the client personal area.

### Bot-Mediated Messages

- Client can initiate a message to master from the client personal area.
- MVP messages are scoped to `partner + client`.
- MVP messages do not need to be tied to a specific appointment.
- Master receives client messages through a partner-scoped linked master Telegram contact.
- Master replies through the bot, and the client receives the answer from the partner/master bot.
- Partner/master can see conversations with own clients.
- Superuser may access conversations for support and incident investigation.
- If client blocks the bot, master reply failure is shown as "message not delivered" and the client is marked unavailable for this partner bot.
- MVP does not need a separate "close dialog / do not write me" button.
- Marketing unsubscribe is separate from service/manual messages.

Exact moderation/abuse handling and detailed privacy/support policies remain design questions.

### Master Telegram Contact

- Bot token validation identifies the bot only; it does not identify the master's personal Telegram account.
- Master-facing Telegram delivery requires an explicit partner-scoped master contact binding.
- Binding is created through a signed admin/deep link opened in the partner's own bot.
- A normal client `/start` must never grant master-contact privileges.
- MVP can expose one primary master contact, with room to add extra notification recipients later.

### Multilingual Content

- MVP languages: Ukrainian, Russian, English.
- Ukrainian is default language.
- Every user-facing profile should have a language preference where relevant. This includes partner/master and partner clients.
- If a user has not chosen a language, Ukrainian is used.
- Client can choose language in client personal area or during bot start flow.
- Partner chooses own language in admin and bot personal area.
- System UI/content must be localized for all MVP languages: navigation, buttons, labels, statuses, errors, booking flow, reminders, and service messages.
- Partner-authored content is not automatically translated. Examples: service names, service descriptions if added later, broadcast text, manual messages, comments, and partner-entered free text.
- System default texts and button labels exist for all MVP languages.
- Partner can optionally override system bot templates per language.
- Partner content overrides should be stored as separate records by partner, content key, and language, not as one large partner JSON blob.
- The confirmed conceptual schema is `SystemTemplate(key, language)` plus `PartnerTemplateOverride(partner, key, language)`.
- System templates define default text/label, content type, allowed placeholders, and whether the template is required.
- Partner overrides are optional and language-specific.
- Partner should have broad control over bot content in MVP, including as many user-facing bot messages and button labels as is practical for the initial release.
- MVP editable content should include booking flow, reminders, cancellation/reschedule, client profile, broadcast buttons, contact master, penalties, and no-show related messages/buttons.
- MVP editable content key groups start with: start/welcome, booking, reschedule/cancellation, reminders, client profile, penalties/no-show/late-arrival, client-master messaging, and broadcast buttons.
- Common navigation buttons such as back, next, cancel, confirm, retry, and view details are system-only localized in MVP.
- System error/service texts such as "something went wrong", "slot already taken", and "no available slots" are system-only localized in MVP.
- Partner-editable start/welcome message is included and can be overridden per language.
- Initial editable `start/welcome` keys:
  - `start.welcome` with `{partner_name}`.
  - `start.choose_language` with no placeholders.
  - `start.profile_incomplete` with `{missing_fields}`.
- Initial editable `booking` keys:
  - `booking.category_select` with no placeholders.
  - `booking.service_select` with `{category_name}`.
  - `booking.summary` with `{services}`, `{duration}`, `{service_total}`, `{currency}`.
  - `booking.date_select` with `{booking_horizon_days}`.
  - `booking.time_select` with `{date}`, `{services}`, `{duration}`.
  - `booking.near_fit_notice` with `{tolerance_minutes}`.
  - `booking.final_confirm` with `{services}`, `{date}`, `{time}`, `{duration}`, `{service_total}`, `{currency}`.
  - `booking.requested_created` with `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `booking.confirmed_created` with `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `booking.request_cancelled_by_client` with `{date}`, `{time}`.
  - `booking.request_confirmed_by_master` with `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `booking.request_declined_by_master` with `{partner_name}`, `{date}`, `{time}`, `{reason}`.
- `booking.slot_taken` and "no available slots" remain system-only localized service texts in MVP.
- Ordinary client booking summary/final confirmation does not show penalties unless penalties have already been explicitly applied to that appointment.
- Initial editable `cancellation/reschedule` keys:
  - `appointment.cancel_confirm_prompt` with `{date}`, `{time}`, `{services}`.
  - `appointment.cancelled_by_client` with `{date}`, `{time}`, `{services}`.
  - `appointment.cancelled_by_master` with `{partner_name}`, `{date}`, `{time}`, `{services}`, `{reason}`.
  - `appointment.completed` with `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `appointment.no_show` with `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `appointment.late_cancel_penalty_warning` with `{penalty_amount}`, `{currency}`, `{deadline_hours}`.
  - `appointment.late_cancelled_with_penalty` with `{penalty_amount}`, `{currency}`.
  - `appointment.reschedule_start` with `{date}`, `{time}`, `{services}`.
  - `appointment.reschedule_requested` with `{old_date}`, `{old_time}`, `{new_date}`, `{new_time}`, `{services}`.
  - `appointment.reschedule_auto_confirmed` with `{old_date}`, `{old_time}`, `{new_date}`, `{new_time}`, `{services}`.
  - `appointment.reschedule_proposed` with `{partner_name}`, `{date}`, `{time}`, `{services}`, `{proposal_expires_at}`.
  - `appointment.reschedule_proposal_accepted` with `{new_date}`, `{new_time}`, `{services}`.
  - `appointment.reschedule_proposal_declined` with `{date}`, `{time}`, `{services}`.
  - `appointment.reschedule_proposal_expired` with `{date}`, `{time}`, `{services}`.
- `appointment.late_cancel_blocked` remains system-only localized in MVP.
- Master booking and cancellation/reschedule notifications are partner-editable in MVP:
  - `master.client_booked_appointment` with `{client_name}`, `{client_phone}`, `{date}`, `{time}`, `{services}`, `{comment}`.
  - `master.client_cancelled_appointment`.
  - `master.client_requested_reschedule`.
  - `master.client_accepted_reschedule`.
  - `master.client_declined_reschedule`.
- Cancellation/reschedule `{reason}` is optional; templates must render cleanly when it is empty.
- Initial editable `reminders` keys:
  - `reminder.client_before_appointment` with `{partner_name}`, `{date}`, `{time}`, `{services}`.
  - `reminder.master_before_appointment` with `{client_name}`, `{client_phone}`, `{date}`, `{time}`, `{services}`.
  - `reminder.master_morning_digest_empty` with `{date}`.
  - `reminder.master_morning_digest` with `{date}`, `{appointments}`.
  - `reminder.stale_requested` with `{client_name}`, `{date}`, `{time}`, `{services}`, `{request_age}`.
  - `reminder.failed_delivery_to_master` with `{client_name}`, `{date}`, `{time}`, `{services}`, `{failure_reason}`.
- Repeated stale requested reminders reuse `reminder.stale_requested`; no separate repeated template is needed in MVP.
- `reminder.manual_retry_sent` and `reminder.manual_retry_failed` remain system-only localized in MVP.
- `{failure_reason}` shown to partners must be business-friendly and must not expose raw technical payloads.
- Initial editable `client profile` keys:
  - `profile.main` with `{client_name}`, `{phone}`, `{language}`, `{visit_count}`, `{extra_text}`.
  - `profile.missing_fields_prompt` with `{missing_fields}`.
  - `profile.future_appointments_empty` with no placeholders.
  - `profile.future_appointments_list` with `{appointments}`.
  - `profile.history_empty` with no placeholders.
  - `profile.history_list` with `{appointments}`.
  - `profile.appointment_details` with `{date}`, `{time}`, `{services}`, `{status}`, `{service_total}`, `{penalties_total}`, `{total}`, `{currency}`, `{comment}`, `{reason}`.
  - `profile.penalties_empty` with no placeholders.
  - `profile.penalties_list` with `{penalties}`, `{total_penalties}`, `{currency}`.
  - `profile.contact_master_prompt` with `{partner_name}`.
- `profile.updated_success`, `profile.marketing_unsubscribed`, `profile.marketing_subscribed`, and `profile.contact_master_sent` remain system-only localized in MVP.
- `profile.appointment_details` must be able to show penalties and total when penalties are applied to that appointment.
- Profile detail `{comment}` and `{reason}` are optional; templates must render cleanly when either is empty.
- Initial editable `penalties/no-show/late-arrival` keys:
  - `penalty.created_late_cancel` with `{penalty_amount}`, `{currency}`, `{appointment_date}`, `{appointment_time}`.
  - `penalty.created_no_show` with `{penalty_amount}`, `{currency}`, `{appointment_date}`, `{appointment_time}`.
  - `penalty.created_late_arrival` with `{penalty_amount}`, `{currency}`, `{late_minutes}`, `{appointment_date}`, `{appointment_time}`.
  - `penalty.applied_to_appointment` with `{penalty_amount}`, `{currency}`, `{appointment_date}`, `{appointment_time}`, `{appointment_total}`.
  - `penalty.paid` with `{penalty_amount}`, `{currency}`.
  - `penalty.waived` with `{penalty_amount}`, `{currency}`.
- Initial editable master-facing penalty notification keys:
  - `master.penalty_created` with `{client_name}`, `{penalty_reason}`, `{penalty_amount}`, `{currency}`.
  - `master.penalty_paid` with `{client_name}`, `{penalty_amount}`, `{currency}`.
  - `master.penalty_waived` with `{client_name}`, `{penalty_amount}`, `{currency}`.
- Internal penalty status labels such as `unpaid`, `applied_to_appointment`, `paid`, and `waived` remain system-only localized.
- Technical penalty validation errors, "penalties disabled" explanations, and admin-only correction confirmations remain system-only localized in MVP.
- Template placeholder `{penalty_reason}` must be a localized business label such as "late cancellation", "no-show", or "late arrival", not a raw enum value.
- Initial editable `client-master messaging` keys:
  - `messaging.contact_master_prompt` with `{partner_name}`.
  - `messaging.client_message_received_for_master` with `{client_name}`, `{client_phone}`, `{message_preview}`.
  - `messaging.master_reply_received_for_client` with `{partner_name}`, `{message_text}`.
- Client confirmation after sending a message to the master remains system-only localized in MVP.
- Delivery failure texts for client-master messaging remain system-only localized in MVP.
- Messaging delivery failure reasons shown to partner/client must be business-friendly and must not expose raw Telegram payloads.
- Master-facing new-message notifications may include client phone when it exists, because the partner/master is allowed to see full client phone in their own area.
- Initial editable `broadcast buttons` keys:
  - `broadcast.default_cta_book` with no placeholders.
  - `broadcast.default_cta_my_appointments` with no placeholders.
  - `broadcast.default_cta_external_link` with no placeholders.
- Broadcast message body is partner-authored content, either typed manually for a campaign or reused from the partner's saved broadcast templates.
- The system should not provide generic marketing body defaults for partners in MVP; system defaults are needed for reusable button labels and service messages.
- Broadcast button labels are language-specific partner overrides with the same fallback rule as other editable button labels.
- If a partner leaves a broadcast button label override empty for a language, the system default for that language is used.
- `broadcast.unsubscribe_confirmed` remains system-only localized in MVP.
- Broadcast delivery/statistics status labels and service errors remain system-only localized in MVP.
- Partner-editable button labels are also language-specific.
- Partner is not required to provide overrides for all languages.
- Empty partner override fields mean "use the system default".
- If a partner override exists for the user's language, use it.
- If a partner override does not exist for the user's language, use the system default for that language rather than showing an override written for another language.
- If a system default for the requested language is unexpectedly missing, fallback should use Ukrainian before failing.
- If a partner customizes one language only, the other languages continue to use their own system defaults until explicitly overridden.
- Mixed-language output is acceptable when localized system text includes partner-authored content, such as an English service message containing a Ukrainian service name.
- Admin UI for editable bot content should expose language tabs for Ukrainian, Russian, and English so the partner can edit translations explicitly.
- Admin UI should show system default text/label, allowed placeholders, and a preview/render test for the selected content key/language.
- Admin UI should allow resetting a specific partner override language/text back to the system default.
- Partner template overrides must be validated before saving. Unknown placeholders or render errors block saving with an error.
- Save-time validation should render against a sample context for the selected content key.
- Save-time validation applies to all editable templates and editable button labels.
- Editable button labels must satisfy basic validation such as non-empty text, length limits, and no collisions where labels are used as routed reply actions.
- Runtime delivery should still have a defensive fallback so a bad template cannot break bot processing if invalid content somehow exists.

### Settings Validation And Recovery

- Critical partner settings should show a warning/confirmation before saving. Examples include bot token, auto-confirm/manual-confirm mode, cancellation deadline, penalties, reminder timings, quiet hours, and required booking fields.
- Critical settings use a normal confirmation modal in MVP; typed confirmations such as entering `CONFIRM` are not required.
- Settings screens should show a short local change history for that setting area where useful.
- The full settings history remains in the partner-visible activity log/audit log.
- Partner-editable settings should be validated during save where possible.
- Invalid save-time values should be rejected with a clear validation error instead of saved and handled later.
- Reminder settings, cancellation settings, quiet hours, required booking fields, and other numeric/time settings should use explicit min/max constraints in admin/UI.
- The system should not try to execute nonsensical settings such as reminder timing of `0 minutes` or an extreme value such as `999 days`; those values should be blocked or constrained during configuration.
- If a previously saved setting becomes invalid after a system change, migration, or stricter validation, runtime should use safe defaults/fallbacks where possible and show a warning to the partner in admin.
- Invalid legacy settings must not crash bot runtime or background jobs.

### Reminders And Notifications

- Client reminders are configurable per partner.
- Default client reminders: 24 hours and 2 hours before confirmed appointment.
- Client reminders are sent only for `confirmed` appointments.
- Client reminders are created when an appointment becomes `confirmed`, not while it is still `requested`.
- Master appointment reminders are configurable per partner.
- Default master appointment reminder: 45 minutes before each confirmed appointment.
- Morning master digest is included in MVP.
- Default digest time: 08:00 in partner timezone.
- Digest is sent even if there are no appointments, with "no appointments today" content.
- Quiet hours are required for client and partner notifications.
- Default quiet hours: 22:00-08:00 in partner timezone.
- If a reminder falls inside quiet hours, send immediately after quiet hours end.
- If a quiet-hours-delayed reminder becomes obsolete before quiet hours end, for example because the appointment time has already passed, skip it instead of sending late.
- If an appointment is rescheduled, cancelled, completed, or marked `no_show`, old pending reminders for that appointment must not send.
- If a `requested` appointment remains unprocessed, partner receives stale-request reminders.
- Default stale-request interval: 3 hours.
- Repeated stale-request reminders use decreasing intervals based on the partner-configured interval: configured interval, then half, then half again, until the minimum interval is reached.
- Minimum stale-request repeat interval: 5 minutes.
- Stale-request reminders continue until the request is processed, but never more often than the minimum interval.
- Stale-request reminders respect partner quiet hours.
- Failed client reminder must be visible to partner.
- Failed reminder should become visible to the partner after automatic retries are exhausted, not necessarily after the first send error.
- A finally failed reminder creates a partner bot notification and appointment marker/status.
- Failed reminder notification includes inline actions: retry, write to client, view client.
- If the master presses "retry/resend" after a failed reminder, the system creates a new send attempt on top of the existing delivery history.
- The failed-reminder marker remains visible until a resend succeeds or the partner otherwise handles the situation.
- Failed reminder retry has no limit/cooldown in MVP.

### Cancellation, No-Show, And Penalties

- Partner configures cancellation restrictions, such as "not later than N hours before appointment".
- Partner may configure a late-cancellation penalty.
- Default cancellation deadline for a new partner is 6 hours before appointment.
- Late-cancellation penalties are disabled by default for a new partner.
- If the partner enables late-cancellation penalties, the default penalty amount is `50.00 UAH`, stored as `5000` minor units.
- Client can cancel a `confirmed` appointment only according to the partner-configured cancellation deadline.
- If client tries to cancel after the deadline and partner penalties are enabled, the bot allows cancellation only after showing the penalty warning and asking the client to confirm.
- If client confirms late cancellation with penalties enabled, the appointment becomes `cancelled_by_client` and a penalty is created according to partner settings.
- Only one automatic late-cancellation penalty is created for one cancelled appointment.
- If client tries to cancel after the deadline and penalties are disabled, the bot does not allow self-service cancellation and suggests contacting the master.
- Master can cancel an appointment after the client deadline.
- Master can cancel without penalty or manually create/apply a penalty where appropriate.
- MVP currency is UAH.
- Money amounts must be stored as integer minor units. Example: `500.00 UAH` is stored as `50000`.
- MVP appointment total is `selected_service_price_snapshots_sum + penalties_applied_to_this_appointment`.
- Discounts, promo codes, bonuses, and manual price adjustments are not part of MVP, but are expected future extension areas.
- Late-cancellation penalties are monetary add-ons that can be applied to an appointment total.
- Client and master both see the penalty.
- Multiple penalties accumulate.
- MVP penalty statuses are `unpaid`, `applied_to_appointment`, `paid`, and `waived`.
- If a penalty was applied to an appointment and that appointment is later cancelled before the penalty is paid/settled, the penalty returns to `unpaid`.
- Penalties should be created for a concrete business reason, not as arbitrary free-form charges.
- MVP penalty reasons include late cancellation, no-show, and being late by more than partner-configured X minutes.
- Late-arrival penalty threshold is partner-configurable.
- Default late-arrival penalty threshold is 15 minutes.
- Default no-show penalty amount is `50.00 UAH`, stored as `5000` minor units.
- Default late-arrival penalty amount is `20.00 UAH`, stored as `2000` minor units.
- When master marks `no_show` and penalties are enabled, the system suggests creating a no-show penalty using the default amount.
- For late-arrival penalty, master manually marks lateness, enters minutes and optional reason, and the system suggests the default late-arrival penalty if the lateness exceeds the configured threshold.
- If penalties are enabled, the client personal area shows active unpaid penalties separately from appointment history.
- The master chooses which unpaid penalties to apply to the current appointment.
- Master separately marks whether a penalty is paid.
- Penalty payment/waiver is managed from the client penalty/debt context, while appointments show applied penalties that affected their total.
- Master can manually waive/remove a penalty.
- Client does not need penalty-specific messaging in MVP; the general "contact master" action is enough.
- Client-facing price display should be detailed: service price, penalties, and final total.
- Penalties appear as one separate combined line in income/revenue reports.
- No-show marker is required in MVP.
- No-show and late-arrival penalties are part of MVP when penalties are enabled.
- Advanced no-show consequences beyond penalties, such as prepayment requirements, are outside MVP.

### Broadcasts

- Partner can create broadcasts in MVP.
- Broadcast filters include:
  - All clients.
  - Clients who have not visited for a partner-defined period.
  - Clients who had a specific service.
  - Clients with future appointment.
  - Clients without future appointment.
- Broadcasts can be sent immediately or scheduled for later.
- An immediate broadcast starts as `queued`; the system then sends recipient messages gradually in the background.
- Broadcast recipients must exclude clients who unsubscribed from marketing broadcasts.
- Client can unsubscribe from marketing while still receiving service notifications.
- Broadcast content supports text, photo, and buttons.
- MVP button types are enough with Book, My appointments, and external link.
- Partner can compose broadcasts manually.
- Partner can save reusable broadcast templates.
- No strict business limit such as one broadcast per day is required in MVP.
- Broadcast sending must respect Telegram limits and possible `429` responses.
- Temporary Telegram errors and `429` should not fail the whole broadcast. The system should back off and continue later.
- Large broadcasts may take a long time to send.
- Broadcast delivery should have per-recipient delivery records for statistics.
- MVP per-recipient broadcast delivery statuses: `queued`, `sent`, `failed`, `skipped`.
- If a client unsubscribes from marketing or blocks the bot after broadcast creation but before their send turn, that recipient delivery should become `skipped`, not `failed`.
- Detailed per-recipient delivery records should not be stored indefinitely; default retention is 90 days.
- Manual retry of failed broadcast recipients is outside MVP unless real usage shows it is needed earlier.
- Do not promise `delivered` if Telegram does not provide reliable delivery confirmation.
- If a client blocks the bot, mark them unavailable for broadcasts while retaining profile/history.

Exact pacing/rate-limit strategy remains a technical design question. Broadcast lifecycle events are part of partner-visible audit in MVP.

### Retention And Cleanup

- Business records such as clients, appointments, penalties, visit history, and partner data are retained indefinitely while the partner is active unless a later legal/product policy says otherwise.
- Noisy technical records should not be stored forever.
- Cleanup-eligible records should use a `retain_until` field to mark the date/time after which the system may delete them.
- `retain_until` is preferred over `expires_at` for cleanup records because the record is not necessarily invalid after that time; it is just no longer worth keeping.
- Cleanup-eligible records include detailed per-recipient broadcast delivery records, delivery attempt records, partner-visible technical logs, and platform/system logs.
- Default retention for cleanup-eligible records is 90 days.
- By default, `retain_until` should be set to `created_at + 90 days` unless a specific record type later receives a different approved policy.

### Audit Logs

- Audit log is required in MVP.
- Audit log is partner-scoped.
- Partner sees own-area logs.
- MVP should provide a separate partner-visible "Activity log" / "Journal" screen.
- Appointment/client cards may also show related local history, but the standalone audit screen remains the primary full view.
- Superuser sees all logs.
- Required audit events:
  - Appointment creation.
  - Appointment update.
  - Appointment cancellation.
  - Appointment status change.
  - Penalty creation/update/payment/waiver.
  - Expense creation/update/deletion.
  - Broadcast creation/scheduling/start/cancellation/completion/failure.
  - Reminder send/failure.
  - Client-master message.
  - Partner bot/settings changes.
- Sensitive/destructive partner actions must be audited, including appointment cancellation, no-show marking, penalty changes/payment/waiver, expense changes/deletion, bot token/settings changes, and corrections after final status.
- Partner-visible logs contain only partner-area events.
- Partner-visible Activity Log entries must be business-friendly and must not expose raw technical payloads.
- Internal technical payloads and platform-wide diagnostics are platform-only.
- Partner-visible business audit logs are retained for 6 months by default.
- Platform/system logs include technical errors from bot runner and background jobs, with sensitive data masked.
- Platform/system logs follow cleanup retention unless a later approved policy overrides it.
- Audit reason is required for correction-flow actions after a final appointment status.
- For master cancellation, penalty waiver, and expense deletion, reason is optional in MVP unless a later policy makes it required.

### Sensitive Data

Sensitive data includes:

- Bot token.
- Client phone.
- Client-master messages.
- Master private notes.

Bot tokens must be stored encrypted. After save, the full bot token is not shown in UI; only safe status or masked representation is shown.

Partner/master can see full phone number when needed in own client context. Phone numbers should not be unnecessarily exposed elsewhere.

Master private notes are operationally sensitive. Partner/master sees them in normal product flows. Superuser may technically access them when needed, but this should be treated as exceptional/support/debug access and not used casually.

Client-master conversations are operationally sensitive. Superuser may access them only for support and incident investigation, not as a casual/default working screen.

Sensitive values may appear in logs only in masked/redacted form. Bot tokens, phone numbers, message text, and private notes must not be written to technical logs or plain errors in raw form.

MVP does not require audit events for every superuser conversation view. This can be added later if support/privacy requirements become stricter.

MVP has no data export. Future sensitive data export requires a separate approved decision.

Appointment/request comment is visible to client and master.

Visit history and penalties are not treated as highly sensitive by default, but still belong to partner-scoped client/business data.

### Retention And Suspension

- Client and appointment data are stored indefinitely while partner is active.
- If a partner disconnects, stops paying, or is suspended/frozen, bot stops.
- Suspended/frozen partner data remains stored.

## MVP Readiness Criteria

MVP is ready when:

- Platform admin can connect a partner and bot.
- Partner can configure services, prices, schedule, bot behavior/texts, reminders, and partner-scoped settings.
- Partner sees only own data.
- Client can register/use bot, book, cancel, and reschedule.
- Partner can create, process, cancel, reschedule, complete, and mark no-show for appointments.
- Notifications/reminders work for clients and partners.
- Failed reminder visibility and retry actions work.
- Stale `requested` reminders work.
- Morning digest works.
- Client personal area works.
- Basic broadcasts work within approved MVP boundary.
- Audit log records required events.

## First Release Validation

The product origin is based on TableOS analogy, Botera ecosystem strategy, and informal positive feedback. During MVP, validation should focus on whether solo masters:

- Actually use the bot/admin daily.
- Trust automatic booking availability.
- Keep services/schedule up to date.
- Rely on reminders.
- Use client history and messages.
- Send broadcasts.
- Feel that BoteraMaster replaces manual Telegram notes/messages/calendar work.
