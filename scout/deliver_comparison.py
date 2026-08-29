"""Send or monitor the two comparison reports using exactly-once Gmail guards."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v2.delivery import ExactlyOnceDelivery, GogMailGateway, monitor_comparison_day


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--recipient", action="append", default=[])
    parser.add_argument("--v1-subject", required=True)
    parser.add_argument("--v2-subject", required=True)
    parser.add_argument("--v1-html", type=Path, required=True)
    parser.add_argument("--v2-html", type=Path, required=True)
    parser.add_argument("--v1-manifest", type=Path, required=True)
    parser.add_argument("--v2-manifest", type=Path, required=True)
    parser.add_argument("--monitor", action="store_true", help="read-only verification; never send")
    args = parser.parse_args(argv)
    gateway = GogMailGateway(args.account)
    subjects = [args.v1_subject, args.v2_subject]
    manifests = [args.v1_manifest, args.v2_manifest]
    if args.monitor:
        result = monitor_comparison_day(
            gateway, subjects=subjects, manifest_paths=manifests
        )
        print(json.dumps({"ok": result.ok, "problems": result.problems}, indent=2))
        return 0 if result.ok else 1

    # Pair-level preflight prevents sending one report when the other already collides.
    collisions = {subject: list(gateway.search_sent_exact(subject)) for subject in subjects}
    if any(collisions.values()):
        print(json.dumps({"sent": False, "collisions": collisions}, indent=2))
        return 1
    delivery = ExactlyOnceDelivery(gateway, args.account)
    results = []
    for subject, html_path, manifest in zip(
        subjects, [args.v1_html, args.v2_html], manifests, strict=True
    ):
        result = delivery.deliver(
            subject=subject,
            recipients=args.recipient,
            html=html_path.read_text(encoding="utf-8"),
            manifest_paths=[manifest],
        )
        results.append(
            {
                "subject": result.subject,
                "message_id": result.message_id,
                "recipients": result.recipients,
                "recovered": result.recovered_after_uncertain_send,
            }
        )
    print(json.dumps({"sent": True, "messages": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
