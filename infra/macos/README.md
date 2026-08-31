# Mac-local sales integration

The sales integration runs as two user LaunchAgents on one persistent Mac:

- `com.aether.sales-api` keeps the FastAPI webhook and unsubscribe service alive on
  `127.0.0.1:8080` and uses `caffeinate` while the Mac is on AC power.
- `com.aether.sales-worker` wakes every minute, leases a bounded batch from
  `aether_sales.sqlite`, processes it, and exits.

The Scout pipeline uses the same SQLite file when it enqueues contacts. SQLite WAL mode
allows the API, Scout, and worker processes to safely share the file.

## Initial setup

1. Run `uv sync` and copy `.env.example` to `.env` if needed.
2. Set `AETHER_SALES_DB_PATH` to a path on the Mac's internal persistent disk. The
   default is `aether_sales.sqlite` in the repository root.
3. Leave every activation flag false while configuring credentials and provider fields.
4. Run `uv run python -m integration.cli doctor`.
5. Start the API manually with `./run-sales-api.sh`, then check
   `curl http://127.0.0.1:8080/healthz`.
6. Run one worker pass with `./run-sales-worker.sh`.
7. Install the background jobs with `./infra/macos/install-launch-agents.sh`.

Install from the permanent repository checkout. The installer intentionally refuses to
bind launchd to a temporary `.codex/worktrees/` path because that checkout can later be
removed.

The LaunchAgents run only while this macOS user is logged in. Keep the Mac connected to
power and the network. `caffeinate` keeps it awake while the API process is running.

## Public HTTPS endpoint

Warmy, Pipedrive, and unsubscribe links need a stable public HTTPS URL. Put a named
Cloudflare Tunnel, Tailscale Funnel, or equivalent HTTPS tunnel in front of
`http://127.0.0.1:8080`, then set `PUBLIC_BASE_URL` to that stable origin. Configure:

- Warmy webhook: `PUBLIC_BASE_URL/webhooks/warmy`
- Pipedrive webhook: `PUBLIC_BASE_URL/webhooks/pipedrive`
- Unsubscribe links are generated under `PUBLIC_BASE_URL/unsubscribe`

The tunnel is deliberately not installed by this repository because its DNS/account
choice and credentials are operator-owned.

## Operations

Inspect health and logs:

```bash
curl http://127.0.0.1:8080/healthz
tail -f logs/sales-api.log logs/sales-worker.log
launchctl print gui/$UID/com.aether.sales-api
launchctl print gui/$UID/com.aether.sales-worker
```

Activation-blocked jobs are deferred without consuming retries. After correcting a
provider outage or configuration fault, explicitly replay any older dead letters:

```bash
uv run python -m integration.cli replay-dead-letters
```

Stop and remove the LaunchAgents:

```bash
./infra/macos/uninstall-launch-agents.sh
```

The uninstaller moves only the two generated plist files to Trash. It does not delete
the SQLite database, logs, `.env`, or provider data.

## Backup

Back up the live database using SQLite's backup command so the WAL is included:

```bash
sqlite3 aether_sales.sqlite ".backup 'aether_sales-backup.sqlite'"
```

Protect the backup like a credential because it contains lead emails and provider IDs.
