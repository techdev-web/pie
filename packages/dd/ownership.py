"""Temporal ownership engine — events → ordered chain; detect gaps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TimelineEntry:
    event_id: str
    event_type: str
    event_date: str | None
    event_date_raw: str | None
    registration_number: str | None
    parcel_id: str | None
    document_id: str | None
    parties: list[dict[str, Any]]
    shares: list[dict[str, Any]]
    verification_state: str
    chain_status: str = "OK"  # OK | GAP | TEMPORAL_CONFLICT | SHARE_OVERSELL
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "event_date": self.event_date,
            "event_date_raw": self.event_date_raw,
            "registration_number": self.registration_number,
            "parcel_id": self.parcel_id,
            "document_id": self.document_id,
            "parties": self.parties,
            "shares": self.shares,
            "verification_state": self.verification_state,
            "chain_status": self.chain_status,
            "notes": self.notes,
        }


@dataclass
class OwnershipChainResult:
    timeline: list[TimelineEntry]
    gaps: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeline": [e.to_dict() for e in self.timeline],
            "gaps": self.gaps,
            "conflicts": self.conflicts,
        }


def _party_rows(event: Any, person_names: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in event.parties or []:
        pid = str(p.person_id)
        rows.append(
            {
                "person_id": pid,
                "role": p.role,
                "display_name": person_names.get(pid),
            }
        )
    return rows


def _share_rows(event: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for s in event.shares or []:
        rows.append(
            {
                "person_id": str(s.person_id) if s.person_id else None,
                "share_numerator": s.share_numerator,
                "share_denominator": s.share_denominator,
                "share_text": s.share_text,
                "fraction": (
                    (s.share_numerator / s.share_denominator)
                    if s.share_numerator is not None
                    and s.share_denominator
                    and s.share_denominator != 0
                    else None
                ),
            }
        )
    return rows


def build_ownership_chain(
    events: list[Any],
    *,
    person_names: dict[str, str] | None = None,
) -> OwnershipChainResult:
    """Order ownership events chronologically and flag temporal / continuity gaps.

    Does not invent missing deeds — gaps are explicit.
    """
    names = person_names or {}
    dated = [e for e in events if e.event_date is not None]
    undated = [e for e in events if e.event_date is None]
    dated_sorted = sorted(
        dated,
        key=lambda e: e.event_date or datetime.min.replace(tzinfo=timezone.utc),
    )

    timeline: list[TimelineEntry] = []
    gaps: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for e in undated:
        timeline.append(
            TimelineEntry(
                event_id=str(e.id),
                event_type=e.event_type,
                event_date=None,
                event_date_raw=e.event_date_raw,
                registration_number=e.registration_number,
                parcel_id=str(e.parcel_id) if e.parcel_id else None,
                document_id=str(e.document_id) if e.document_id else None,
                parties=_party_rows(e, names),
                shares=_share_rows(e),
                verification_state=e.verification_state,
                chain_status="GAP",
                notes=["Event has no normalized date — chronology incomplete."],
            )
        )
        gaps.append(
            {
                "gap_type": "OWNERSHIP_DATE_MISSING",
                "event_id": str(e.id),
                "summary": "Ownership event lacks a normalized date.",
            }
        )

    prev: Any | None = None
    for e in dated_sorted:
        entry = TimelineEntry(
            event_id=str(e.id),
            event_type=e.event_type,
            event_date=e.event_date.isoformat() if e.event_date else None,
            event_date_raw=e.event_date_raw,
            registration_number=e.registration_number,
            parcel_id=str(e.parcel_id) if e.parcel_id else None,
            document_id=str(e.document_id) if e.document_id else None,
            parties=_party_rows(e, names),
            shares=_share_rows(e),
            verification_state=e.verification_state,
        )

        if prev is not None:
            # Same document chronology is fine; cross-doc continuity checks
            same_parcel = True
            if prev.parcel_id and e.parcel_id and prev.parcel_id != e.parcel_id:
                same_parcel = False

            if same_parcel and prev.document_id != e.document_id:
                earlier_buyers = {
                    p.person_id
                    for p in (prev.parties or [])
                    if p.role in ("buyer", "donee", "owner")
                }
                later_sellers = {
                    p.person_id
                    for p in (e.parties or [])
                    if p.role in ("seller", "donor")
                }
                if earlier_buyers and later_sellers and earlier_buyers.isdisjoint(later_sellers):
                    entry.chain_status = "GAP"
                    entry.notes.append(
                        "Later transfer sellers do not match earlier transfer buyers."
                    )
                    gaps.append(
                        {
                            "gap_type": "OWNERSHIP_CHAIN_GAP",
                            "earlier_event_id": str(prev.id),
                            "later_event_id": str(e.id),
                            "summary": "Ownership chain discontinuity between successive transfers.",
                        }
                    )

            # Chronology inversion: later-dated event that claims to precede earlier
            if prev.event_date and e.event_date and e.event_date < prev.event_date:
                entry.chain_status = "TEMPORAL_CONFLICT"
                entry.notes.append("Event date is earlier than previous timeline entry.")
                conflicts.append(
                    {
                        "conflict_type": "TEMPORAL_CONFLICT",
                        "earlier_event_id": str(prev.id),
                        "later_event_id": str(e.id),
                        "summary": "Ownership chronology is inverted.",
                    }
                )

        timeline.append(entry)
        prev = e

    # Stable display order: undated first (unknown), then chronological
    return OwnershipChainResult(timeline=timeline, gaps=gaps, conflicts=conflicts)
