"""Operator CLI. Mutating provider commands always require --apply and activation flags."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from .campaign import load_campaign
from .config import Settings
from .database import Database
from .providers import WarmyClient
from .provisioning import apply as apply_provisioning
from .provisioning import plan as provisioning_plan
from .scout_bridge import contacts_from_csv, enqueue_contacts


def _json(value) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    enqueue = commands.add_parser("enqueue-contacts")
    enqueue.add_argument("csv")
    enqueue.add_argument("--run-id", default="")

    reconcile = commands.add_parser("reconcile-csv")
    reconcile.add_argument("csv")
    reconcile.add_argument("--run-id", default="reconcile-preview")

    provision = commands.add_parser("provision")
    provision.add_argument("--apply", action="store_true")

    campaign = commands.add_parser("create-campaign-draft")
    campaign.add_argument("manifest")
    campaign.add_argument("--apply", action="store_true")

    start = commands.add_parser("start-campaign")
    start.add_argument("--apply", action="store_true")

    commands.add_parser("doctor")
    commands.add_parser("replay-dead-letters")
    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "doctor":
        _json(
            {
                "database_path": settings.database_path,
                "database_ready": Database(settings.database_path).healthcheck(),
                "warmy_key_configured": bool(settings.warmy_api_key),
                "pipedrive_configured": bool(
                    settings.pipedrive_api_token and settings.pipedrive_domain
                ),
                "gmail_delegation_configured": bool(
                    settings.gmail_service_account_json
                ),
                "provider_writes_enabled": settings.provider_writes_enabled,
                "campaign_activation_ready": settings.campaign_activation_ready,
            }
        )
        return 0
    if args.command == "replay-dead-letters":
        replayed = Database(settings.database_path).replay_dead_letters()
        _json({"replayed": replayed})
        return 0
    if args.command == "reconcile-csv":
        contacts = contacts_from_csv(args.csv, args.run_id)
        ids = [item.outreach_id for item in contacts]
        emails = [item.email for item in contacts]
        _json(
            {
                "rows_ready": len(contacts),
                "unique_outreach_ids": len(set(ids)),
                "duplicate_outreach_ids": len(ids) - len(set(ids)),
                "unique_emails": len(set(emails)),
                "duplicate_emails": len(emails) - len(set(emails)),
            }
        )
        return 0
    if args.command == "provision":
        _json(
            apply_provisioning(settings) if args.apply else provisioning_plan(settings)
        )
        return 0
    if args.command == "create-campaign-draft":
        manifest = load_campaign(args.manifest, settings)
        if not args.apply:
            _json(manifest.model_dump(mode="json"))
            return 0
        settings.require_provider_writes()
        warmy = WarmyClient(settings)
        try:
            _json(
                warmy.create_campaign(
                    manifest.model_dump(mode="json"), "aether-campaign-evergreen-v1"
                )
            )
        finally:
            warmy.close()
        return 0
    if args.command == "start-campaign":
        if not args.apply:
            _json(
                {
                    "would_start": settings.warmy_campaign_id,
                    "ready": settings.campaign_activation_ready,
                }
            )
            return 0
        settings.require_campaign_activation()
        warmy = WarmyClient(settings)
        try:
            _json(
                warmy.start_campaign(
                    settings.warmy_campaign_id, "aether-campaign-start-v1"
                )
            )
        finally:
            warmy.close()
        return 0

    run_id = args.run_id or datetime.now(UTC).strftime("scout-%Y%m%dT%H%M%SZ")
    db = Database(settings.database_path)
    created, total = enqueue_contacts(db, args.csv, run_id)
    _json({"run_id": run_id, "enqueued": created, "eligible": total})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - CLI boundary renders a concise error
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
