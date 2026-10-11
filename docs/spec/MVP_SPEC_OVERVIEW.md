# BoteraMaster MVP Spec Overview

Status: finalized MVP overview on 2026-08-20, not final technical specification.

This document summarizes the current product picture after discovery and follow-up specification interviews. It is the working project map for the MVP. It intentionally does not define final architecture, Django models, migrations, dependencies, AGENTS.md, or implementation code.

## Product Picture

BoteraMaster is a Telegram-first scheduling, client-management, communication, notification, broadcast, and lightweight reporting service for solo beauty masters.

The first MVP user is a solo appointment-based master without a team. In client-facing language this user is a master. In platform, admin, and code language the tenant boundary is called partner.

Each partner has their own Telegram bot. The partner provides the bot token, and BoteraMaster operates the bot through polling. The partner does not manage servers, hosting, workers, deployments, or runtime infrastructure.

The core value is the connected bundle: a client can book and manage visits through the master's bot, the master controls services/schedule/clients/appointments/settings, reminders work reliably, communication happens through the bot, and the partner can customize behavior/content without changing global platform behavior.

BoteraMaster is related to the broader Botera ecosystem idea and was inspired by TableOS, but TableOS remains a conceptual reference rather than an architecture to copy blindly.

## MVP Users And Roles

- Client: a Telegram user who interacts with one partner bot to book and manage visits.
- Partner/master: one solo owner/master with full access to their own partner area.
- Superuser/platform admin: platform operator who can create/manage partners, see platform-wide data, manage global settings, and resolve operational issues.

Out of MVP:

- Partner staff roles.
- Multi-master studios.
- Self-service public partner registration.

## Partner Onboarding

MVP onboarding is controlled by superuser.

- Superuser creates the partner.
- Superuser creates the partner admin account and gives login/password.
- Partner must complete minimum setup before the bot is considered ready for clients.
- Minimum setup before bot launch: bot token, language, timezone, at least one service category/service, at least one availability window, and booking confirmation mode.
- Most other settings can start from defaults: texts/buttons, reminders, quiet hours, cancellation deadline, penalties off, and basic required fields.
- Setup should have a draft/setup-required state so the bot does not run for clients before required setup is complete.
- Partner admin should show an onboarding checklist/setup guide for the minimum launch steps.
- While setup is incomplete, client booking/profile flows are unavailable; the bot may show a short system-only "being configured" message.
- After minimum setup is complete, the partner can launch the bot without final superuser approval.
- Superuser dashboard/admin should show setup-required partners and missing setup steps.
- If an active partner later has no services or availability windows, the bot remains active but shows appropriate empty states.

## Bot And Settings

Bot configuration is split conceptually:

- Bot Instance: technical Telegram connection/runtime identity, token, operational status, runtime eligibility, last error, and health/activity.
- Bot Settings: partner-controlled behavior/content, including languages, texts, buttons, reminders, quiet hours, booking rules, required fields, cancellation rules, and penalties.

Admin UX should expose one main Bot section with tabs:

- Instance.
- Settings.
- Content.
- Notifications.
- Booking.
- Penalties.

Bot/settings configuration is admin-only in MVP, not controlled through the Telegram bot.

Bot statuses:

- `active`
- `paused`
- `suspended`
- `error`

Partner can switch own bot between `active` and `paused`. `suspended` is superuser-only.

Partner sees bot health/status in admin: operational status, last startup/runtime error when present, and last successful polling/activity timestamp when available.

If status is `error`, partner can trigger a connection/configuration check from admin. Because MVP runner does not hot-reload, successful check may still require runner restart/redeploy before live polling changes.

Critical settings use normal confirmation modals before save. Typed confirmations are not required in MVP.

## MVP Functional Boundary

Included in MVP:

