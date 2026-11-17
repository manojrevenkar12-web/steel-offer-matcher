"""Turn an inbound email and its attachments into one text the model can quote from.

Every segment keeps its source (body, a PDF page, a spreadsheet row) so a reviewer can see
where a value came from. Images are not read here: they go to the vision route and come back
as text that is marked as a transcription.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    filename: str
    content_type: str
    data: bytes


class Inbound(BaseModel):
    message_id: str
    sender: str
    subject: str
    body: str
    received: str
    attachments: list[Attachment] = Field(default_factory=list)


@dataclass
class Segment:
    source: str
    text: str


@dataclass
class DocumentText:
    segments: list[Segment] = field(default_factory=list)
    images: list[Attachment] = field(default_factory=list)

    def render(self) -> str:
        return "\n".join(f"[{s.source}]\n{s.text}" for s in self.segments)

    def contains(self, quote: str) -> bool:
        q = squash(quote)
        return bool(q) and q in squash(self.render())

    def source_of(self, quote: str) -> str | None:
        q = squash(quote)
        for s in self.segments:
            if q and q in squash(s.text):
                return s.source
        return None


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace(" ", " ")).strip().lower()


def read(msg: Inbound) -> DocumentText:
    doc = DocumentText()
    doc.segments.append(Segment("email", f"From: {msg.sender}\nSubject: {msg.subject}\n\n{msg.body}"))
    for att in msg.attachments:
        name = att.filename.lower()
        if name.endswith(".xlsx"):
            doc.segments.extend(_xlsx(att))
        elif name.endswith(".csv"):
            doc.segments.append(Segment(att.filename, _csv_text(att.data)))
        elif name.endswith(".pdf"):
            doc.segments.extend(_pdf(att))
        elif name.endswith((".png", ".jpg", ".jpeg")):
            doc.images.append(att)
        elif name.endswith(".txt"):
            doc.segments.append(Segment(att.filename, att.data.decode("utf-8", "replace")))
    return doc


def _xlsx(att: Attachment) -> list[Segment]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(att.data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        for row in ws.iter_rows(values_only=True):
            if row is None or all(v is None or str(v).strip() == "" for v in row):
                continue
            rows.append(" | ".join("" if v is None else _cell(v) for v in row))
        if rows:
            out.append(Segment(f"{att.filename}:{ws.title}", "\n".join(rows)))
    return out


def _cell(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _csv_text(data: bytes) -> str:
    text = data.decode("utf-8-sig", "replace")
    try:
        dialect = csv.Sniffer().sniff("\n".join(text.splitlines()[:8]), delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
    return "\n".join(" | ".join(r) for r in csv.reader(io.StringIO(text), dialect))


def _pdf(att: Attachment) -> list[Segment]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(att.data))
    return [Segment(f"{att.filename} p{i + 1}", page.extract_text() or "") for i, page in enumerate(reader.pages)]


def table_rows(data: bytes, filename: str) -> list[list[str]]:
    """Raw rows of a supplier stock list, for the stock-list importer."""
    name = filename.lower()
    if name.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [["" if v is None else _cell(v) for v in row] for row in ws.iter_rows(values_only=True)
                if row and any(v is not None and str(v).strip() for v in row)]
    text = data.decode("utf-8-sig", "replace")
    try:
        dialect = csv.Sniffer().sniff("\n".join(text.splitlines()[:8]), delimiters=";,\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if text.count(";") >= text.count(",") else ","
    return [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
