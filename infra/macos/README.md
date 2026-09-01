# Mac-local sales integration

The sales integration runs as three user LaunchAgents on one persistent Mac:

- `com.aether.sales-api` keeps the FastAPI webhook and unsubscribe service alive on
  `127.0.0.1:8187` and uses `caffeinate` while the Mac is on AC power.
- `com.aether.sales-worker` wakes every minute, leases a bounded batch from
  `aether_sales.sqlite`, processes it, and exits.
- `com.aether.sales-tunnel` keeps the configured Cloudflare Tunnel connected to the
  local API.

The Scout pipeline uses the same SQLite file when it enqueues contacts. SQLite WAL mode
allows the API, Scout, and worker processes to safely share the file.

## Initial setup

1. Run `uv sync` and copy `.env.example` to `.env` if needed.
2. Set `AETHER_SALES_DB_PATH` to a path on the Mac's internal persistent disk. The
   default is `aether_sales.sqlite` in the repository root.
3. Leave every activation flag false while configuring credentials and provider fields.
4. Run `uv run python -m integration.cli doctor`.
5. Start the API manually with `./run-sales-api.sh`, then check
   `curl http://127.0.0.1:8187/healthz`.
6. Run one worker pass with `./run-sales-worker.sh`.
7. Install `cloudflared`, create a named tunnel configuration at
   `~/.cloudflared/aec-sales.yml` that forwards the public hostname to
   `http://127.0.0.1:8187`, and verify it manually.
8. Install the background jobs with `./infra/macos/install-launch-agents.sh`.

Install from the permanent repository checkout. The installer intentionally refuses to
bind launchd to a temporary `.codex/worktrees/` path because that checkout can later be
removed.

The LaunchAgents run only while this macOS user is logged in. Keep the Mac connected to
power and the network. `caffeinate` keeps it awake while the API process is running.

## Public HTTPS endpoint

Warmy, Pipedrive, and unsubscribe links need a stable public HTTPS URL. The included
LaunchAgent expects a named Cloudflare Tunnel in front of `http://127.0.0.1:8187`.
Set `PUBLIC_BASE_URL` to that tunnel's stable origin, then configure:

- Warmy webhook: `PUBLIC_BASE_URL/webhooks/warmy`
- Pipedrive webhook: `PUBLIC_BASE_URL/webhooks/pipedrive`
- Unsubscribe links are generated under `PUBLIC_BASE_URL/unsubscribe`

The installer verifies that `cloudflared` and `~/.cloudflared/aec-sales.yml` exist
before changing any LaunchAgents. Tunnel DNS, account setup, and credentials remain
operator-owned and are never stored in this repository.

## Operations

Inspect health and logs:

```bash
curl http://127.0.0.1:8187/healthz
tail -f logs/sales-api.log logs/sales-worker.log logs/sales-tunnel.log
launchctl print gui/$UID/com.aether.sales-api
launchctl print gui/$UID/com.aether.sales-worker
launchctl print gui/$UID/com.aether.sales-tunnel
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

The uninstaller moves only the three generated plist files to Trash. It does not delete
the SQLite database, logs, `.env`, or provider data.

## Backup

Back up the live database using SQLite's backup command so the WAL is included:

```bash
sqlite3 aether_sales.sqlite ".backup 'aether_sales-backup.sqlite'"
```

Protect the backup like a credential because it contains lead emails and provider IDs.
