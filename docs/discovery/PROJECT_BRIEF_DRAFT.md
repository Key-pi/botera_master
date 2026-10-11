# BoteraMaster Project Brief Draft

Status: discovery approved on 2026-08-07.

## Discovery Checkpoint For Approval

Status: approved discovery checkpoint, not final specification.

### Whole Product Picture

BoteraMaster is a Telegram-first scheduling, client-management, and communication service for solo beauty masters. The first market is individual masters who work by appointment: nails, brows, lashes, hair, cosmetology, massage, tattoo, makeup, depilation, and similar appointment-based services.

The platform hides infrastructure from the partner. A partner/master does not manage servers, hosting, workers, bot runtime, or technical setup. The partner provides a Telegram bot token, and BoteraMaster runs that partner's bot through polling. Each partner has their own bot; the shared-platform-bot model is not the MVP direction.

The main value is not one isolated feature, but a connected bundle that feels predictable: clients can book and manage visits through the master's bot, the master sees and controls their own schedule/client base, reminders work, communication happens through the bot, and the partner can adjust bot behavior/texts/settings without touching global platform behavior.

The product origin is strategic as well as practical: while building TableOS, the user recognized that the same partner-owned bot/service model could be useful for masters and appointment-based personal services. This also led to a broader Botera ecosystem idea, where several vertical products may later share ecosystem-level mechanics such as promotions, bonuses, and partner programs across businesses.

### Proposed MVP Boundary

MVP should include:

- Partner setup with one owner/master role.
- Superuser-controlled partner onboarding: superuser creates partner and admin access; public/self-service partner registration is outside MVP.
- Partner must complete minimum setup before bot launch: bot token, language, timezone, at least one service category/service, at least one availability window, and booking confirmation mode.
- Setup should have a draft/setup-required state so the bot does not run for clients before required setup is complete.
- One Telegram bot per partner, with partner-provided token and safe token visibility after save.
- Partner-scoped admin isolation: partner sees only their own data; superuser sees all.
- Partner management of services, prices, durations, schedule, blocked time, appointments, clients, bot texts, button labels, language, reminders, quiet hours, required booking fields, and cancellation rules.
- Client bot flow for registration/profile data, service selection, date/time selection, booking request, confirmation/cancellation/rescheduling, and personal area.
- Client personal area with profile/card, editable data, visit count, additional free-text field, future appointments, cancellation, rescheduling, full history, and contact with master through bot-mediated messages.
- Appointment statuses: `requested`, `confirmed`, `cancelled_by_client`, `cancelled_by_master`, `completed`, `no_show`.
- Auto-confirmed and manually confirmed booking modes.
- Protection against overlapping confirmed/accepted appointments.
- Snapshot of service price and duration at booking time.
- Schedule-conflict handling when the partner changes schedule: conflict list, leave as is, propose reschedule, cancel, message client, and bulk conflict actions.
- Multilingual bot behavior in Ukrainian, Russian, and English, with Ukrainian as default.
- System default texts/button labels per language and partner overrides per language with fallback to defaults.
- Configurable client reminders, default 24 hours and 2 hours before confirmed appointment.
- Configurable master appointment reminders, default 45 minutes before each confirmed appointment.
- Morning digest for master, default 08:00 partner timezone, including "no appointments today" when empty.
- Stale `requested` reminders for unprocessed booking requests, default after 3 hours with decreasing repeat interval and 5-minute minimum.
- Quiet hours for clients and partners, default 22:00-08:00 partner timezone; reminders delayed until quiet hours end.
- Failed reminder visibility for master with appointment marker and inline actions: retry, write to client, view client.
- UAH-only money handling for MVP.
- Configurable late-cancellation penalty that accumulates, is added to the next appointment total, is visible to client and master, can be marked paid separately, and can be waived by master.
- No-show marker.
- Income/reporting view where penalties appear as a separate line.
- Basic partner broadcasts in MVP: filters, immediate or scheduled send, text/photo/buttons, reusable templates, unsubscribe from marketing, queued/sent/failed statuses, unavailable-client marking when bot is blocked.
- Partner-scoped audit log for appointment changes, status changes, penalties, expenses, broadcasts, reminder send/failure events, client-master messages, and partner bot/settings changes; superuser can see all.
- MVP should provide a separate partner-visible activity log/journal screen, while appointment/client cards may also show related local history.
- Indefinite data retention while partner is active; suspended/frozen partner bot stops, data remains.
- Initial load target benchmark: 50 partners, 500 appointments/day total, 10 active bots, while keeping room to scale higher later.

### Explicitly Outside MVP

- Studios/multi-master organization support.
- Employee roles and granular partner-side permissions.
- Online payments.
- Manual prepayment by requisites.
- Reviews.
- Loyalty and bonuses or promo.
- Google Calendar sync.
- Self-service client data deletion request through bot.
- Data export.
- Repeat previous service.
- Advanced no-show consequences beyond MVP penalties, such as prepayment requirements.
- Cross-product Botera partner program, shared bonuses, and ecosystem-level promotions.

### Gaps And Risky Assumptions

- The product problem is based on analogy with TableOS, ecosystem strategy, and informal positive feedback, but still lacks a concrete observed "before BoteraMaster" scenario from a master.
- MVP scope is broad. Booking, reminders, multilingual text overrides, penalties, broadcasts, audit, and bot-mediated messaging together form a substantial first release.
- TableOS is a useful conceptual reference for partner/bot/settings/admin scoping, but BoteraMaster has time-slot conflicts, appointment lifecycle, reminders, and money behavior that should not be copied blindly from TableOS business logic.
- Future studios may pressure the tenant model. MVP says partner equals solo master, but code/domain language should not make future expansion unnecessarily painful.
- Multilingual partner-edited content needs an exact editing/storage/validation design later.
- Broadcast rate limiting across many partner bots needs a precise technical design later.
- Bot-mediated messaging needs exact rules for context, failure handling, privacy, and support visibility.
- Sensitive-data handling is directionally confirmed, but exact storage, masking, audit, and operational-access rules still need technical design.
- Exact platform-only internal audit/diagnostic taxonomy still needs technical design.

