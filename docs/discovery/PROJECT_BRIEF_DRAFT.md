# BoteraMaster Project Brief Draft

Status: discovery draft, not approved.

## Discovery Checkpoint For Approval

Status: approval candidate, not final specification.

### Whole Product Picture

BoteraMaster is a Telegram-first scheduling, client-management, and communication service for solo beauty masters. The first market is individual masters who work by appointment: nails, brows, lashes, hair, cosmetology, massage, tattoo, makeup, depilation, and similar appointment-based services.

The platform hides infrastructure from the partner. A partner/master does not manage servers, hosting, workers, bot runtime, or technical setup. The partner provides a Telegram bot token, and BoteraMaster runs that partner's bot through polling. Each partner has their own bot; the shared-platform-bot model is not the MVP direction.

The main value is not one isolated feature, but a connected bundle that feels predictable: clients can book and manage visits through the master's bot, the master sees and controls their own schedule/client base, reminders work, communication happens through the bot, and the partner can adjust bot behavior/texts/settings without touching global platform behavior.

The product origin is strategic as well as practical: while building TableOS, the user recognized that the same partner-owned bot/service model could be useful for masters and appointment-based personal services. This also led to a broader Botera ecosystem idea, where several vertical products may later share ecosystem-level mechanics such as promotions, bonuses, and partner programs across businesses.

### Proposed MVP Boundary

MVP should include:

- Partner setup with one owner/master role.
- One Telegram bot per partner, with partner-provided token and safe token visibility after save.
- Partner-scoped admin isolation: partner sees only their own data; superuser sees all.
- Partner management of services, prices, durations, schedule, blocked time, appointments, clients, bot texts, button labels, language, reminders, quiet hours, required booking fields, and cancellation rules.
- Client bot flow for registration/profile data, service/date/time selection, booking request, confirmation/cancellation/rescheduling, and personal area.
- Client personal area with profile/card, editable data, visit count, additional free-text field, future appointments, cancellation, rescheduling, full history, and contact with master through bot-mediated messages.
- Appointment statuses: `requested`, `confirmed`, `cancelled_by_client`, `cancelled_by_master`, `completed`, `no_show`.
- Auto-confirmed and manually confirmed booking modes.
- Protection against double booking of the same slot.
- Snapshot of service price and duration at booking time.
- Schedule-conflict handling when the partner changes schedule: conflict list, leave as is, propose reschedule, cancel, message client, and bulk conflict actions.
- Multilingual bot behavior in Ukrainian, Russian, and English, with Ukrainian as default.
- System default texts/button labels per language and partner overrides per language with fallback to defaults.
- Configurable client reminders, default 24 hours and 2 hours before confirmed appointment.
- Configurable master appointment reminders, default 45 minutes before each confirmed appointment.
- Morning digest for master, default 08:00 partner timezone, including "no appointments today" when empty.
- Stale `requested` reminders for unprocessed booking requests, default after 3 hours with decreasing repeat interval and 30-minute minimum.
- Quiet hours for clients and partners, default 22:00-08:00 partner timezone; reminders delayed until quiet hours end.
- Failed reminder visibility for master with appointment marker and inline actions: retry, write to client, view client.
- UAH-only money handling for MVP.
- Configurable late-cancellation penalty that accumulates, is added to the next appointment total, is visible to client and master, can be marked paid separately, and can be waived by master.
- No-show marker.
- Income/reporting view where penalties appear as a separate line.
- Basic partner broadcasts in MVP: filters, immediate or scheduled send, text/photo/buttons, reusable templates, unsubscribe from marketing, queued/sent/failed statuses, unavailable-client marking when bot is blocked.
- Partner-scoped audit log for appointment changes, status changes, penalties, reminder send/failure events, and client-master messages; superuser can see all.
- Indefinite data retention while partner is active; suspended/frozen partner bot stops, data remains.
- Initial load target benchmark: 50 partners, 500 appointments/day total, 10 active bots, while keeping room to scale higher later.

### Explicitly Outside MVP

