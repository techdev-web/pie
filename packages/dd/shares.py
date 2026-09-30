"""Deterministic share accounting — never ask an LLM about percentages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ShareLedgerEntry:
    person_id: str
    display_name: str | None
    holding: float


@dataclass
class ShareAccountingResult:
    initial: list[ShareLedgerEntry] = field(default_factory=list)
    transfers: list[dict[str, Any]] = field(default_factory=list)
    final: list[ShareLedgerEntry] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def _rows(entries: list[ShareLedgerEntry]) -> list[dict[str, Any]]:
            return [
                {
                    "person_id": e.person_id,
                    "display_name": e.display_name,
                    "holding": round(e.holding, 6),
                }
                for e in entries
            ]

        return {
            "initial": _rows(self.initial),
            "transfers": self.transfers,
            "final": _rows(self.final),
            "conflicts": self.conflicts,
        }


def _fraction(share: Any) -> float | None:
    if share.share_numerator is None or not share.share_denominator:
        return None
    if share.share_denominator == 0:
        return None
    return share.share_numerator / share.share_denominator


def account_shares(
    events: list[Any],
    *,
    person_names: dict[str, str] | None = None,
) -> ShareAccountingResult:
    """Track ownership mathematically across transfer events.

    Detects within-event sum drift and oversell (seller transfers more than held).
    """
    names = person_names or {}
    holdings: dict[str, float] = {}
    result = ShareAccountingResult()

    dated = sorted(
        [e for e in events if e.event_date is not None],
        key=lambda e: e.event_date,
    )
    # If no dates, process in insertion order
    ordered = dated if dated else list(events)

    for idx, event in enumerate(ordered):
        shares = list(event.shares or [])
        parties = list(event.parties or [])

        # Per-event sum check
        total = 0.0
        counted = False
        for s in shares:
            frac = _fraction(s)
            if frac is not None:
                total += frac
                counted = True
        if counted and abs(total - 1.0) > 0.02:
            result.conflicts.append(
                {
                    "conflict_type": "SHARE_CONFLICT",
                    "event_id": str(event.id),
                    "share_sum": round(total, 6),
                    "summary": f"Ownership shares for event sum to {total:.4f}, not 1.0.",
                }
            )

        sellers = [p for p in parties if p.role in ("seller", "donor")]
        buyers = [p for p in parties if p.role in ("buyer", "donee", "owner")]

        # Seed ledger from first event parties if empty
        if not holdings and buyers:
            for b in buyers:
                pid = str(b.person_id)
                # Prefer explicit share for this person; else equal split of 1.0
                person_share = next(
                    (
                        _fraction(s)
                        for s in shares
                        if s.person_id and str(s.person_id) == pid and _fraction(s) is not None
                    ),
                    None,
                )
                holdings[pid] = person_share if person_share is not None else (
                    1.0 / len(buyers)
                )
            if idx == 0:
                result.initial = [
                    ShareLedgerEntry(pid, names.get(pid), amt)
                    for pid, amt in holdings.items()
                ]

        # Subsequent transfers: sellers must hold enough
        if idx > 0 and sellers and buyers:
            transfer_amount = 0.0
            for s in shares:
                frac = _fraction(s)
                if frac is not None and s.person_id and str(s.person_id) in {
                    str(b.person_id) for b in buyers
                }:
                    transfer_amount += frac
            if transfer_amount <= 0 and buyers:
                # Fallback: assume full conveyance if no buyer shares listed
                transfer_amount = 1.0 if len(sellers) == 1 and len(buyers) >= 1 else 0.0

            if transfer_amount > 0:
                seller_capacity = sum(holdings.get(str(s.person_id), 0.0) for s in sellers)
                if transfer_amount - seller_capacity > 0.02:
                    result.conflicts.append(
                        {
                            "conflict_type": "SHARE_OVERSELL_CONFLICT",
                            "event_id": str(event.id),
                            "attempted": round(transfer_amount, 6),
                            "available": round(seller_capacity, 6),
                            "summary": (
                                f"Seller(s) attempt to transfer {transfer_amount:.4f} "
                                f"but hold only {seller_capacity:.4f}."
                            ),
                        }
                    )
                else:
                    # Apply transfer proportionally from sellers
                    remaining = transfer_amount
                    for s in sellers:
                        pid = str(s.person_id)
                        have = holdings.get(pid, 0.0)
                        take = min(have, remaining)
                        holdings[pid] = have - take
                        remaining -= take
                    per_buyer = transfer_amount / len(buyers) if buyers else 0.0
                    for b in buyers:
                        pid = str(b.person_id)
                        holdings[pid] = holdings.get(pid, 0.0) + per_buyer

                result.transfers.append(
                    {
                        "event_id": str(event.id),
                        "amount": round(transfer_amount, 6),
                        "sellers": [str(s.person_id) for s in sellers],
                        "buyers": [str(b.person_id) for b in buyers],
                    }
                )

    result.final = [
        ShareLedgerEntry(pid, names.get(pid), amt)
        for pid, amt in sorted(holdings.items(), key=lambda x: -x[1])
        if amt > 1e-9
    ]
    if not result.initial and result.final:
        result.initial = list(result.final)
    return result