### Approval Decisions Needed

Before moving from discovery toward final specification and architecture, the user needs to approve or change:

- Whether this whole-product picture is accurate.
- Whether this broad MVP boundary is accepted knowingly.
- Whether the listed outside-MVP items are correct.
- Whether TableOS should remain a reference source only, not an automatically copied architecture.
- Whether unresolved gaps can move into future architecture/design questions rather than blocking discovery approval.

## CONFIRMED

### Product Intent
- BoteraMaster is envisioned as a Telegram-first CRM and scheduling assistant for individual service masters and small service businesses.
- The initial focus is beauty specialists who work by appointment.
- The product should reduce appointment chaos, missed visits, manual reminders, and fragmented client information.
- The experience should feel lightweight and simple, closer to a personal assistant than a heavy CRM.

### Target Users
- Initial target: individual beauty masters working by appointment.
- First MVP user is a solo beauty master without a team.
- In MVP, a partner is understood as a solo master.
- Product naming differs by audience: clients see "master"; the platform/back-office sees "partner".
- Internal future documentation/code naming should use `partner` for the tenant boundary.
- Examples: brow masters, lash masters, manicure masters, hairdressers, barbers, cosmetologists, massage therapists, tattoo artists, makeup artists, depilation masters.
- Future expansion may include small studios and other service businesses.

### Core Product Areas Mentioned
- Client self-booking through a Telegram bot.
- Each partner/master should have their own Telegram bot.
- The master gives BoteraMaster the bot token; BoteraMaster operates the bot through polling.
- If a partner strongly wants multiple bots, connecting more than one bot can be considered as a paid/custom option.
- Master administrative workflows through Telegram and an admin panel.
- Services with name, price, and duration.
- Work schedule and blocked time.
- Appointment creation, confirmation, cancellation, completion, and no-show marking.
- Client database with visit history, notes, cancellations, no-shows, and approximate revenue.
- Reminders for clients and masters.
- Messages/notifications to users should work in the expected, predictable way.
- Client personal area is for the partner's clients and should not feel impoverished, even if it is described as "small".
- In MVP, the client personal area should include a client card/profile, client data display/editing, visit count, and an additional free-text field.
- The client can enter or edit data that was not collected earlier.
- Repeat visit prompts in the future.
- Broadcasts/messages to clients are needed in MVP.
- Flexible per-partner settings for behavior and bot texts.
- Translations are required, but scope and languages are not yet defined.
- Bot multilingual behavior is required in MVP: the client chooses language in the client personal area, and the bot communicates with that client in the selected language.
- The partner chooses their own language in the admin panel and should also be able to change it in their bot personal area.
- Default MVP language is Ukrainian.
- Russian and English are available language choices in MVP.
- System default texts and button labels should exist for Ukrainian, Russian, and English.
- Partner can override texts and button labels per language; if an override is empty, the system uses the default text/label for that language.

