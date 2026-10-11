# BoteraMaster Diagnostics Taxonomy

Status: approved MVP platform-diagnostic taxonomy pending exact enum/class names during implementation.
Last updated: 2026-10-02.

This document defines platform-only diagnostic categories and event codes for MVP. Partner-visible Activity Log remains business-friendly and partner-scoped. Platform diagnostics are for superuser/developer operations and must use masked/redacted payloads.

## Principles

- Platform diagnostics are superuser/developer-facing, not partner-facing by default.
- Partner-facing errors must be business-friendly and must not expose raw technical payloads.
- Raw bot tokens, phones, message text, private notes, and raw Telegram payloads must not be stored in diagnostics.
- Diagnostics use `retain_until`; default retention is 3 months unless a stricter type-specific retention is approved.
- Diagnostics should be structured enough for filtering by component, severity, code, partner, bot, queue, appointment, campaign, or worker where applicable.
- High-volume repeated events should be deduplicated/aggregated where practical.

## Severity Levels

Use these severities:
- `info`: useful operational event, not a problem.
- `warning`: degraded behavior or recoverable problem.
- `error`: failed operation requiring attention or automatic recovery.
- `critical`: system component or core flow is down/broken.

Health UI maps diagnostics into OK / Warning / Critical summaries, but diagnostic severity remains more granular.

## Common Diagnostic Fields

Recommended fields:
- `component`
- `severity`
- `code`
- `message_masked`
- `metadata_masked`
- `partner`, nullable
- `bot_instance`, nullable
- `appointment`, nullable
- `broadcast_campaign`, nullable
- `notification`, nullable
- `queue`
- `worker`
- `retain_until`
- `created_at`

Implementation may keep these in `PlatformDiagnostic` plus optional target fields/metadata, as long as sensitive data is redacted before persistence.

## Component Names

Recommended component values:
- `web`
- `bot_runner`
- `telegram`
- `celery_beat`
- `notifications_worker`
- `broadcast_worker`
- `default_worker`
- `queue`
- `postgres`
- `redis`
- `cleanup`
- `security`
- `content`
- `scheduler`
- `health`

## Bot Runner Diagnostics

Codes:
- `bot_runner.startup_started`
- `bot_runner.startup_completed`
- `bot_runner.startup_failed`
- `bot_runner.shutdown_started`
- `bot_runner.shutdown_completed`
- `bot_runner.active_config_loaded`
- `bot_runner.active_config_skipped`
- `bot_runner.duplicate_token_detected`
- `bot_runner.token_decrypt_failed`
- `bot_runner.token_validation_failed`
- `bot_runner.polling_started`
- `bot_runner.polling_stopped`
- `bot_runner.polling_failed`
- `bot_runner.partner_not_active`
- `bot_runner.bot_not_active`

Severity guidance:
- startup/polling failed: error or critical depending on scope.
- duplicate token: error.
- token decrypt failed for active bot: error.
- inactive/skipped config: info or warning depending on why.

Partner visibility:
- partner may see a business-friendly bot status/error summary.
- raw diagnostic details remain platform-only.

## Background Job Diagnostics

Codes:
- `job.dispatch_started`
- `job.dispatch_completed`
- `job.dispatch_failed`
- `job.task_started`
- `job.task_completed`
- `job.task_failed`
- `job.retry_scheduled`
- `job.max_retries_exhausted`
- `job.idempotency_duplicate_skipped`
- `job.stale_record_skipped`
- `job.lock_unavailable_or_timeout`

Severity guidance:
- individual retryable failure: warning.
- max retries exhausted: error.
- repeated dispatcher failure: critical if it blocks reminders/service messages.

Notes:
- Do not store raw task args if they may contain sensitive values.
- Store target IDs and redacted metadata instead.

## Queue And Worker Health Diagnostics

Codes:
- `health.beat_stale`
- `health.notifications_ping_stale`
- `health.broadcasts_ping_stale`
- `health.due_reminder_lag_warning`
- `health.due_reminder_lag_critical`
- `health.broadcast_no_progress_warning`
- `health.broadcast_no_progress_critical`
- `health.worker_offline`
- `health.worker_recovered`
- `health.queue_backlog_warning`
- `health.queue_backlog_critical`

Severity guidance:
- Warning/Critical follows thresholds in `RUNBOOK_DRAFT.md`.
- Recovery events are info.

Notes:
- Current health state belongs in `WorkerHeartbeat` / health records.
- Noisy history belongs in diagnostics with retention.

## Telegram Diagnostics

Codes:
- `telegram.send_failed_retryable`
- `telegram.send_failed_terminal`
- `telegram.rate_limited`
- `telegram.bot_blocked_by_user`
- `telegram.chat_not_found`
- `telegram.user_deactivated`
- `telegram.invalid_token`
- `telegram.unauthorized`
- `telegram.network_error`
- `telegram.api_error`
- `telegram.payload_redacted`

Severity guidance:
- retryable send failure: warning.
- terminal send failure for a client: warning, plus partner-visible failed delivery where useful.
- invalid token/unauthorized for active bot: error.
- broad Telegram/network outage: critical if many bots/jobs are affected.