- Partner-scoped admin isolation.
- Services and service categories managed by partner.
- Multi-service bookings.
- Availability calendar with windows, blocked time, templates, booking horizon, and conflict handling.
- Calendar range selection for availability windows, blocked time, and manual appointments.
- Calendar appointment detail panel/modal with edit/reschedule/cancel/complete/no-show/message actions.
- Quick copy day/week calendar actions.
- Mobile-usable calendar admin with adapted interactions.
- Client bot booking flow: category, services, summary, date, slot, final confirmation.
- Auto-confirm/manual-confirm booking modes.
- `requested` handling, including master confirm/decline/propose alternatives.
- Client and master rescheduling/cancellation.
- Manual appointments and manual clients without Telegram identity, including time-first calendar creation, inline manual client creation, default `confirmed` status, linked-client Telegram notification, and warning-based creation outside availability windows.
- Client profile/personal area with future appointments, history, unpaid penalties, contact master, and profile data editing.
- Multilingual bot behavior for Ukrainian, Russian, and English, Ukrainian default.
- Partner-editable bot templates/buttons per language, with fallback to system defaults.
- Client reminders, master reminders, morning digest, stale requested reminders, quiet hours, and failed reminder handling.
- Client business event notifications for appointment lifecycle/change/penalty events, with delivery failure visibility.
- Bot-mediated client-master messaging.
- Penalties for late cancellation, no-show, and late arrival when enabled.
- Broadcasts with filters, immediate/scheduled sending, text/photo/buttons, reusable templates, unsubscribe, and delivery stats.
- Simple reporting: actual revenue, expected revenue, penalties line, expenses.
- Partner-visible activity log/audit and superuser-wide logs.

Explicitly outside MVP:

- Online payments.
- Manual prepayment by requisites.
- Reviews.
- Loyalty/bonuses/promos.
- Google Calendar sync.
- Data export.
- Self-service client deletion through bot.
- Repeat previous service.
- Advanced no-show consequences such as prepayment requirements.
- Cross-product Botera partner program.

## Key Business Rules

- Partner-owned records must be partner-scoped.
- A Telegram identity can be shared technically, but each partner has a separate client profile.
- Bot blocking/reachability is partner-bot scoped, not global to the Telegram identity.
- Confirmed appointments cannot overlap for the same booking resource.
- In solo-master MVP, each partner has one default booking resource; future studios can add multiple resources under one partner.
- Requested appointments do not block public availability in MVP; multiple requests can compete for the same slot.
- Confirming a requested appointment re-checks availability.
- Service price/duration snapshots are fixed at booking time.
- Multi-service appointments should preserve service item snapshots.
- Future confirmed appointment changes that are visible to the client require client confirmation.
- Client-visible appointment change proposals keep the old appointment unchanged until accepted, expire after 48 hours by default, and use accept/decline actions.
- Manual clients without Telegram identity can be changed directly with audit logging because client confirmation is not possible.
- Final statuses can be corrected by master with audit reason, but past final appointments should not be restored to confirmed.
- Old reminders are not restored automatically after finalization; restored future appointments get fresh reminders.

## Money And Reports

- MVP currency is UAH.
- Money is stored in minor units, for example `500.00 UAH` as `50000`.
- Appointment total is selected service snapshots plus penalties applied to that appointment.
- Actual revenue is calculated from completed appointments using current corrected appointment state.
- Paid/settled penalties applied to completed appointments are included in actual revenue.
- Expected revenue is calculated from future confirmed appointments and includes only penalties already applied to those appointments.
- Reports use partner timezone for day/week/month boundaries.
- Expenses are partner-only, manually entered with date, amount, and description/reason.
- Expense categories are not part of MVP.
- Expense create/update/delete is audit logged.

## Partner Admin UX

MVP partner admin main navigation:

- Dashboard.
- Calendar.
- Appointments.
- Clients.
- Catalog.
- Broadcasts.
- Finance.
- Bot.
- Activity Log.

Dashboard is the first screen and should focus on today's/tomorrow's appointments, pending requests, failed notifications, active conflicts/problems, and compact stats/revenue.