### Initial Client Scenario
- Client opens the master Telegram bot.
- Client chooses to book.
- Client selects a service category first, then one or more services, then date and available time.
- Selected services may come from different service categories.
- Service order inside one appointment is not business-significant in MVP; selected service prices and durations are summed.
- MVP should not enforce a hard count limit for services in one appointment, as long as the combined duration can be handled by booking availability and bot UX.
- Service selection in the client booking flow should use a multi-select/checkbox-like UX.
- Service selection screen needs actions: done, clear selection, and back.
- If the client goes back from summary/service selection, the current service selection is reset.
- Unfinished booking selections are not restored later in MVP; when the client starts booking again after abandoning the flow, they start from scratch.
- Before choosing time, the client sees a booking summary with selected services, total duration, and total amount; categories do not need to be shown in the summary.
- From the booking summary, the client can remove selected services before choosing time.
- If a selected service becomes inactive or changes while the client is still inside the booking flow, the bot revalidates the selection and asks the client to review/reselect before proceeding.
- After summary, client selects a date first, then a time slot for that date.
- Date selection uses a hybrid Telegram UX: show 7 nearest available dates at a time with a "next dates" action, plus month selection for farther booking.
- Month selection shows only dates with available slots.
- If the selected date has no slots for the selected services, bot shows a no-slots message and actions: choose another date, write to master, or change services.
- Time slots for a selected day are paginated when there are many slots, roughly 8-10 slots per page.
- Near-fit slots are shown in the same slot list as normal slots, with a marker that master confirmation is required.
- Selecting a slot does not create an appointment immediately.
- After selecting a slot, client sees a final confirmation summary with selected services, date/time, duration, total amount, and relevant markers such as near-fit or penalties.
- The appointment is created only after the client explicitly confirms the final summary.
- Client booking comment is optional and must not block booking when empty.
- At final confirmation, the system re-checks slot availability before creating the appointment.
- If the slot became unavailable during final confirmation, bot shows that the slot is no longer available and returns the client to time selection.
- Inactive services are hidden from client booking flow, but existing appointments/history with those services remain visible.
- Slot availability is based on the full total duration of selected service(s), availability windows, blocked time, and existing confirmed appointments.
- Near-fit tolerance is included in MVP: a slot may exceed the end of an availability window by the partner-configured tolerance.
- Default near-fit tolerance is 10 minutes.
- Near-fit tolerance applies only to exceeding the end of an availability window; it does not allow overlap with blocked time or an existing confirmed appointment in MVP.
- Near-fit slots are shown to the client with a marker that master confirmation is required.
- Near-fit bookings always start as `requested`, even if the partner uses auto-confirmation.
- Near-fit client-facing text must explain that the selected slot requires master confirmation.
- If no slots are available for the selected service(s), the bot shows that there are no suitable slots in the searched/nearest period and suggests contacting the master.
- Bot asks for name and phone if missing.
- Required client data for booking should be configurable per partner, because different partners may want different data collected before booking.
- System creates a booking request.
- If the partner uses manual confirmation, a client booking starts as `requested`.
- If the partner uses auto-confirmation, a client booking starts as `confirmed`.
- If auto-confirmation creates a `confirmed` appointment, client immediately receives a booking confirmed message, and the master receives a notification about the new confirmed appointment.
- If manual confirmation creates a `requested` appointment, client receives a message that the request was sent and is waiting for master confirmation.
- For a `requested` appointment, the master receives inline actions: confirm, decline/cancel, or propose another time.
- After creating a `requested` appointment, client immediately has an action to cancel the request.
- When master confirms a `requested` appointment, appointment becomes `confirmed`, client receives a confirmed message, and reminders are created.
- When master declines/cancels a `requested` appointment, client-facing reason is optional.
- When master proposes another time for a `requested` appointment, master may propose multiple alternative slots.
- When master proposes alternative slots for a `requested` appointment, the original appointment remains `requested` until the client chooses a slot, cancels the request, or the proposal expires.
- If the client chooses one of the master-proposed slots, the system re-checks availability, then the appointment becomes `confirmed` and reminders are created.
- If none of the proposed slots works for the client, the client may choose another available time; that choice follows the partner's normal auto-confirm/manual-confirm mode.
- Requested appointment alternative proposals expire after 48 hours.
- If a requested appointment alternative proposal expires, the appointment is automatically cancelled and both client and master are notified.
- If client cancels a `requested` appointment, master receives a notification that the request was cancelled by the client.
- If the partner uses manual confirmation, multiple `requested` bookings may exist for the same slot and the master can choose how to handle them.
- When the master confirms a `requested` booking, the system must re-check availability. If the slot is already occupied by a confirmed appointment, the request cannot be confirmed as-is.
- If the partner uses auto-confirmation, a booking starts as `confirmed` and blocks availability immediately.
- Master receives a notification with inline actions: accept, reject, or suggest reschedule.
- Client receives confirmation or cancellation information after master's action.
- Client should be able to see future appointments.
- For MVP, client cancellation is desired.
- Partner can create manual appointments for clients without Telegram, using name and/or phone.
- Manual clients still get partner-scoped client profiles without Telegram identity.
- Manual appointment creation from Calendar starts with selected time/range first, then client and service details.
- If no matching client exists, the partner can quickly create a manual client inside the appointment modal/panel.
- Manual appointments are `confirmed` by default.
- Manual appointments for Telegram-linked clients should send a Telegram notification.
- Manual appointments may be created outside availability windows with a warning, but still cannot overlap another `confirmed` appointment.
- Telegram client reminders are unavailable for manual appointments until a Telegram identity is linked, but master reminders and morning digest still include manual appointments.
- If a Telegram user later appears with the same phone as an existing manual client profile for that partner, the system should suggest linking/merging to the master.
- Linking/merging manual client data requires master confirmation and must not happen silently.
- Manual clients without phone are not auto-linked later.
- In MVP, the client personal area includes future appointments and cancellation.
- In MVP, the client personal area includes full visit/appointment history, including `completed`, `cancelled_by_client`, `cancelled_by_master`, and `no_show` records.
- If the master provides a cancellation or reschedule reason, the client sees it in the bot.
- MVP does not require a separate private cancellation/reschedule reason hidden from the client.
- A client can see when an appointment is marked `no_show`.
- If penalties were applied to an appointment, the client history shows the penalty line and final total.
- For manual clients without Telegram identity, appointment history is visible only to the partner/admin until the client profile is linked to a Telegram identity.
- Long bot responses and entity-bound inline keyboards must be paginated, including client history and other lists that can grow large.
- Client history in the bot is sorted newest-first, shown as short cards, opens full details by button/action, and defaults to 5 records per page.
- Telegram pagination uses previous/next navigation and a page indicator such as `1/4`, with labels localized through bot content.
- Client appointment details show service, date/time, status, service price, applied penalties, final total, client/request comment, and cancellation/reschedule reason when provided.
- Master appointment details show the client-visible appointment information plus client phone and private master note.
- Future `confirmed` appointments allow client actions: cancel, reschedule, and contact master.
- `requested` appointments can be cancelled by the client before master response.
- Completed, cancelled, and no-show appointments are read-only for the client.
- MVP does not auto-complete appointments; after appointment time, master manually sets `completed` or `no_show`.
- When master sets `completed`, master can mark applied penalties as paid.
- When master sets `no_show` and penalties are enabled, the system suggests creating a no-show penalty with the default amount.
- For late-arrival penalty, master manually marks lateness, enters minutes and optional reason, and the system suggests the default penalty if lateness exceeds the partner-configured threshold.
- Client receives notifications when master marks `completed`, marks `no_show`, or creates a penalty.
- Master-initiated reschedule is sent to the client as a proposal, not silently applied.
- When master proposes alternative slots, the existing appointment remains `confirmed` until the client chooses a new slot or another final action happens.
- A master proposal may include multiple concrete alternative slots; if they do not fit, the client can choose another available slot or cancel.
- Client-initiated reschedule follows the partner's auto-confirm/manual-confirm setting, same as a new booking.
- If the partner uses auto-confirmation, a client-initiated reschedule is auto-confirmed after availability is re-checked.
- If the partner uses manual confirmation, a client-initiated reschedule requires master confirmation.
- Even when a client-initiated reschedule is auto-confirmed, the master receives a notification.
- Master-initiated reschedule proposal lifetime defaults to 48 hours.
- If a master-initiated reschedule proposal expires, the old appointment remains `confirmed`, and the client must request reschedule again if still needed.
- In MVP, the client personal area includes a way to contact the master by sending a message through the bot.
- Master should be able to reply to client messages through the bot as well.
- Client-to-master messages can be initiated from the client personal area without being tied to a specific appointment.
- MVP client-master conversations are scoped to `partner + client`.
- Master receives client messages through the partner bot/admin Telegram context, and the client receives replies from the partner/master bot.
- If client blocks the bot, master reply failure is shown as "message not delivered" and the client is marked unavailable for this partner bot.
- MVP does not need a separate "close dialog / do not write me" action; marketing unsubscribe is separate from service/manual messages.
- "Repeat previous service" is not required in MVP and can be added later.

