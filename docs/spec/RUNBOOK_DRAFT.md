# BoteraMaster MVP Runbook

Status: implemented operational baseline for the approved Pilot Wiring milestone.
Last updated: 2026-10-15.

This runbook covers the implemented single-host Docker Compose pilot. Durable
business state remains in PostgreSQL; Redis and Celery transport work but do not
replace database delivery records.

## Deployment Shape

MVP may run on a single VPS/server with Docker Compose.

Required production MVP services:
- `web`
- `bot-runner`
- `celery-beat`
- `notifications-worker`
- `broadcast-worker`
- `default-worker`
- `postgres`
- `redis`

Recommended worker roles:
- `notifications-worker`: urgent reminders/service notifications, initial concurrency around 4.
- `broadcast-worker`: broadcasts only, initial concurrency 1.
- `default-worker`: `default` and `maintenance` queues, initial concurrency 1.

Rules:
- `broadcasts` must not share the urgent notification worker in production MVP.
- `celery-beat` / scheduler must run explicitly; scheduled features are not supported if beat is down.
- Docker Compose restart policies and healthchecks are enough for MVP process supervision.
- Kubernetes/heavier orchestration is not required for MVP.
- Sentry or equivalent error monitoring is nice-to-have/post-MVP.

## Standard Deploy / Restart Procedure

Recommended flow:
1. Pull the repository and prepare `.env` from `.env.example` with real secrets.
2. Build the application image.
3. Run database migrations before accepting normal traffic.
4. Start or restart all services.
5. Run Django's system check and inspect process state.
6. Run the bot runtime dry-run and one configured partner readiness check.
7. Inspect health records and recent masked platform diagnostics.

```bash
docker compose build
docker compose run --rm web python manage.py migrate
docker compose up -d
docker compose exec web python manage.py check
docker compose exec web python manage.py sync_partner_owner_access
docker compose ps
docker compose exec web python manage.py run_partner_bots --dry-run
docker compose exec web python manage.py check_pilot_readiness \
  --partner-id <partner-uuid>
docker compose logs --tail=200 bot-runner celery-beat \
  notifications-worker broadcast-worker default-worker
```

Use `docker compose restart bot-runner` after bot token/status/setup changes.
The runner intentionally does not hot-reload configs. A fresh installation with
no eligible bot lets `bot-runner` exit cleanly instead of entering a restart
loop; start it after the first partner is eligible.

## Partner And Owner Onboarding

Partner creation is controlled by a superuser in the Django admin.

1. Open **Partners > Partners > Add partner**.
2. Enter the partner name, timezone, default language, and initial owner
   credentials.
3. Save once. The onboarding service atomically creates the setup-required
   partner, staff owner account, active owner membership, default booking
   resource, schedule settings, notification defaults, and penalty defaults.
4. Give the credentials to the intended owner through an appropriate private
   channel. Do not include credentials in logs or diagnostics.
5. The owner signs in to the same admin and completes the onboarding checklist
   shown on their partner record before launching the bot.

Partner-owner access is maintained by one reviewed Django permission group.
Run `python manage.py sync_partner_owner_access` after deployment when owner
permissions change, and once for installations that already contain partner
owners. The command is idempotent; `--dry-run` only reports the affected count.
Do not manually add broad model permissions to owner accounts: every permitted
admin surface must first be tenant-scoped and safe for its available writes.

## Partner Bot Setup In Admin

The Django admin adapter exposes token setup without rendering the stored raw
token. The token is always persisted through the bot service, so encryption,
fingerprinting, tenant access, and duplicate-token checks still apply.

1. Open **Bot > Bot instances** and add the instance for the visible partner.
2. Enter the BotFather token in **New bot token** and save. On later edits,
   leave this field empty to keep the existing token.
3. Select the bot and run **Check Telegram connection**. Confirm that bot id,
   username, and `last_checked_at` are populated and no error remains.
4. While logged in as the intended owner/master admin account, run **Create my
   master Telegram link**, open the short-lived link in that person's Telegram,
   and complete `/start`. The link binds the currently logged-in admin account.
5. Create at least one active category/service, choose booking confirmation
   mode, and add a future availability window.
6. Run **Launch selected bots after setup checks**. This atomically activates
   the setup-complete partner and bot; incomplete setup or an unchecked bot is
   rejected with a business-friendly message.
7. Restart `bot-runner`, because the MVP runner loads active bot configs only at
   process startup.

Use **Pause selected bots** for a voluntary stop. A platform-suspended bot
cannot be resumed by a partner owner.

## Partner Schedule Setup In Admin

The pilot admin exposes schedule writes only through schedule-domain services.

1. Open **Schedule > Partner schedule settings** and choose confirmation mode,
   slot step, booking horizon, near-fit tolerance, and cancellation deadline.
2. Use **Availability windows** for one-off concrete working intervals.
3. Use **Blocked times** for breaks, days off, vacations, or other unavailable
   intervals. Blocks can be created, edited, and deleted by the partner owner.
4. For reusable schedules, create an **Availability template**, then add its
   weekday/time rows under **Availability template windows**.