Rules:
- Raw Telegram payloads are not persisted without filtering/masking.
- Partner-facing failure reason uses business-friendly labels, not raw Telegram text.

## Invalid Token / Security Diagnostics

Codes:
- `security.encryption_key_missing`
- `security.encryption_key_invalid`
- `security.decrypt_failed`
- `security.token_fingerprint_failed`
- `security.sensitive_payload_redacted`
- `security.raw_sensitive_payload_blocked`
- `security.invalid_bot_token_format`
- `security.invalid_bot_token_runtime`

Severity guidance:
- missing/invalid encryption key: critical if active bots cannot run.
- decrypt failed for active bot: error.
- raw sensitive payload blocked/redacted: warning unless repeated/systemic.

Rules:
- Never include raw token, raw phone, raw message text, or raw private note.

## Broadcast Diagnostics

Codes:
- `broadcast.campaign_queued`
- `broadcast.campaign_started`
- `broadcast.campaign_completed`
- `broadcast.campaign_cancelled`
- `broadcast.campaign_failed`
- `broadcast.recipient_skipped_marketing_opt_out`
- `broadcast.recipient_skipped_unreachable`
- `broadcast.recipient_send_failed`
- `broadcast.backoff_started`
- `broadcast.backoff_finished`
- `broadcast.no_progress`
- `broadcast.resume_after_worker_restart`

Severity guidance:
- campaign lifecycle: info.
- no progress/backoff: warning or critical according to health thresholds.
- recipient send failed: warning when aggregated; not one noisy diagnostic per high-volume expected failure unless needed.

## Notification / Reminder Diagnostics

Codes:
- `notification.scheduled`
- `notification.sent`
- `notification.skipped_stale`
- `notification.skipped_quiet_hours_obsolete`
- `notification.send_failed_retryable`
- `notification.send_failed_terminal`
- `notification.retry_scheduled`
- `notification.max_retries_exhausted`
- `notification.manual_retry_requested`
- `notification.manual_retry_succeeded`
- `notification.manual_retry_failed`

Severity guidance:
- stale/skipped expected: info.
- retryable failed: warning.
- max retries exhausted: error and partner-visible marker/action where useful.

## Cleanup Diagnostics

Codes:
- `cleanup.started`
- `cleanup.completed`
- `cleanup.failed`
- `cleanup.deleted_records`
- `cleanup.retention_misconfigured`
- `cleanup.protected_business_record_skipped`

Severity guidance:
- cleanup completed/deleted records: info.
- cleanup failed: warning or error depending on repeated failures.
- retention misconfigured or attempted business-record deletion: error.

Rules:
- Cleanup diagnostics must not include raw sensitive row payloads.
- Log model names, counts, cutoff timestamps, and masked identifiers only.

## PostgreSQL / Redis Diagnostics

Codes:
- `postgres.connection_failed`
- `postgres.migration_pending`
- `postgres.disk_pressure`
- `postgres.slow_or_blocked_queries`
- `redis.connection_failed`
- `redis.queue_connection_failed`
- `redis.reconnected`

Severity guidance:
- connection failed for active app/worker: critical.
- migration pending after deploy: error.
- disk pressure: warning/critical depending on threshold.
- Redis reconnect: info if recovered, warning if repeated.

## Content / Template Diagnostics

Codes:
- `content.system_default_missing`
- `content.template_render_failed`
- `content.partner_override_invalid`
- `content.placeholder_invalid`
- `content.fallback_used`

Severity guidance:
- missing system default for runtime key: error or critical if it blocks bot flow.
- partner override invalid at save time should usually be validation error, not diagnostic.
- fallback used: info/warning depending on frequency.

## Stranded / Stale Work Diagnostics

Codes:
- `scheduler.due_work_stale`
- `scheduler.stranded_notification`
- `scheduler.stranded_broadcast_recipient`
- `scheduler.stranded_requested_reminder`
- `scheduler.stranded_proposal_expiration`
- `scheduler.recovered_stranded_work`

Severity guidance:
- stale due work affecting reminders/service messages: warning/error.
- recovered stranded work: info.
- repeated stranded work: error.

## Partner-Visible Audit vs Platform Diagnostics

Partner-visible AuditEvent examples:
- appointment created/updated/cancelled/status changed
- penalty created/paid/waived
- expense created/updated/deleted
- broadcast created/scheduled/started/completed
- reminder failed after retries
- bot/settings changed

PlatformDiagnostic examples:
- Telegram API returned unauthorized
- token decrypt failed
- worker heartbeat stale
- Redis unavailable
- raw Telegram payload redacted
- cleanup job failed

Rule:
- If a partner can act on it directly, create or update a business-friendly partner-visible state/event.
- If it is infrastructure/runtime/debug detail, keep it platform-only and masked.

## Still Implementation-Specific

To finalize during coding:
- exact enum/class names
- exact metadata schema per code
- exact aggregation/deduplication behavior for high-volume diagnostics
- exact superuser filters and dashboard layout
