"""Frozen-corpus parity harness for Phase 1.

This module never calls an LLM. It only:
  - captures article text from campaign-selected sources,
  - emits prompt packets for an in-session judgment pass, and
  - compares recorded judgments against the old baseline.

Usage:
  uv run python -m tests.parity.harness capture --limit 30
  uv run python -m tests.parity.harness golden
  uv run python -m tests.parity.harness golden --judgments /path/to/golden_old.json
  uv run python -m tests.parity.harness compare --judgments /path/to/golden_new.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import feedparser

from pipeline import extract, fetch, prompts, util
from pipeline.config import ROOT
from pipeline.spec import CampaignSpec, load_campaign_spec

CORPUS_DIR = ROOT / "tests" / "fixtures" / "corpus"
MANIFEST = CORPUS_DIR / "manifest.json"
GOLDEN_OLD = ROOT / "tests" / "fixtures" / "golden_old.json"
GOLDEN_NEW = ROOT / "tests" / "fixtures" / "golden_new.json"
GOLDEN_PACKETS = CORPUS_DIR / "golden_prompt_packets.jsonl"
COMPARE_PACKETS = CORPUS_DIR / "compare_prompt_packets.jsonl"
PARITY_RESULTS = ROOT / "PARITY-RESULTS.md"


@dataclass(frozen=True)
class CorpusItem:
    article_id: str
    path: Path
    url: str
    title: str
    source: str


def _load_manifest() -> list[dict[str, Any]]:
    if not MANIFEST.exists():
        raise SystemExit(f"missing corpus manifest: {MANIFEST}")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _corpus_items() -> list[CorpusItem]:
    items = []
    for row in _load_manifest():
        path = CORPUS_DIR / f"{row['id']}.txt"
        if not path.exists():
            raise SystemExit(f"manifest references missing article text: {path}")
        items.append(CorpusItem(
            article_id=row["id"],
            path=path,
            url=row["url"],
            title=row.get("title") or "",
            source=row.get("source") or "",
        ))
    return items


def _read_json_records(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        records = raw
    elif isinstance(raw, list):
        records = {str(item["id"]): item for item in raw}
    else:
        raise SystemExit(f"{path} must contain a JSON object or array")
    for article_id, item in records.items():
        missing = {"priority", "az_relevant", "confidence", "signal_type"} - set(item)
        if missing:
            raise SystemExit(f"{path} record {article_id} missing {sorted(missing)}")
    return {str(k): v for k, v in records.items()}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _capture(args: argparse.Namespace, spec: CampaignSpec) -> int:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists() and not args.overwrite:
        raise SystemExit(f"{MANIFEST} exists; pass --overwrite to replace the corpus")

    manifest: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    sources = fetch.sources_for_campaign(spec)
    rendered_urls = []
    with util.make_http_client() as http:
        for src in sources:
            handler = fetch.METHOD_HANDLERS.get(src["method"])
            if handler is None:
                continue
            feed_url = handler(src["endpoint"])
            try:
                resp = http.get(feed_url)
                resp.raise_for_status()
            except Exception as exc:
                print(f"source_failed {src['name']}: {exc!r}", file=sys.stderr)
                continue
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries:
                link = getattr(entry, "link", None)
                if not link:
                    continue
                try:
                    url = util.canonicalize_url(link)
                except ValueError:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                rendered_urls.append((src, entry, url))

    with util.make_http_client() as http:
        for src, entry, url in rendered_urls:
            if len(manifest) >= args.limit:
                break
            article_id = f"{len(manifest) + 1:03d}"
            try:
                text = extract.extract_article_text(url, http)
            except Exception as exc:
                print(f"extract_failed {url}: {exc!r}", file=sys.stderr)
                continue
            (CORPUS_DIR / f"{article_id}.txt").write_text(text, encoding="utf-8")
            manifest.append({
                "id": article_id,
                "url": url,
                "title": getattr(entry, "title", "") or "",
                "source": src["name"],
                "fetched_at": util.utc_now_iso(),
            })

    _write_json(MANIFEST, manifest)
    print(f"captured {len(manifest)} articles into {CORPUS_DIR}")
    return 0


def _write_prompt_packets(path: Path, spec: CampaignSpec) -> None:
    assess_prompt = prompts.render_assess_prompt(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item in _corpus_items():
            packet = {
                "id": item.article_id,
                "url": item.url,
                "title": item.title,
                "source": item.source,
                "prompt": assess_prompt,
                "article_text": item.path.read_text(encoding="utf-8"),
            }
            fh.write(json.dumps(packet, sort_keys=True) + "\n")


def _golden(args: argparse.Namespace, spec: CampaignSpec) -> int:
    if args.judgments:
        records = _read_json_records(Path(args.judgments))
        _write_json(GOLDEN_OLD, records)
        print(f"wrote old baseline judgments to {GOLDEN_OLD}")
        return 0
    _write_prompt_packets(GOLDEN_PACKETS, spec)
    print(f"wrote prompt packets to {GOLDEN_PACKETS}")
    print("Record in-session judgments, then rerun with --judgments <json>.")
    return 0


def _is_dropped(record: dict[str, Any]) -> bool:
    if not bool(record["az_relevant"]):
        return True
    if record["priority"] == "low":
        return True
    confidence = float(record["confidence"])
    if record["signal_type"] == "other" and confidence < extract.OTHER_MIN_CONFIDENCE:
        return True
    if confidence < extract.GENERAL_MIN_CONFIDENCE:
        return True
    return False


def _compare(args: argparse.Namespace, spec: CampaignSpec) -> int:
    if not GOLDEN_OLD.exists():
        raise SystemExit(f"missing old baseline: {GOLDEN_OLD}")
    if not args.judgments:
        _write_prompt_packets(COMPARE_PACKETS, spec)
        print(f"wrote prompt packets to {COMPARE_PACKETS}")
        print("Record spec-driven judgments, then rerun compare with --judgments <json>.")
        return 0

    old = _read_json_records(GOLDEN_OLD)
    new = _read_json_records(Path(args.judgments))
    _write_json(GOLDEN_NEW, new)

    all_ids = sorted(set(old) | set(new))
    disagreements = []
    high_to_dropped = []
    for article_id in all_ids:
        old_record = old.get(article_id)
        new_record = new.get(article_id)
        if old_record is None or new_record is None:
            disagreements.append((article_id, "missing_record", old_record, new_record))
            continue
        old_drop = _is_dropped(old_record)
        new_drop = _is_dropped(new_record)
        if (
            old_record["priority"] == "high"
            and not old_drop
            and new_drop
            and not old_record.get("jitter_exempt", False)
        ):
            high_to_dropped.append(article_id)
        fields = ("priority", "az_relevant", "confidence", "signal_type")
        if any(old_record[field] != new_record[field] for field in fields):
            disagreements.append((article_id, "field_diff", old_record, new_record))

    jitter_floor = int(args.jitter_floor)
    status = "PASS" if len(high_to_dropped) <= jitter_floor else "FAIL"
    old_counts = Counter(record["priority"] for record in old.values())
    new_counts = Counter(record["priority"] for record in new.values())

    lines = [
        "# Phase 1 Parity Results",
        "",
        f"Status: **{status}**",
        f"Compared records: {len(all_ids)}",
        f"HIGH-to-dropped regressions: {len(high_to_dropped)}",
        f"Jitter floor: {jitter_floor}",
        "",
        "## Priority Distribution",
        "",
        "| Priority | Old | New |",
        "|---|---:|---:|",
    ]
    for priority in ("high", "medium", "low"):
        lines.append(f"| {priority} | {old_counts[priority]} | {new_counts[priority]} |")
    lines.extend([
        "",
        "## HIGH-to-Dropped",
        "",
    ])
    if high_to_dropped:
        lines.extend(f"- {article_id}" for article_id in high_to_dropped)
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Disagreements",
        "",
    ])
    if disagreements:
        for article_id, kind, old_record, new_record in disagreements:
            lines.append(f"- {article_id}: {kind}")
            lines.append(f"  - old: {json.dumps(old_record, sort_keys=True)}")
            lines.append(f"  - new: {json.dumps(new_record, sort_keys=True)}")
    else:
        lines.append("- None")
    lines.append("")

    PARITY_RESULTS.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {PARITY_RESULTS}")
    return 0 if status == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture", "golden", "compare"))
    parser.add_argument("--campaign", default=None)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--judgments")
    parser.add_argument("--jitter-floor", type=int, default=0)
    args = parser.parse_args(argv)

    spec = load_campaign_spec(args.campaign)
    if args.mode == "capture":
        return _capture(args, spec)
    if args.mode == "golden":
        return _golden(args, spec)
    return _compare(args, spec)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