### Initial Master Scenario
- Master can view today's, tomorrow's, and upcoming appointments.
- Master can manually add an appointment.
- In manual appointments, the master can use a one-off custom service that is not saved in the partner-defined service list.
- In manual appointments, the master can override the selected service price and/or duration only for that appointment.
- One-off custom services in manual appointments appear in client history as normal appointment service lines.
- Master can confirm, cancel, complete, and mark no-show.
- Master can view clients.
- Master can add services and change service price/duration.
- Services are fully partner-defined; BoteraMaster does not provide a shared/global service catalog for masters.
- Partner-defined services have categories in MVP.
- Service categories are manually sorted by the partner.
- Service categories can have an optional description.
- If a service category is inactive, services inside it are hidden from client booking flow, while existing appointments/history remain visible.
- Partner-defined services can have an optional short client-facing description.
- Service price can be `0.00`, for example for a free consultation.
- Partner can control service display order for clients.
- Client-facing service lists in the bot must be paginated when they grow large.
- If a manual appointment uses overridden price and/or duration for a partner-defined service, the client sees a marker that the value was changed by the master.
- Master can configure work time, days off, and blocked time.
- Availability windows are the primary source of bookable time; clients see slots only inside partner-created availability windows.
- The partner should have a convenient calendar-like admin experience for filling weekly/monthly schedule and available hours; the confirmed interaction direction exists, while exact visual treatment and polished component behavior remain open.
- Main calendar view defaults to week view and supports day/week/month switching.
- Availability windows can be created by selecting a time range or through quick manual input: date, start time, end time.
- Calendar range selection also supports creating blocked/unavailable time and manual appointments.
- Availability windows, blocked time, appointments, conflicts, and near-fit/request markers should be visually distinguishable by type in the calendar.
- Clicking an appointment in Calendar opens a detail panel/modal with actions to edit, propose reschedule, cancel, mark completed, mark no-show, and message client.
- Calendar supports quick copy actions such as copy day and copy week in addition to saved availability templates.
- Calendar admin should be usable on mobile with adapted interactions, while dense editing may remain more comfortable on desktop/tablet in MVP.
- Multiple availability templates per partner are required in MVP, and partners can apply templates to future periods.
- Applying a template should preview conflicts with existing confirmed appointments before applying.
- Partner can block/unavailable time inside an available window.
- Blocked/unavailable reason text is optional.
- Booking horizon is partner-configurable.
- Partner can create client broadcasts in MVP.

### Broadcasts
- MVP broadcasts should support filters, not only "send to all".
- MVP broadcast filters should include: all clients, clients who have not visited for a long time, clients who had a specific service, clients with a future appointment, and clients without a future appointment.
- Broadcasts can be sent immediately or scheduled for later, based on partner choice at creation time.
- Clients can unsubscribe from marketing broadcasts while continuing to receive service notifications about appointments.
- Broadcast content should be richer than plain text: text, photo, and buttons should be supported.
- A "Book" button in a broadcast is desired.
- MVP broadcast buttons are enough with: "Book", "My appointments", and external link.
- Partner should not have a strict business limit like "one broadcast per day" in MVP.
- Broadcast delivery must still respect Telegram limits and handle Telegram rate limiting, including possible `429` responses. If there are many recipients, delivery may take a long time.
- Immediate broadcasts start as `queued` and are sent gradually in the background.
- Broadcast sending should be paced per partner bot and should not send faster than one message every 0.5 seconds per bot.
- Temporary Telegram errors and `429` should not fail the whole broadcast; the system should back off and continue later.
- Partner should see broadcast statistics. MVP per-recipient statuses should be `queued`, `sent`, `failed`, and `skipped`; `delivered` should not be promised if Telegram does not provide reliable delivery confirmation.
- Partner should see visible campaign progress while a long broadcast is still sending.
- If broadcast sending is interrupted by a worker crash/restart, it should resume from unsent recipients without duplicate sends for recipients already recorded as completed.
- If a client unsubscribes from marketing or blocks the bot after broadcast creation but before send, that recipient delivery should become `skipped`.
- Detailed per-recipient broadcast delivery records should have limited retention; default retention is 90 days.
- Manual retry of failed broadcast recipients is outside MVP unless real usage shows it is needed earlier.
- Broadcasts can be composed manually.
- Partner should be able to save a broadcast as a reusable template.
- The "long-absent clients" filter period is chosen by the partner.
- The system should support sending broadcasts with buttons generally; exact button types can be expanded over time.
- Broadcast recipients should include only clients who have not unsubscribed from marketing broadcasts.
- If a client blocks the bot, the client should be marked unavailable for broadcasts, while their profile and history remain stored.
- Broadcast lifecycle events should be part of partner-visible audit in MVP.

