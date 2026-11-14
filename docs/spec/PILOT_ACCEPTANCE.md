# BoteraMaster Pilot Acceptance

Status: completed on 2026-11-11 after `PILOT_ACCEPTANCE_APPROVED` on
2026-11-09.

This document is the release-candidate evidence map for the first pilot. It
does not add product behavior. Each row must end with either reproducible
automated evidence or an explicit live/manual check where Telegram itself is
the system under test.

## Evidence Rules

- `Automated` means a committed test or management/script command with a
  deterministic pass/fail result.
- `Live` means an interaction with the configured test bot plus durable state
  verification in PostgreSQL/admin; a chat screenshot alone is not enough.
- Sensitive values, especially raw bot tokens, phone numbers, message text,
  and private notes, must not be copied into this document or command output.
- Acceptance fixes must preserve tenant scope, service ownership, transaction
  boundaries, idempotency, and post-commit network delivery.

## Acceptance Matrix

| Area | Release-critical scenario | Evidence | 2026-11-11 result |
| --- | --- | --- | --- |
| Bootstrap | A fresh Compose project applies migrations, synchronizes content/owner access, seeds safe demo data, and reaches healthy services | `scripts/check_clean_bootstrap.sh` | Pass: isolated project became healthy, reran idempotent initialization, and removed its containers/volumes |
| Database | Full test suite runs against Compose PostgreSQL, including advisory-lock behavior | Host and Compose PostgreSQL suites | Pass: host `437 passed`; PostgreSQL `434 passed, 3 skipped` |
| Tenant security | Owner/client mutations cannot cross partner boundaries | Full regression suites | Pass |
| Bot security | Tokens remain encrypted/masked and duplicate active tokens are rejected | Full regression suites and active masked test-bot configuration | Pass |
| Client booking | Client selects multiple services and a slot; auto/manual/near-fit paths create the correct appointment state | Router/domain tests plus durable live pilot records | Pass: confirmed and final appointment records remain queryable after restarts |
| Master workspace | Linked master can process requests and safe appointment actions without becoming a client | Router/domain tests plus live test-bot navigation | Pass: the linked master opened the master workspace and remained distinct from client runtime scope |
| Scheduling | Confirmed overlaps are rejected under the booking-resource lock; requested overlaps remain possible | PostgreSQL concurrency and service tests | Pass |
| Notifications | Durable notifications are idempotent, retry safely, and respect quiet hours/staleness | Worker/service tests plus live Telegram delivery | Pass: a temporary network failure retried once and reached `sent` without changing the completed business operation |
| Restart recovery | Restarting runner/workers resumes durable pending work without duplicate sends | Compose restart smoke and durable-record comparison | Pass: one eligible bot loaded before/after restart; appointment, notification, and message counts remained intact |
| Proposals | Reschedule/alternative proposals revalidate availability, expire safely, and preserve the original confirmed slot until acceptance | Full regression suites plus durable accepted pilot proposal | Pass |
| Messaging | Client/master messages queue asynchronously and terminal delivery failure updates partner-scoped reachability | Full regression suites plus durable sent pilot messages | Pass: five sent conversation records survived restart smoke |
| Penalties | Late-cancel, no-show, late-arrival, apply/release/pay/waive flows preserve totals and notify once | Finance tests plus focused live late-arrival action | Pass: test penalty was created, notified, waived, notified after automatic retry, and left no active debt |
| Broadcasts | Recipient filtering, pacing, resume, internal buttons, and photo delivery do not duplicate recipients | Broadcast tests plus one live targeted campaign | Pass: dry-run selected one expected profile; campaign completed with exactly one sent recipient and no failures/skips |
| Admin | Partner views remain scoped and core calendar/catalog/client/appointment workflows are usable on desktop and mobile | Access tests plus Playwright desktop/mobile smoke | Pass: dashboard/calendar rendered cleanly and seven key owner routes returned `200` without server errors at mobile width |
| Diagnostics | Health/readiness surfaces report real runner/worker/database/broker state with masked errors | Selector/command tests plus Compose health check | Pass: web, runner, beat, three workers, PostgreSQL, Redis, delivery lag, broadcast progress, and diagnostics reported healthy |
| Production config | Production settings and merged Compose config reject unsafe inputs and expose only Caddy | Focused production/operations/Docker tests | Pass: `21 passed` |
| Recovery | Backup archive verifies and restores only into an isolated rehearsal database | `scripts/rehearse_postgres_restore.sh` | Pass: current-schema archive restored into the fixed rehearsal database and the temporary database was dropped |

## Standard Automated Baseline

```bash
.venv/bin/python -m pytest -q
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/ruff check .
./scripts/compose.sh dev exec web env \
  DJANGO_SETTINGS_MODULE=core.settings.postgres_test pytest -q
./scripts/check_clean_bootstrap.sh .env
```

The acceptance milestone may add a single orchestration script for reproducible
bootstrap/release smoke checks, but it must call the same documented project
entry points rather than bypassing them.

## Live Pilot Evidence

Use the configured test partner and test bot. Verify durable appointment,
notification, proposal, message, penalty, broadcast, audit, and delivery records
after the Telegram interaction. Record only pass/fail and masked identifiers.
The detailed interaction order remains in `RUNBOOK_DRAFT.md` under
**End-To-End Pilot Check**.

### Acceptance run: 2026-11-11

- Test bot connection: active and encrypted/masked in application state.
- Appointment completion notification: `sent` to the one linked test profile.
- Targeted broadcast campaign `...e5a70`: one eligible recipient, one `sent`,
  zero failed/skipped, and the internal **My appointments** button included.
- Late-arrival penalty `...c5b1`: created for the test profile, then waived.
  The create notification was sent immediately. The waiver notification hit
  one transient `telegram_network_error`, stayed durable, retried once at the
  scheduled time, and became `sent` without a duplicate record.
- Penalties were returned to their original disabled setting after the check;
  the test penalty remains waived for audit evidence and creates no debt.
- Playwright checked the partner dashboard and calendar visually at desktop
  and mobile widths. Dashboard, calendar, appointments, clients, catalog,
  broadcasts, and Bot workspace routes returned `200` for the owner account.
  The only browser console error was a non-functional missing `favicon.ico`.

## Exit Record

Recorded exit evidence:

- Tested working tree: acceptance patch based on revision `ded76a6`.
- Host suite: `437 passed in 44.57s`.
- PostgreSQL suite: `434 passed, 3 skipped in 44.49s`.
- Django system check: no issues; migrations: no changes detected; Ruff and
  `git diff --check`: passed.
- Clean bootstrap: passed in an isolated Compose namespace and cleaned up.
- Restart/recovery: passed with durable business state preserved.
- Production/operations/Docker focused suite: `21 passed in 19.74s`.
- Backup verification and isolated restore rehearsal: passed.
- Live Telegram completion, retry, penalty/waiver, and targeted broadcast
  scenarios: passed.

Non-blocking first-pilot limitations:

- Public DNS/TLS/VPS deployment was not performed in this local acceptance
  run; production settings and merged Compose topology were validated instead.
- The live campaign used text plus an internal button. Photo delivery remains
  covered by automated adapter/service tests rather than this live recipient.
- Mobile acceptance is a focused smoke at 390 px, not an exhaustive browser and
  device matrix.
- Python dependencies currently use the project's approved version ranges,
  not a newly introduced lock file. Exact dependency pinning remains a separate
  change that requires explicit dependency approval under the project rules.
