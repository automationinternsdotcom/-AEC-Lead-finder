"""Export today's human-review deliverables as XLSX files.

This is intentionally a reporting utility. It reads existing run artifacts and
does not change discovery, qualification, enrichment, or delivery behavior.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

from pipeline.contracts import ArtifactEnvelope, load_artifact


QUALIFIED_HEADERS = [
    "Title",
    "Company",
    "URL",
    "Priority",
    "Signal Type",
    "Property Type",
    "City",
    "Address",
    "Confidence",
    "Score",
    "Published Date",
    "Summary",
    "Filter Reason",
    "Service Angle",
    "Qualified",
]

ENRICHED_HEADERS = [
    "Title",
    "Company",
    "URL",
    "Priority",
    "Signal Type",
    "Property Type",
    "City",
    "Confidence",
    "Service Angle",
    "Grok Mode",
    "Contact #",
    "Contact Name",
    "Contact Title",
    "Contact Email",
    "Contact Phone",
    "LinkedIn",
    "Email Verified",
    "Phone Verified",
    "Notes",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, help="Run directory containing artifacts/.")
    parser.add_argument(
        "--qualified-artifact",
        default=None,
        help="Codex qualification/pattern artifact. Defaults to RUN_DIR/artifacts/pattern.json.",
    )
    parser.add_argument(
        "--enriched-artifact",
        default=None,
        help="Grok enrichment artifact. Defaults to RUN_DIR/artifacts/enriched_leads.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for XLSX files. Defaults to RUN_DIR/deliverables.",
    )
    args = parser.parse_args([] if argv is None else argv)

    run_dir = Path(args.run_dir)
    output_dir = Path(args.output_dir) if args.output_dir else run_dir / "deliverables"
    qualified_artifact = (
        Path(args.qualified_artifact)
        if args.qualified_artifact
        else run_dir / "artifacts" / "pattern.json"
    )
    enriched_artifact = (
        Path(args.enriched_artifact)
        if args.enriched_artifact
        else run_dir / "artifacts" / "enriched_leads.json"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    qualified_records = _artifact_records(qualified_artifact)
    qualified_path = output_dir / "codex-qualified-leads.xlsx"
    _write_xlsx(
        qualified_path,
        [QUALIFIED_HEADERS] + [_qualified_row(record) for record in qualified_records],
        sheet_name="Codex Qualified",
    )

    enriched_path = output_dir / "grok-enriched-leads.xlsx"
    if enriched_artifact.exists():
        enriched_records = _json_records(enriched_artifact)
        _write_xlsx(
            enriched_path,
            [ENRICHED_HEADERS] + _enriched_rows(enriched_records),
            sheet_name="Grok Enriched",
        )
        enriched_output = str(enriched_path)
    else:
        enriched_records = []
        enriched_output = None

    json.dump(
        {
            "qualified": {
                "input": str(qualified_artifact),
                "output": str(qualified_path),
                "records": len(qualified_records),
            },
            "enriched": {
                "input": str(enriched_artifact),
                "output": enriched_output,
                "records": len(enriched_records),
            },
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


def _artifact_records(path: Path) -> list[dict[str, Any]]:
    return load_artifact(path).records


def _json_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [record for record in data if isinstance(record, dict)]
    if isinstance(data, dict):
        try:
            envelope = ArtifactEnvelope.model_validate(data)
        except Exception:
            records = data.get("records")
            if isinstance(records, list):
                return [record for record in records if isinstance(record, dict)]
            return [data]
        return envelope.records
    raise ValueError(f"unsupported JSON shape in {path}")


def _qualified_row(record: dict[str, Any]) -> list[Any]:
    raw = _article(record)
    return [
        raw.get("title") or record.get("title"),
        raw.get("company_name") or record.get("company_name") or record.get("entity_name"),
        raw.get("url") or record.get("url"),
        raw.get("priority"),
        raw.get("signal_type"),
        raw.get("property_type"),
        raw.get("city"),
        raw.get("address"),
        raw.get("confidence"),
        record.get("score"),
        raw.get("published_date"),
        raw.get("summary_2sent"),
        raw.get("filter_reason") or record.get("filter_reason"),
        raw.get("service_angle"),
        record.get("qualified"),
    ]


def _enriched_rows(records: list[dict[str, Any]]) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for record in records:
        article = _article(record)
        leads = _leads(record)
        if not leads:
            rows.append(_enriched_row(record, article, None, None))
            continue
        for idx, lead in enumerate(leads, start=1):
            rows.append(_enriched_row(record, article, idx, lead))
    return rows


def _enriched_row(
    record: dict[str, Any],
    article: dict[str, Any],
    contact_number: int | None,
    lead: dict[str, Any] | None,
) -> list[Any]:
    lead = lead or {}
    return [
        article.get("title") or record.get("title"),
        article.get("company_name") or record.get("company_name"),
        article.get("url") or record.get("url"),
        article.get("priority"),
        article.get("signal_type"),
        article.get("property_type"),
        article.get("city"),
        article.get("confidence"),
        article.get("service_angle"),
        record.get("mode") or (record.get("enrichment") or {}).get("mode"),
        contact_number,
        lead.get("name"),
        lead.get("title"),
        lead.get("email"),
        lead.get("phone"),
        lead.get("linkedin_url"),
        lead.get("email_verified"),
        lead.get("phone_verified"),
        record.get("notes") or record.get("error"),
    ]


def _article(record: dict[str, Any]) -> dict[str, Any]:
    for value in (
        record.get("article"),
        record.get("raw"),
        (record.get("candidate") or {}).get("raw") if isinstance(record.get("candidate"), dict) else None,
    ):
        if isinstance(value, dict):
            return value
    return record


def _leads(record: dict[str, Any]) -> list[dict[str, Any]]:
    leads = record.get("leads")
    if leads is None and isinstance(record.get("enrichment"), dict):
        leads = record["enrichment"].get("leads")
    out: list[dict[str, Any]] = []
    if isinstance(record.get("lead"), dict):
        out.append(record["lead"])
    if isinstance(leads, list):
        out.extend(lead for lead in leads if isinstance(lead, dict))
    extras = record.get("extra_contacts")
    if isinstance(extras, list):
        out.extend(lead for lead in extras if isinstance(lead, dict))

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for lead in out:
        key = (
            str(lead.get("name") or ""),
            str(lead.get("email") or ""),
            str(lead.get("phone") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(lead)
    return deduped


def _write_xlsx(path: Path, rows: list[list[Any]], *, sheet_name: str) -> None:
    sheet = _worksheet_xml(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _rels_xml())
        zf.writestr("xl/workbook.xml", _workbook_xml(sheet_name))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def _worksheet_xml(rows: list[list[Any]]) -> str:
    body = []
    for row_idx, row in enumerate(rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            ref = f"{_col_name(col_idx)}{row_idx}"
            cells.append(_cell_xml(ref, value))
        body.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'
        + "".join(body)
        + "</sheetData></worksheet>"
    )


def _cell_xml(ref: str, value: Any) -> str:
    if value is None:
        value = ""
    if isinstance(value, bool):
        value = "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    if isinstance(value, float):
        return f'<c r="{ref}"><v>{value}</v></c>'
    escaped = html.escape(str(value), quote=False)
    return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'


def _col_name(idx: int) -> str:
    out = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""


def _rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _workbook_xml(sheet_name: str) -> str:
    name = html.escape(sheet_name[:31], quote=True)
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="{name}" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""


def _workbook_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