## ASSUMPTION

- MVP likely starts with one master per partner rather than full studio support.
- Studio/multi-master support is a future direction, not initial MVP.
- Beauty segment is the first vertical because the pain is clearest.
- Telegram is the primary client and master interaction channel.
- Django and aiogram 3.x are the preferred reference direction for the MVP stack after inspecting TableOS, unless implementation reveals a concrete blocker. This still does not create final dependencies before technical approval.
- TableOS is available as a reference project with a similar product idea in another market. Its `bot`, `core`, `apps/partners`, and `apps/users` areas have been reviewed as reference patterns for partner setup, bot setup, bot texts, configurable bot behavior, identity split, and admin scoping.
- The user sees BoteraMaster as similar in spirit to TableOS: the customer does not deal with servers, hosting, or technical setup.
- The idea for BoteraMaster came from building TableOS and recognizing that a similar partner-owned bot/service model could fit masters and appointment-based services.
- BoteraMaster is part of a broader Botera ecosystem idea. In the future, multiple vertical products may share ecosystem-level features such as promotions, bonuses, and partner programs between different partner types.
- TableOS has relevant existing patterns: encrypted bot tokens, active polling bot instances, multi-bot polling runner shape, runtime config validation, duplicate-token safety, partner runtime middleware/context, partner-specific button/text behavior, safe template fallback, and admin data scoping by partner.
- Bot configuration should be split conceptually into Bot Instance and Bot Settings.
- Bot Instance represents technical Telegram connection/runtime state: token, operational status, runtime eligibility, last error, and health/activity.
- Bot Settings represent partner-controlled behavior/content: languages, texts, buttons, reminders, quiet hours, booking rules, required fields, cancellation rules, and penalties.
- Bot/settings configuration is admin-only in MVP, not controlled through the Telegram bot.
- Admin UX should expose one main `Bot` section with tabs: Instance, Settings, Content, Notifications, Booking, and Penalties.
- Partner should see bot health/status in admin, including operational status, last startup/runtime error when present, and last successful polling/activity timestamp when available.
- Partner can switch their own bot between `active` and `paused`; `suspended` is superuser-only.
- When status is `error`, partner can trigger an admin connection/configuration check to revalidate token/configuration and see the current error.
- In MVP, because runtime does not hot-reload, a successful check may still require runner restart/redeploy before live polling actually changes.
- The user's direct market validation so far is informal: the idea received a positive reaction, but no concrete observed "before BoteraMaster" scenario from a master has been provided.
- TableOS `apps/users` is a useful reference for separating a global Telegram identity from partner-scoped customer profiles, but its employee/admin-access model may be more complex than BoteraMaster MVP needs.
- TableOS bot content is a useful reference: default texts/button labels, partner overrides, and safe fallback behavior when a partner-defined template is invalid. TableOS does not currently cover translations, so BoteraMaster needs a multilingual extension to that idea.
- TableOS bot token validation is a useful reference: token format validation on save plus active runtime validation before polling starts.
- BoteraMaster should not copy TableOS hospitality-specific handlers/business logic, menu/cart/orders/tables/billing flows, `AdminAccessProfile` / section-permission complexity, single large content settings structure, global `is_blocked`, or per-partner file logging as MVP defaults.
- Obviously invalid bot tokens should be blocked at save time. Tokens that pass save-time validation but fail Telegram/runtime startup should put the bot into `error`.
- Critical partner settings require warning/confirmation before saving.
- Critical setting warnings use normal confirmation modals; typed confirmations are not required in MVP.
- Settings screens may show short local change history, while full settings history remains in the partner-visible activity/audit log.

### Roles And Access
- Client-facing language should refer to a master.
- Platform/back-office language may refer to a partner.
- MVP partner onboarding is controlled by superuser.
- Superuser creates the partner and partner admin account, then gives login/password to the partner.
- Public/self-service partner registration is outside MVP.
- Partner-scoped settings and data should be accessible to the partner/master.
- Global application settings should be available only to the superuser/platform admin.
- In MVP, the partner admin role can be a single owner/master role; detailed partner-side roles can be handled later.
- The partner can manage their own partner-scoped settings.
- The partner can manage their own bot token.
- If changing a setting affects only the current partner's behavior/data, it can be partner-editable.
- If changing a setting affects the application globally, root behavior, or multiple partners, it is superuser-only.