Calendar is the primary day-to-day working screen for the master. Appointments list is a secondary searchable/filterable operational view.

Partner Activity Log is a standalone screen, while appointment and client cards may also show short related history.

Superuser admin can remain Django-admin-like/pragmatic in MVP, with dedicated operational views for health, setup-required partners, and problems/incidents.

## Audit And Retention

Partner-visible audit has a standalone activity log/journal screen. Appointment and client cards may also show local related history.

Partner-visible audit includes:

- Appointments.
- Penalties.
- Expenses.
- Broadcasts.
- Reminders.
- Client-master messages.
- Partner bot/settings changes.

Partner-visible business audit logs are retained for 6 months by default.

Cleanup-eligible noisy technical records use `retain_until`. Default cleanup retention is 90 days unless a specific record type has another policy.

System/platform logs include technical bot runner and background job errors with sensitive data masked.

## Sensitive Data

Sensitive data includes:

- Bot tokens.
- Client phone numbers.
- Client-master messages.
- Master private notes.

Bot tokens are stored encrypted and are not shown in full after save.

Sensitive data must not appear raw in technical logs, platform diagnostics, or partner-visible audit.

## Readiness Criteria

MVP is ready when:

- Superuser can create a partner and admin access.
- Partner can complete minimum setup and launch a bot.
- Partner admin sees only own data.
- Client can register/profile, book, cancel, reschedule, view history, and contact master.
- Master can manage services, schedule, clients, appointments, penalties, broadcasts, settings, reminders, and bot content.
- Reminders, digests, quiet hours, stale requested reminders, and failed reminder handling work.
- Bot runtime can run active partner bots through polling.
- Partner-visible audit and basic reports work.
- Suspended partner bot stops while data remains.

## Main Risks And Open Design Areas

- MVP scope is broad for a first release.
- The product problem is still partly hypothesis-driven, based on TableOS analogy and informal validation rather than observed master workflows.
- Calendar UX and conflict resolution must be carefully designed because availability is a core value.
- Multilingual partner-editable content direction is approved, but the exact content registry, placeholder contexts, and default copy still need implementation design.
- Broadcast pacing direction is approved, but exact backoff, global safety limits, and alert thresholds still need implementation design.
- Bot runner without hot reload is simpler but creates operational friction around token/status changes.
- Sensitive-data masking/encryption details still require technical design.
- Platform-only internal diagnostic event taxonomy remains open.
- Future studios may pressure the MVP tenant model.

## Finalized Overview Decisions

- Broad MVP boundary is accepted as the current target, with scope-risk review before implementation.
- MVP partner onboarding is managed by superuser.
- Public/self-service partner registration is outside MVP.
- Bot configuration is split conceptually into Bot Instance and Bot Settings.
- Admin UX uses one Bot section with tabs: Instance, Settings, Content, Notifications, Booking, and Penalties.
- MVP bot runtime uses a simple runner without hot reload.
- TableOS is a conceptual reference, not an architecture to copy blindly.
- Partner-visible business audit retention is 6 months.
- Cleanup-eligible noisy technical retention defaults to 90 days.
- Online payments, exports, reviews, loyalty, Google Calendar, studio roles, and public partner registration are outside MVP.

## Next Specification Work

- Review and refine `docs/spec/TECHNICAL_SPEC_DRAFT.md`.
- Prepare the final technical specification / implementation blueprint from `PRODUCT_REQUIREMENTS.md`, `DOMAIN_MODEL.md`, `ARCHITECTURE_DECISIONS.md`, and `TECHNICAL_SPEC_DRAFT.md`.
- Resolve or consciously defer the remaining questions in `docs/discovery/OPEN_QUESTIONS.md`.
- Treat the remaining blockers as implementation-planning decisions, not as unresolved product discovery.
- Before writing application code, approve the final architecture, project structure, dependency choices, and implementation order.