5. Select exactly one active template and run **Apply selected template to a
   date range**. Choose the booking resource and future period in the
   confirmation form.

Applying a template creates concrete availability windows; the template alone
does not expose client slots. Reapplying the same template to the same resource
and range does not duplicate identical generated windows. Deleting a template
keeps already generated concrete windows and only clears their template link.

## Manual Appointments In Admin

Use **Appointments > Appointments > Add appointment** for a partner-created
visit. Select the calendar resource and start time first, then either choose an
existing client or enter a new manual client's name and/or phone.
The start time is entered in the selected partner's timezone and stored as an
aware UTC timestamp.

Add one or more service lines. A catalog service uses its current price and
duration when the override fields are empty. Filled price or duration fields
apply only to this appointment snapshot. A line without a catalog service must
provide a custom service name, price, and duration; it is not added to the
partner catalog.

Manual appointments are created as `confirmed`. They may sit outside an
availability window, but the same resource-level advisory lock and fresh
overlap check used by normal confirmation still reject overlapping confirmed
appointments. If inline manual-client creation and appointment confirmation
fail together, both writes roll back. A Telegram-linked client receives a
durable immediate confirmation notification; a manual client without Telegram
still appears in master reminders and digests but has no client delivery target.

## End-To-End Pilot Check

Before the check, complete the admin setup flow above. The partner and bot must
be active and have an encrypted checked token, at least one active
category/service, schedule confirmation mode, a future availability window,
and a primary master Telegram contact created through the signed admin-link
flow.

1. Verify the stored token against Telegram. The raw token is read from encrypted
   storage and is never passed as a CLI argument.
2. Run the aggregate readiness command.
3. Restart the polling runner so it loads the current eligible bot set.
4. Send `/start`, then `/book`, from a client Telegram account.
5. Select a category, service, date, and time; confirm the booking.
6. For manual confirmation, confirm the request in partner admin.
7. Verify the client notification becomes `sent`, or has a durable masked
   failure/retry state.
8. Verify the master receives partner-facing notifications, or readiness clearly
   reports the missing master contact.

```bash
docker compose exec web python manage.py check_bot_connection \
  --partner-id <partner-uuid>
docker compose exec web python manage.py check_pilot_readiness \
  --partner-id <partner-uuid> --check-telegram
docker compose restart bot-runner
docker compose logs --tail=200 -f bot-runner notifications-worker
```

Do not paste a raw token into shell arguments. Partner token setup must use the
admin **New bot token** field (or the underlying application service) so
encryption and duplicate fingerprint checks are applied.

## Health Dashboard Checklist

Superuser health dashboard should show:
- overall status: OK / Warning / Critical
- `web`
- `bot-runner`
- `celery-beat`
- `notifications-worker`
- `broadcast-worker`
- `default-worker`
- notifications queue lag
- broadcasts queue lag
- due reminder lag
- active broadcast progress
- PostgreSQL connectivity
- Redis connectivity
- recent platform diagnostics count by severity

Partner-facing admin shows only partner-relevant bot health/status, not internal queue/worker infrastructure.

## Worker Health Thresholds

MVP thresholds:
- `beat`: Warning after 3 minutes stale, Critical after 5 minutes stale.
- `notifications` ping: Warning after 3 minutes unprocessed, Critical after 5 minutes.
- due reminder lag: Warning when due work is more than 2 minutes late, Critical when more than 5 minutes late.
- `broadcasts` ping: Warning after 10 minutes unprocessed, Critical after 30 minutes.
- active broadcast without progress: Warning after 15 minutes, Critical after 60 minutes.

Operational rule:
- broadcast lag can be acceptable if progress is visible.
- notification/reminder lag is a real health problem.

## Queue Checks

Notifications queue:
- verify `notifications-worker` is alive
- verify due reminder lag
- inspect failed reminder diagnostics
- inspect recent delivery attempts and failure codes
- restart `notifications-worker` if stuck

Broadcast queue:
- verify `broadcast-worker` is alive
- check campaign progress counters: queued/sent/failed/skipped
- check whether Telegram 429/backoff is active
- restart `broadcast-worker` if crashed/stuck
- do not manually duplicate-send a broadcast; durable recipient records are the source of truth

Default/maintenance queue:
- verify cleanup and health tasks are not starving urgent queues
- if cleanup becomes heavy, consider future dedicated maintenance worker

## Bot Runner Checks

Check when a bot does not respond or partner says the bot is unavailable:
1. Partner status is active.
2. BotInstance status is active.
3. Partner setup is complete.
4. Token decrypts successfully.
5. Token validation passes.
6. Token fingerprint is not duplicated by another active bot.
7. `bot-runner` is running.
8. `bot-runner` has recent successful polling/activity timestamp.
9. Recent diagnostics do not show Telegram startup/polling errors.

Commands:

```bash
docker compose exec web python manage.py run_partner_bots --dry-run
docker compose exec web python manage.py check_bot_connection \
  --partner-id <partner-uuid>
docker compose exec web python manage.py check_pilot_readiness \
  --partner-id <partner-uuid>
docker compose logs --tail=200 bot-runner
```