### Required Partner Bot Settings Mentioned
- Minimum setup before bot launch: bot token, language, timezone, at least one service category/service, at least one availability window, and booking confirmation mode.
- Other settings can start from defaults at launch.
- Setup should have a draft/setup-required state so the bot does not run for clients before required setup is complete.
- Message texts.
- Button labels.
- Auto-confirmation versus manual confirmation of appointments.
- Reminder timings and notification behavior.
- Client reminder intervals are configurable by the partner in MVP.
- Default client reminders for a new partner are enabled at 24 hours and 2 hours before the appointment.
- Master reminders before appointments are configurable by the partner in MVP; a partner may enable/disable them and choose timing.
- Default master reminder before an appointment is 45 minutes.
- Morning daily appointment digest for the master should be included in MVP as a simple message.
- Morning digest time is configurable by the partner in bot settings.
- Default morning digest time is 08:00 in the partner timezone.
- If the master has no appointments today, the morning digest should still be sent with a "no appointments today" message.
- Master reminders before appointments are sent for every `confirmed` appointment when enabled.
- Client reminders are sent only for `confirmed` appointments, not for `requested` appointments.
- If a `requested` appointment remains unprocessed longer than a partner-configured interval, the bot reminds the master that a client wants to book and the request is still unhandled.
- Default unprocessed `requested` reminder interval is 3 hours.
- Repeated reminders for unprocessed `requested` appointments should use a decreasing interval pattern: first after the configured interval, then after half of that interval, then after a quarter of that interval. For the default 3-hour interval, repeats would be after 3 hours, then 1.5 hours, then 45 minutes.
- Repeated reminders for unprocessed `requested` appointments should respect partner quiet hours.
- Quiet hours are required in MVP for both partner/master notifications and client notifications.
- Default quiet hours for both clients and partners are 22:00-08:00 in the partner timezone.
- If a reminder falls inside quiet hours, it should be sent immediately after quiet hours end.
- Minimum interval for repeated unprocessed-request reminders is 5 minutes.
- Repeated stale `requested` reminders continue until the request is processed, but respect quiet hours.
- Client reminders are created only when an appointment becomes `confirmed`.
- Old pending reminders are invalidated/skipped when an appointment is rescheduled, cancelled, completed, or marked `no_show`.
- Quiet-hours-delayed reminders are skipped if they are obsolete by the time quiet hours end.
- If a client reminder is not delivered after automatic retries are exhausted, the master sees a bot notification and the appointment shows a "reminder not delivered" status/marker.
- Failed reminder notification should include inline actions: retry sending, write to client, and view client.
- Failed reminder notifications should be shown after automatic retries are exhausted where practical.
- Failed reminder retry can be triggered without a limit/cooldown.
- Client business event notifications are required for appointment created/confirmed, appointment change proposed, change accepted/declined/expired, appointment cancelled, appointment completed, no-show marked, and penalty created/paid/waived.
- If a client business event notification cannot be delivered because the client blocked the bot, the event is logged, the client is marked unavailable for this partner bot, and the master sees a delivery failed marker/notification where action is useful.
- For manual clients without Telegram identity, Telegram notifications are not created; partner admin should clearly show that there is no Telegram connection for that client.
- Failed appointment change proposal delivery should expose a manual notify/resend action for the master.
- Whether phone number is required.
- Client data fields required for booking.
- If a partner marks a field such as phone as required, the booking flow stops and asks the client to fill that field before continuing.
- Partner-editable multilingual templates use conceptual system defaults by key/language plus optional partner overrides by partner/key/language.
- Empty or missing partner overrides use the system default for that language. If a requested system default language is unexpectedly missing, Ukrainian is the fallback.
- Partner-editable templates and button labels are validated at save time. Unknown placeholders and render errors block saving.
- Admin content UI should show language tabs, system defaults, allowed placeholders, preview/render test, and reset-to-default action per key/language.
- Reminder settings, cancellation settings, quiet hours, required booking fields, and other numeric/time settings should use explicit min/max constraints.
- If a previously saved setting becomes invalid after a system change, runtime should use safe defaults/fallbacks where possible and show a partner-visible admin warning.
- Partner language.
- Client-facing language choices in the bot.
- Cancellation rules, including a partner-defined latest cancellation window.
- Optional late-cancellation penalty, for example adding 20 UAH to the next visit.
- No-show handling rules; MVP includes a no-show marker and optional no-show penalties when penalties are enabled.
- No-show-related prepayment can exist later, but is deferred beyond MVP.
- MVP partner admin main navigation: Dashboard, Calendar, Appointments, Clients, Catalog, Broadcasts, Finance, Bot, and Activity Log.
- Partner admin operational dashboard focused on today's/tomorrow's appointments/calendar, pending `requested` appointments, failed reminders/notifications, active conflicts/problems, and compact basic stats/revenue.
- Calendar is the primary day-to-day working screen for the master; Appointments list is a secondary searchable/filterable operational view.
- Partner Activity Log is a standalone screen, while appointment and client cards may show short related local history.
- Superuser admin can remain Django-admin-like/pragmatic in MVP, with dedicated operational views/filters for health, setup-required partners, and problems/incidents.
- Appointment list MVP filters limited to date/period, status, client search, service/category, penalty presence/unpaid penalty, and requested/near-fit markers.
- Simple day/week/month income report with service revenue, penalties as one separate combined line, and total.
- Reports should also show expected revenue from future `confirmed` appointments separately from actual revenue.
- Actual revenue is calculated from `completed` appointments using the current appointment state and includes paid/settled penalties applied to those appointments.
- Expected revenue includes only penalties already applied to future `confirmed` appointments.
- If a `completed` appointment is corrected and its amount changes, reports recalculate from the appointment's current corrected state.
- Reports use the partner's timezone for day/week/month boundaries.
- Partner can enter simple expense records with amount, date, and description/reason.
- Expense categories are not part of MVP; expenses are partner-only and not client-visible.
- Partner can edit/delete expense records; changes are audit logged.

### Partner-Editable Areas Mentioned
- Services.
- Prices.
- Schedule.
- Blocked time.
- Appointments.
- Clients.
- Bot texts.
- Button labels.
- Reminder/notification behavior for that partner's bot.
- Partner client card with client data, future appointments, appointment history, unpaid penalties, visit count, and total amount spent with this partner.

### Client Data
- Default client fields should include name, phone, language, automatically captured Telegram identity, Telegram username if available, Telegram Premium status if available, and booking comment.
- Date of birth is not required in MVP by default.
- Partner-required fields should start as a small/minimal configurable set and expand later only if needed.