- Studios/multi-master organization support.
- Employee roles and granular partner-side permissions.
- Online payments.
- Manual prepayment by requisites.
- Reviews.
- Loyalty.
- Google Calendar sync.
- Self-service client data deletion request through bot.
- Data export.
- Repeat previous service.
- Advanced lifecycle for no-show consequences beyond the no-show marker.
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
- Broadcast audit events remain questionable; we confirmed appointment/reminder/message audit, but not whether every broadcast lifecycle event is worth partner-visible audit.

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
- Client selects service, date, and available time.
- Bot asks for name and phone if missing.
- Required client data for booking should be configurable per partner, because different partners may want different data collected before booking.
- System creates a booking request.
- If the partner uses manual confirmation, a client booking starts as `requested`.
- If the partner uses auto-confirmation, a client booking starts as `confirmed`.
- If two clients try to book the same slot concurrently, only one booking can win; the other client should be told that the time is already occupied and asked to choose another slot.
- Master receives a notification with inline actions: accept, reject, or suggest reschedule.
- Client receives confirmation or cancellation information after master's action.
- Client should be able to see future appointments.
- For MVP, client cancellation is desired.
- In MVP, the client personal area includes future appointments and cancellation.
- In MVP, the client personal area includes full visit/appointment history, including completed, cancelled, and no-show records.
- In MVP, the client personal area includes a way to contact the master by sending a message through the bot.
- Master should be able to reply to client messages through the bot as well.
- Client-to-master messages can be initiated from the client personal area without being tied to a specific appointment.
- "Repeat previous service" is not required in MVP and can be added later.

### Initial Master Scenario
- Master can view today's, tomorrow's, and upcoming appointments.
- Master can manually add an appointment.
- Master can confirm, cancel, complete, and mark no-show.
- Master can view clients.
- Master can add services and change service price/duration.
- Master can configure work time, days off, and blocked time.
- The partner should have a convenient calendar-like admin experience for filling weekly/monthly schedule and available hours, but the exact UX is not decided.
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
- Broadcast sending should be paced, for example through queued sending with small delays between messages.
- Partner should see broadcast statistics. MVP statuses should be `queued`, `sent`, and `failed`; `delivered` should not be promised if Telegram does not provide reliable delivery confirmation.
- Broadcasts can be composed manually.
- Partner should be able to save a broadcast as a reusable template.
- The "long-absent clients" filter period is chosen by the partner.
- The system should support sending broadcasts with buttons generally; exact button types can be expanded over time.
- Broadcast recipients should include only clients who have not unsubscribed from marketing broadcasts.
- If a client blocks the bot, the client should be marked unavailable for broadcasts, while their profile and history remain stored.
- Broadcast audit events are not clearly required yet; their value should be reconsidered if needed.

## ASSUMPTION

- MVP likely starts with one master per partner rather than full studio support.
- Studio/multi-master support is a future direction, not initial MVP.
- Beauty segment is the first vertical because the pain is clearest.
- Telegram is the primary client and master interaction channel.
- Django and aiogram are a preferred technical direction from the user, but this is not yet an approved architecture decision.
- TableOS is available as a reference project with a similar product idea in another market. Its `bot`, `core`, and `apps/partners` areas should be studied as possible reference patterns for partner setup, bot setup, bot texts, and configurable bot behavior.
- The user sees BoteraMaster as similar in spirit to TableOS: the customer does not deal with servers, hosting, or technical setup.
- The idea for BoteraMaster came from building TableOS and recognizing that a similar partner-owned bot/service model could fit masters and appointment-based services.
- BoteraMaster is part of a broader Botera ecosystem idea. In the future, multiple vertical products may share ecosystem-level features such as promotions, bonuses, and partner programs between different partner types.
- TableOS has relevant existing patterns: encrypted bot tokens, active polling bot instances, partner runtime middleware, partner-specific bot settings, and admin data scoping by partner.
- The user's direct market validation so far is informal: the idea received a positive reaction, but no concrete observed "before BoteraMaster" scenario from a master has been provided.
- TableOS `apps/users` is a useful reference for separating a global Telegram identity from partner-scoped customer profiles, but its employee/admin-access model may be more complex than BoteraMaster MVP needs.
- TableOS bot content is a useful reference: default texts/button labels, partner overrides, and safe fallback behavior when a partner-defined template is invalid. TableOS does not currently cover translations, so BoteraMaster needs a multilingual extension to that idea.
- TableOS bot token validation is a useful reference: token format validation on save plus active runtime validation before polling starts.

### Roles And Access
- Client-facing language should refer to a master.
- Platform/back-office language may refer to a partner.
- Partner-scoped settings and data should be accessible to the partner/master.
- Global application settings should be available only to the superuser/platform admin.
- In MVP, the partner admin role can be a single owner/master role; detailed partner-side roles can be handled later.
- The partner can manage their own partner-scoped settings.
- The partner can manage their own bot token.
- If changing a setting affects only the current partner's behavior/data, it can be partner-editable.
- If changing a setting affects the application globally, root behavior, or multiple partners, it is superuser-only.

