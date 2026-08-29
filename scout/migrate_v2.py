"""Preview or apply the local Scout V1-to-V2 state migration."""
from __future__ import annotations

import argparse
import json

import config
from v2.migration import LegacyMigrator


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create a timestamped backup and atomically replace the database with the migrated copy",
    )
    args = parser.parse_args(argv)
    migrator = LegacyMigrator(config.DB_PATH, config.RESULTS_DIR)
    report = migrator.apply() if args.apply else migrator.inventory()
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    if not args.apply:
        print("preview only; pass --apply to migrate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
