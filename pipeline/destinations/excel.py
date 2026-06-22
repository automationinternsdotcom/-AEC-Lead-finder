"""Excel-compatible XLSX preview destination using only the standard library."""
from __future__ import annotations

import html
import zipfile
from pathlib import Path

from pipeline.destinations.base import DeliveryNotApproved, DeliveryPreview, DeliveryRecord
from pipeline.spec import DestinationV2


HEADERS = [
    "Title",
    "Company",
    "URL",
    "Priority",
    "Score",
    "Contact Name",
    "Contact Title",
    "Contact Email",
    "Contact Phone",
    "Notes",
]


class ExcelDestination:
    destination_type = "excel"

    def validate_config(self, config: DestinationV2) -> None:
        if config.type != "excel":
            raise ValueError("ExcelDestination requires destination type 'excel'")

    def preview(
        self,
        records: list[DeliveryRecord],
        *,
        output_dir: Path,
        run_id: str,
    ) -> DeliveryPreview:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{run_id}-lead-preview.xlsx"
        write_xlsx(path, records)
        return DeliveryPreview(
            destination_type=self.destination_type,
            record_count=len(records),
            output_path=str(path),
            records=records,
        )

    def deliver(self, records: list[DeliveryRecord], *, approved: bool) -> DeliveryPreview:
        raise DeliveryNotApproved("excel destination supports preview export, not live delivery")


def write_xlsx(path: Path, records: list[DeliveryRecord]) -> None:
    rows = [HEADERS] + [_record_row(record) for record in records]
    sheet = _worksheet_xml(rows)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _rels_xml())
        zf.writestr("xl/workbook.xml", _workbook_xml())
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def _record_row(record: DeliveryRecord) -> list[str | int | None]:
    return [
        record.title,
        record.company_name,
        record.url,
        record.priority,
        record.score,
        record.contact_name,
        record.contact_title,
        record.contact_email,
        record.contact_phone,
        record.notes,
    ]


def _worksheet_xml(rows: list[list[str | int | None]]) -> str:
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


def _cell_xml(ref: str, value: str | int | None) -> str:
    if value is None:
        value = ""
    if isinstance(value, int):
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


def _workbook_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Lead Preview" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""


def _workbook_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