### Required Partner Bot Settings Mentioned
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
- Minimum interval for repeated unprocessed-request reminders is 30 minutes.
- If a client reminder is not delivered, the master sees an immediate bot notification and the appointment shows a "reminder not delivered" status/marker.
- Failed reminder notification should include inline actions: retry sending, write to client, and view client.
- Failed reminder retry can be triggered without a limit/cooldown.
- Whether phone number is required.
- Client data fields required for booking.
- Partner language.
- Client-facing language choices in the bot.
- Cancellation rules, including a partner-defined latest cancellation window.
- Optional late-cancellation penalty, for example adding 20 UAH to the next visit.
- No-show handling rules; MVP definitely needs a no-show marker.
- No-show-related prepayment can exist later, but is deferred beyond MVP.

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

### Client Data
- Default client fields should include name, phone, language, automatically captured Telegram identity, Telegram username if available, Telegram Premium status if available, and booking comment.
- Date of birth is not required in MVP by default.
- Partner-required fields should start as a small/minimal configurable set and expand later only if needed.

### Money
- MVP currency is UAH.
- Late-cancellation penalties are added to the next appointment price so the master sees the higher amount and collects more for that visit.
- Multiple late-cancellation penalties accumulate.
- The master separately marks whether a penalty was paid.
- The master can manually waive/remove a penalty.
- Penalties should be shown as a separate line in income/revenue reports, not merged invisibly into service revenue.
- Manual prepayment by payment details/requisites is deferred beyond MVP.

### Appointment Lifecycle
- MVP appointment statuses: `requested`, `confirmed`, `cancelled_by_client`, `cancelled_by_master`, `completed`, `no_show`.
- These statuses are considered enough for MVP; no separate `rejected` status is required for now.
- Confirmed transitions for MVP: `requested -> confirmed`, `requested -> cancelled_by_master`, `requested -> cancelled_by_client`, `confirmed -> completed`, `confirmed -> no_show`, `confirmed -> cancelled_by_client`, `confirmed -> cancelled_by_master`.
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
- Partner-visible audit log should contain only events that concern the partner's own area.
- Internal technical payloads and platform-wide diagnostics should be platform-only.

### Sensitive Data
- Bot token is sensitive.
- After saving, the partner should not see the full bot token; show only a safe status or encrypted/masked representation.
- Client phone number is sensitive.
- A master may see their client's full phone number when needed.
- Outside master-facing client context, client phone numbers should not be unnecessarily exposed.
- Phone numbers can be stored in normal usable form in the database, subject to access controls and log hygiene.
- Client-master messages are sensitive.
- The partner/master sees client-master conversations for their own clients.
- Superuser/platform admin may access client-master conversations for support and incident investigation.
- Master notes about clients are sensitive.
- Master notes about clients are visible only to the master/partner, not to the superuser by default.
- Appointment/request comment text is separate from private master notes and is visible to both client and master.
- Visit history and penalties are not considered highly sensitive by default, but still belong to the partner-scoped client/business data.

### Data Export And Deletion
- Data export is not required in MVP.
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

- Is the proposed broad MVP boundary approved as-is, or should it be cut before final specification?
- Which exact permissions does the MVP owner/master role have?
- Is partner exactly the master forever in MVP, or should the tenant concept be named more generally to allow future studios?
- What exact technical broadcast pacing/rate-limit strategy is required for Telegram and multiple active bots?
- Are broadcast audit events needed at all in MVP, and if yes, which ones?
- What default cancellation rule should new partners start with?
- How should no-show consequences after MVP be represented, applied, shown to the client, and cleared/recovered?
- How should sensitive data be protected in product UX, storage, logs, and exports?
- Which exact internal audit/diagnostic events are platform-only?
- The product origin is now clearer through TableOS and Botera ecosystem strategy, but a concrete observed "before BoteraMaster" scenario from a master is still unavailable; the product problem remains a hypothesis to validate.
- Whether to reuse the TableOS-style global Telegram account plus partner-scoped client profile pattern is not technically approved yet.
- Details of bot-mediated client-to-master messaging are not fully approved yet: message privacy, conversation context, moderation/abuse handling, and delivery failure behavior remain open.
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