### Money
- MVP currency is UAH.
- MVP income report should be simple day/week/month reporting with service revenue, penalties as one separate combined line, and total.
- Expected revenue from future `confirmed` appointments should be shown separately from actual revenue.
- Actual revenue is based on `completed` appointment current state and includes paid/settled applied penalties.
- Expected revenue includes only penalties already applied to future `confirmed` appointments, because the master may still waive or choose not to apply other unpaid penalties.
- Reports use the partner's timezone.
- MVP includes simple partner-entered expense records with amount, date, and description/reason.
- Expense categories are not part of MVP; simple description/title search can cover the first use cases later.
- Expenses are partner-only data and are not shown to clients.
- Expense edits/deletions are allowed and audit logged.
- Default cancellation deadline for a new partner is 6 hours before appointment.
- Late-cancellation penalties are disabled by default for a new partner.
- If the partner enables late-cancellation penalties, the default penalty amount is `50.00 UAH`, stored as `5000` minor units.
- Client self-service cancellation of a confirmed appointment follows the partner-configured cancellation deadline.
- If the client cancels after the deadline and penalties are enabled, the bot warns about the penalty and asks the client to confirm.
- If the client confirms late cancellation with penalties enabled, the appointment becomes `cancelled_by_client` and the penalty is created.
- Only one automatic late-cancellation penalty is created for one cancelled appointment.
- If the client tries to cancel after the deadline and penalties are disabled, self-service cancellation is blocked and the bot suggests contacting the master.
- The master can cancel after the deadline and can choose whether to cancel without penalty or with a penalty.
- Late-cancellation penalties are added to the next appointment price so the master sees the higher amount and collects more for that visit.
- Multiple late-cancellation penalties accumulate.
- MVP penalty statuses are `unpaid`, `applied_to_appointment`, `paid`, and `waived`.
- If a penalty was applied to an appointment and that appointment is later cancelled before the penalty is paid/settled, the penalty returns to `unpaid`.
- Penalties should be tied to a concrete business reason, not arbitrary free-form charges.
- MVP penalty reasons include late cancellation, no-show, and being late by more than partner-configured X minutes.
- Late-arrival penalty threshold is partner-configurable.
- Default late-arrival penalty threshold is 15 minutes.
- Default no-show penalty amount is `50.00 UAH`, stored as `5000` minor units.
- Default late-arrival penalty amount is `20.00 UAH`, stored as `2000` minor units.
- If penalties are enabled, the client personal area shows active unpaid penalties separately from appointment history.
- Penalty-specific client messaging is not needed in MVP; the general "contact master" action is enough.
- The master separately marks whether a penalty was paid.
- The master can manually waive/remove a penalty.
- Penalties should be shown as one separate combined line in income/revenue reports, not merged invisibly into service revenue.
- Manual prepayment by payment details/requisites is deferred beyond MVP.

### Appointment Lifecycle
- MVP appointment statuses: `requested`, `confirmed`, `cancelled_by_client`, `cancelled_by_master`, `completed`, `no_show`.
- These statuses are considered enough for MVP; no separate `rejected` status is required for now.
- Confirmed transitions for MVP: `requested -> confirmed`, `requested -> cancelled_by_master`, `requested -> cancelled_by_client`, `confirmed -> completed`, `confirmed -> no_show`, `confirmed -> cancelled_by_client`, `confirmed -> cancelled_by_master`.
- For future `confirmed` appointments, client-visible master changes such as time, services, price/duration, or client-visible comment require client confirmation before taking effect.
- Internal-only changes such as master private note, internal-only status metadata, and internal comments do not require client confirmation.
- While a client-visible appointment change proposal is pending, the existing appointment remains unchanged.
- Client receives a "master proposed appointment changes" message with accept and decline actions.
- Appointment change proposals expire after 48 hours by default.
- If a client-visible change proposal expires or is declined, the old appointment remains unchanged and both sides should be notified where possible.
- If client confirmation is impossible, such as for a manual client without Telegram identity, the master can save directly with audit logging and a clear indication that client confirmation/notification was not possible.
- If a future appointment price or duration is changed by the master, the client sees a marker that the value was changed by the master.
- Setting an appointment to `completed` does not change services, service snapshots, durations, prices, or final total.
- Payment state for already-applied penalties may be marked separately while completing the appointment.
- Final appointment statuses are not fully immutable in MVP; master may correct mistakes after `completed`, `no_show`, or cancellation, with audit logging and client notification when client-visible data changes.
- Correction flow requires a master-provided reason for audit.
- Returning a final appointment back to `confirmed` is allowed only through an explicit restore/reopen action for a future appointment, with availability re-check and client notification or confirmation when client-visible changes are involved.
- Past final appointments should not be returned to `confirmed`; if another visit is needed, the partner creates or proposes a new appointment.
- Old reminders from before finalization are not restored automatically. If a future appointment is explicitly restored/reconfirmed, a fresh reminder schedule is created.
- If a client cancels a `requested` appointment before the master confirms it, the status becomes `cancelled_by_client`.
- Created appointments keep the service price that was active when the client booked.
- Created appointments also keep the service duration that was active when the client booked.
- If the partner changes service duration, it affects only new appointments.
- Existing appointments should not be silently broken by later schedule changes. If a schedule change creates a conflict, the system should ask the master to process the conflict.
- For schedule conflicts, the master should see a list of conflict appointments and actions: leave as is, propose reschedule, cancel, or message the client.
- When proposing a reschedule, the master can send concrete alternative slots. If those do not work for the client, the client can choose another available slot or cancel.
- Bulk actions for conflict appointments, such as sending all affected clients a reschedule request, are included in MVP.

