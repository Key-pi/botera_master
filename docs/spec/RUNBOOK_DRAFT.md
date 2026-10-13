# BoteraMaster MVP Runbook Draft

Status: approved operational direction pending exact command names from implementation.
Last updated: 2026-10-02.

This runbook defines what MVP operations must cover. Exact command names may change when the real Docker Compose services, Django commands, and health views exist, but the operational flows should stay stable.

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
- `default-worker`: default and maintenance jobs, initial concurrency 1.

Rules:
- `broadcasts` must not share the urgent notification worker in production MVP.
- `celery-beat` / scheduler must run explicitly; scheduled features are not supported if beat is down.
- Docker Compose restart policies and healthchecks are enough for MVP process supervision.
- Kubernetes/heavier orchestration is not required for MVP.
- Sentry or equivalent error monitoring is nice-to-have/post-MVP.

## Standard Deploy / Restart Procedure

Recommended flow:
1. Pull or build the application image.
2. Stop or restart services through Docker Compose according to the chosen deployment style.
3. Run database migrations before accepting normal traffic.
4. Start or restart all services.
5. Verify the superuser health dashboard.
6. Check recent platform diagnostics for startup/runtime errors.
7. Confirm at least one known active partner bot is polling successfully.

Command placeholders:
- `docker compose pull` or `docker compose build`
- `docker compose run --rm web <migrate command>`
- `docker compose up -d`
- `docker compose ps`
- `docker compose logs --tail=200 <service>`

Exact command names are finalized during implementation, after the project command layout exists.

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
- maintenance tasks: `audit.cleanup_expired_retained_records`, `audit.refresh_worker_health`, and `audit.heartbeat`.

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

To finalize during coding:
- exact Docker Compose file/service names if they differ from the recommended names
- exact Django management commands
- exact health dashboard URLs
- exact migration/deploy command
- exact log inspection commands for production host
