from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .bookings import Booking


@dataclass
class Scenario:
    scenario_id: str
    booking: Booking
    node: str = "default"
    variant: str = "default"
    scenario_text: str = ""
    pass_criteria: list[str] = field(default_factory=list)
    max_turns: int = 0

    @property
    def email(self) -> str:
        return self.booking.email_id


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "scenario"


def build_default_scenarios(bookings: list[Booking]) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for booking in bookings:
        # Derive the node from the *final* scenario text (after the fallback-text
        # generator has run), not the raw sheet column — otherwise a blank sheet
        # cell falls back to cancellation-flavored text but keeps node="general",
        # so the required-fact check never fires for that scenario.
        scenario_text = booking.scenario_text or _default_scenario_text(booking)
        scenarios.append(
            Scenario(
                scenario_id=f"booking-{booking.booking_id}",
                booking=booking,
                node=_derive_node(scenario_text),
                variant="default",
                scenario_text=scenario_text,
            )
        )
    return scenarios


# Deliberately narrower than a bare "ticket" substring match -- scenario text
# routinely mentions a ticket in passing ("already has a valid ticket for this
# booking but also needs an invoice") without the scenario actually being about
# ticket delivery. Matching on "ticket" alone misrouted invoice/payment-receipt
# scenarios into the "ticket" node, which forces the grader to require
# ticket-delivery language the bot was never actually asked to give.
_TICKET_DELIVERY_MARKERS = (
    "when will i receive", "when will i get", "haven't received", "have not received",
    "hasn't received", "has not received", "receive my ticket", "receive the ticket",
    "get my ticket", "resend", "re-send", "ticket delivery", "didn't get the ticket",
    "did not get the ticket", "not received the ticket",
)


def _derive_node(scenario_text: str) -> str:
    text = scenario_text.lower()
    if "cancel" in text:
        return "cancel"
    if "extend" in text:
        return "extend"
    if "postpon" in text or "reschedul" in text:
        return "reschedule"
    if "modify" in text or "change" in text:
        return "modify"
    if any(marker in text for marker in _TICKET_DELIVERY_MARKERS):
        return "ticket"
    return "general"


def _default_scenario_text(booking: Booking) -> str:
    parts = []
    if booking.booking_title:
        parts.append(f"The guest is asking about their booking for '{booking.booking_title}'.")
    if booking.is_cancellable:
        parts.append("The guest wants to cancel. Cancellation is possible; the guest is polite.")
    else:
        parts.append(
            "The guest wants to cancel. Cancellation is not possible; the guest gets frustrated, "
            "pushes back twice, then asks to speak to a supervisor."
        )
    if booking.is_reschedulable:
        parts.append("The guest is also considering rescheduling as an alternative.")
    return " ".join(parts)


async def fetch_scenarios_csv(url: str, client: httpx.AsyncClient) -> list[dict[str, str]]:
    resp = await client.get(url)
    resp.raise_for_status()
    return [row for row in csv.DictReader(io.StringIO(resp.text))]


def build_scenarios(
    bookings: list[Booking], scenario_rows: list[dict[str, str]] | None = None
) -> list[Scenario]:
    if not scenario_rows:
        return build_default_scenarios(bookings)

    by_id = {b.booking_id: b for b in bookings}
    scenarios: list[Scenario] = []
    for idx, row in enumerate(scenario_rows, start=1):
        booking_id = row.get("booking_id", "").strip()
        booking = by_id.get(booking_id)
        if booking is None:
            continue
        criteria_raw = row.get("pass_criteria") or ""
        criteria = [c.strip() for c in criteria_raw.split("|") if c.strip()]
        scenarios.append(
            Scenario(
                scenario_id=row.get("scenario_id") or f"scenario-{idx}",
                booking=booking,
                node=row.get("node") or "default",
                variant=row.get("variant") or "default",
                scenario_text=row.get("scenario_text") or "",
                pass_criteria=criteria,
                max_turns=int(row["max_turns"]) if row.get("max_turns", "").strip().isdigit() else 0,
            )
        )
    return scenarios