### Audit And Logging
- Audit log is required in MVP.
- Audit events should be partner-scoped so a partner can view logs for their own area.
- Superuser/platform admin can view logs across all partners.
- Required audit events: appointment creation, appointment update, appointment cancellation, appointment status changes, penalties, reminder send/failure events, and client-master messages.
- Required audit events also include expense creation/update/deletion, broadcast creation/scheduling/start/cancellation/completion/failure, and partner bot/settings changes.
- MVP should provide a separate partner-visible activity log/journal screen; appointment/client cards may also show related local history.
- Partner-visible audit log should contain only events that concern the partner's own area.
- Partner-visible Activity Log entries should be business-friendly and should not expose raw technical payloads.
- Sensitive/destructive partner actions must be audited, including appointment cancellation, no-show marking, penalty changes/payment/waiver, expense changes/deletion, bot token/settings changes, and corrections after final status.
- Partner-visible business audit logs are retained for 6 months by default.
- Superuser-visible system/audit logs include technical errors from bot runner and background jobs, with sensitive data masked.
- Audit reason is required for correction-flow actions after a final appointment status.
- For master cancellation, penalty waiver, and expense deletion, reason is optional in MVP unless a later policy makes it required.
- Internal technical payloads and platform-wide diagnostics should be platform-only.

### Sensitive Data
- Bot token is sensitive.
- After saving, the partner should not see the full bot token; show only a safe status or encrypted/masked representation.
- Bot tokens should be stored encrypted.
- Client phone number is sensitive.
- A master may see their client's full phone number when needed.
- Outside master-facing client context, client phone numbers should not be unnecessarily exposed.
- Phone numbers can be stored in normal usable form in the database, subject to access controls and log hygiene.
- Client-master messages are sensitive.
- The partner/master sees client-master conversations for their own clients.
- Superuser/platform admin may access client-master conversations for support and incident investigation.
- Master notes about clients are sensitive.
- Master notes about clients are visible only to the master/partner, not to the superuser by default.
- Sensitive values may appear in logs only in masked/redacted form. Bot tokens, phone numbers, message text, and private notes must not be logged raw.
- MVP does not require audit events for every superuser conversation view.
- Appointment/request comment text is separate from private master notes and is visible to both client and master.
- Visit history and penalties are not considered highly sensitive by default, but still belong to the partner-scoped client/business data.

### Data Export And Deletion
- Data export is not required in MVP.
- Future sensitive data export requires a separate approved decision.
- Client self-service data deletion request through the bot is not part of MVP, but should be added later.

### Expected Load
- Initial real usage may start with only a few masters.
- At least one interested early user is expected: a nail master for whom this may be a second job/side work.
- The product should not be treated as throwaway/prototype-only; even early MVP should be able to grow beyond the first few masters.
- Initial load target can use 50 partners, 500 appointments per day total, and 10 active bots as a reasonable early benchmark.
- The architecture should remain flexible and expandable so raising load beyond the initial benchmark is not a major problem later.

### Retention And Partner Suspension
- Client and appointment data should be stored indefinitely while the partner is active.
- If a partner disconnects, stops paying, or is suspended/frozen, their bot should stop.
- Suspended/frozen partner data should be retained.

### MVP Readiness Criteria
- Platform admin can connect a partner and their bot.
- Partner can configure services, prices, schedule, bot behavior/texts, reminders, and relevant partner-scoped settings.
- Partner sees only their own data area in admin.
- Client can register/use the bot, book, cancel, and reschedule.
- Partner can create, process, cancel, reschedule, complete, and mark no-show for appointments.
- Notifications/reminders work for clients and partners, including configured intervals, digest, failed reminder visibility, and stale `requested` reminders.
- Client personal area works.
- Basic broadcasts to clients work within the approved MVP boundary.

### Engineering Preferences For Later
- Code comments should not be avoided when they clarify hard-to-understand logic or deliberate workarounds.
- Tests should focus on important, fragile, business-critical, or foundational behavior rather than covering every trivial detail from the start.

## OPEN QUESTION

- Product/specification-level blockers before implementation planning are closed in `docs/discovery/OPEN_QUESTIONS.md`. Remaining work is implementation planning: exact code names, migrations, command names, UI polish, and rollout milestones.
- Important but non-blocking implementation design details remain open: calendar UI polish, appointment-history and pagination copy, onboarding checklist presentation, broadcast backoff/safety thresholds, and business-friendly delivery failure wording.
- MVP scope is broad; before coding, identify the minimum pilot path and which confirmed MVP features can be delayed if the first implementation becomes too large.
- The product origin is now clearer through TableOS and Botera ecosystem strategy, but a concrete observed "before BoteraMaster" scenario from a master is still unavailable; the product problem remains a hypothesis to validate.
- Details of future message moderation/abuse/reporting controls remain open and can be revisited if bot-mediated client-master messages create support or misuse cases.
- Exact role of future ecosystem-level Botera promotions/bonuses/partner program is outside MVP and not designed yet.

## REJECTED

- Full studio/multi-master system is outside MVP.
- Advanced/smart broadcast automation beyond the confirmed basic MVP broadcasts is outside MVP.
- Shared platform bot as the primary MVP model is rejected for now; each partner/master should have their own bot.
- Data export is not required in MVP.
- Client self-service data deletion request through the bot is not part of MVP.
- Employee roles are not part of MVP.
- Online payments are not part of MVP.
- Reviews are not part of MVP.
- Loyalty is not part of MVP.
- Google Calendar integration is not part of MVP.
