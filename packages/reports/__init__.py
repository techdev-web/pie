"""Phase 7: diligence report generation from the verified truth layer."""

from packages.reports.builder import (
    GENERATOR_VERSION,
    SECTION_KEYS,
    REVIEW_AFFECTED_SECTIONS,
    generate_case_report,
    get_latest_report,
    mark_reports_stale,
    truth_fingerprint_for_case,
)
from packages.reports.pdf import render_report_pdf

__all__ = [
    "GENERATOR_VERSION",
    "SECTION_KEYS",
    "REVIEW_AFFECTED_SECTIONS",
    "generate_case_report",
    "get_latest_report",
    "mark_reports_stale",
    "truth_fingerprint_for_case",
    "render_report_pdf",
]