MVP runtime does not hot-reload bot token/status changes. After token/status changes, restart/redeploy `bot-runner` for live polling to pick up the new active bot set.

## Invalid Token Recovery

If partner enters an obviously invalid token:
- block save with a validation error where possible.

If token passes save-time validation but runtime fails:
- set bot status to `error`.
- show partner a business-friendly admin message.
- create masked platform diagnostic for superuser.
- partner may use "check/retry connection" after updating token/settings.
- after a fixed token is saved, restart/redeploy `bot-runner` if the live runner must pick up the change.

Sensitive data:
- never show raw token in UI after save.
- never write raw token to logs/diagnostics.
- diagnostics use masked token/status/error codes only.

## PostgreSQL Checks

Check when app/worker health is bad or database errors appear:
- PostgreSQL service/container is running.
- application can open a DB connection.
- migrations are applied.
- disk is not full.
- slow queries or locks are not growing unexpectedly.
- cleanup jobs are not deleting core business history.

Operational rule:
- PostgreSQL is the business source of truth for delivery records, broadcasts, notifications, appointments, and audit records.
- Celery/Redis result state is not the source of truth for business delivery outcomes.

## Redis Checks

Check when workers/queues look stuck:
- Redis service/container is running.
- workers can connect to Redis.
- queue length/lag is not growing without progress.
- broker errors in worker logs are masked and diagnosable.

If Redis restarts:
- workers should reconnect or be restarted.
- durable DB records should let reminder/broadcast dispatchers resume work safely.

## Disk And Log Growth

MVP logging:
- Docker stdout/stderr.
- application-level sensitive-data masking.
- durable audit/domain records for business events.
- cleanup-eligible diagnostics/attempt records use `retain_until`.

Checks:
- host disk usage.
- Docker log growth.
- PostgreSQL volume usage.
- old diagnostic/delivery/broadcast recipient cleanup execution.
- manual cleanup command: `python manage.py cleanup_retained_records`.
- heartbeat command for runbook checks: `python manage.py record_worker_heartbeat <component> --queue <queue> --worker <worker>`.
- maintenance tasks: `audit.cleanup_expired_retained_records`, `audit.refresh_worker_health`, `audit.heartbeat`, and `appointments.expire_pending_proposals`.

Operational rule:
- core business history is retained while partner data is retained.
- noisy technical records should not be stored forever.

## Reminder Incident Flow

If reminders are late:
1. Check health dashboard due reminder lag.
2. Check `celery-beat`.
3. Check `notifications-worker`.
4. Check Redis.
5. Check PostgreSQL connectivity.
6. Inspect failed/stale notification diagnostics.
7. Restart `notifications-worker` and/or `celery-beat` if needed.
8. Confirm pending reminders resume from durable database records.

Do not manually send reminders from logs. Use system retry/manual resend actions where implemented.

## Appointment Proposal Checks

If a proposal remains pending after its 48-hour expiry:
1. Check that `celery-beat` is running.
2. Check that a worker consumes the `maintenance` queue.
3. Inspect the `appointments.expire_pending_proposals` task logs.
4. Restart beat or the maintenance-capable worker if needed.
5. Confirm the proposal becomes expired and its durable client/master expiry notifications are queued once.

Proposal delivery and response notifications run through the `notifications` queue. A superseded, accepted, declined, cancelled, or expired proposal's original pending notification must become stale rather than send as current.

## Broadcast Incident Flow

If a broadcast is not progressing:
1. Check broadcast campaign counters.
2. Check `broadcast-worker`.
3. Check Redis.
4. Check Telegram 429/backoff diagnostics.
5. Check recipient delivery records.
6. Restart `broadcast-worker` if stuck.
7. Confirm sending resumes from queued recipients without duplicate sends.

Manual retry of failed broadcast recipients is post-MVP unless real usage forces earlier implementation.

## Bot Runner Incident Flow

If one bot fails:
- check partner/bot status and setup.
- check token diagnostics.
- check partner-visible admin bot health.
- fix token/settings where needed.
- restart/redeploy `bot-runner` if runtime needs to reload active bot configs.

If all bots fail:
- check `bot-runner` service.
- check Telegram network/runtime diagnostics.
- check shared encryption key configuration.
- check duplicate token diagnostics.
- restart `bot-runner` after fixing configuration.

## Sensitive Data Rules In Operations

Operators must not ask partners for raw token screenshots if avoidable.

Allowed:
- masked token/status view.
- partner re-enters/replaces token.
- diagnostics with masked error codes/messages.

Forbidden:
- raw tokens in logs, screenshots, issue text, audit payloads, platform diagnostics, or support notes.
- raw phone/message/private-note text in technical logs or platform diagnostics.

## Still Implementation-Specific

To finalize in later pilot/admin slices:
- polished onboarding checklist and dedicated partner creation form
- a dedicated composed health dashboard URL beyond current Django admin records
- production TLS/reverse-proxy and backup/restore commands for the chosen VPS
- per-bot broadcast parallelism beyond the conservative pilot dispatcher
