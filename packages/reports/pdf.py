"""Minimal PDF renderer for diligence reports (no third-party PDF deps)."""

from __future__ import annotations

from typing import Any


def _escape_pdf_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _wrap(text: str, width: int = 90) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) <= width:
            cur = f"{cur} {w}"
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _flatten_report(body: dict[str, Any], title: str) -> list[str]:
    lines: list[str] = [title, "=" * min(len(title), 72), ""]
    summary = body.get("summary") or {}
    lines.append(
        "Summary: "
        f"docs={summary.get('document_count', 0)} "
        f"open_conflicts={summary.get('open_conflicts', 0)} "
        f"missing={summary.get('missing_evidence', 0)} "
        f"risk={summary.get('risk_level', 'UNKNOWN')}"
    )
    lines.append(f"Truth fingerprint: {body.get('truth_fingerprint', '')}")
    lines.append("")
    for rule in body.get("language_rules") or []:
        lines.append(f"- {rule}")
    lines.append("")

    sections = body.get("sections") or {}
    order = body.get("section_order") or list(sections.keys())
    for key in order:
        sec = sections.get(key) or {}
        heading = sec.get("heading") or key
        lines.append(heading)
        lines.append("-" * min(len(heading), 72))
        if "conclusion" in sec:
            lines.append(str(sec["conclusion"]))
        if key == "scope":
            for d in sec.get("documents") or []:
                lines.append(
                    f"  • {d.get('filename') or d.get('document_id')} "
                    f"[{d.get('doc_type') or 'unclassified'}] pages={d.get('page_count')}"
                )
        elif key == "conflicts":
            lines.append(f"Open: {sec.get('open_count', 0)}  Resolved: {sec.get('resolved_count', 0)}")
            for c in sec.get("open_conflicts") or []:
                lines.append(
                    f"  • [{c.get('status')}] {c.get('conflict_type')}: {c.get('summary')}"
                )
        elif key == "missing_evidence":
            for g in sec.get("items") or []:
                lines.append(
                    f"  • [{g.get('status')}] {g.get('referenced_label')}: {g.get('summary')}"
                )
        elif key == "risk_drivers":
            lines.append(f"Level: {sec.get('risk_level')}  Score: {sec.get('score')}")
            for d in sec.get("drivers") or []:
                if isinstance(d, dict):
                    lines.append(
                        f"  • {d.get('label') or d.get('code')} (+{d.get('weight', 0)})"
                    )
            if sec.get("disclaimer"):
                lines.append(str(sec["disclaimer"]))
        elif key == "recommended_verifications":
            for item in sec.get("items") or []:
                lines.append(f"  • [{item.get('priority')}] {item.get('action')}")
        elif key == "parties_ownership":
            for e in (sec.get("ownership_timeline") or [])[:20]:
                if isinstance(e, dict):
                    lines.append(
                        f"  • {e.get('event_date') or e.get('date') or '?'} "
                        f"{e.get('event_type') or e.get('type') or ''}: "
                        f"{e.get('summary') or e.get('label') or e}"
                    )
            for c in sec.get("open_ownership_conflicts") or []:
                lines.append(f"  ! [{c.get('status')}] {c.get('summary')}")
        elif key == "encumbrances":
            for f in sec.get("encumbrance_facts") or []:
                lines.append(
                    f"  • [{f.get('verification_state')}] {f.get('value')}"
                )
            lines.append(str(sec.get("conclusion") or ""))
        elif key == "parcel_identity":
            for p in sec.get("parcels") or []:
                ids = ", ".join(
                    f"{i.get('id_type')}={i.get('id_value')}"
                    for i in (p.get("identifiers") or [])
                )
                lines.append(f"  • {p.get('display_label')} {ids}")
            for c in sec.get("open_identity_conflicts") or []:
                lines.append(f"  ! [{c.get('status')}] {c.get('summary')}")
        lines.append("")
    return lines


def render_report_pdf(body: dict[str, Any], *, title: str) -> bytes:
    """Render a simple multi-page PDF (Helvetica) from the report body."""
    text_lines = _flatten_report(body, title)
    # Paginate ~48 lines per page
    per_page = 48
    pages: list[list[str]] = [
        text_lines[i : i + per_page] for i in range(0, max(len(text_lines), 1), per_page)
    ]
    if not pages:
        pages = [[title]]

    objects: list[bytes] = []

    def add_obj(content: bytes) -> int:
        objects.append(content)
        return len(objects)

    # 1: Catalog
    add_obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    # 2: Pages placeholder — filled later
    pages_obj_index = add_obj(b"")  # placeholder

    page_obj_ids: list[int] = []
    content_obj_ids: list[int] = []

    for page_lines in pages:
        # content stream
        y_start = 780
        leading = 14
        cmds = ["BT", "/F1 10 Tf", "14 TL", f"50 {y_start} Td"]
        first = True
        for line in page_lines:
            esc = _escape_pdf_text(line[:120])
            if first:
                cmds.append(f"({esc}) Tj")
                first = False
            else:
                cmds.append("T*")
                cmds.append(f"({esc}) Tj")
        cmds.append("ET")
        stream = "\n".join(cmds).encode("latin-1", errors="replace")
        content_id = add_obj(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        content_obj_ids.append(content_id)
        page_id = add_obj(b"")  # placeholder
        page_obj_ids.append(page_id)

    # Font object
    font_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    # Fill page objects
    for page_id, content_id in zip(page_obj_ids, content_obj_ids):
        objects[page_id - 1] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ).encode()

    # Fill pages tree
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects[pages_obj_index - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_ids)} >>"
    ).encode()

    # Assemble PDF
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode())
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)
