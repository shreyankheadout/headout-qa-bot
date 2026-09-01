from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


@dataclass
class Booking:
    booking_id: str
    email_id: str
    booking_title: str | None = None
    booking_date: str | None = None
    booking_status: str | None = None
    is_cancellable: bool | None = None
    is_reschedulable: bool | None = None
    has_extended_validity: bool | None = None
    extended_validity: str | None = None
    ticket_download_link: str | None = None
    scenario_text: str | None = None
    l1: str | None = None
    l2: str | None = None
    l3: str | None = None
    mood: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Booking":
        extended_raw = _clean(_pick(row, "extended_validity"))
        has_ext = _parse_bool(_pick(row, "has_extended_validity", "is_extended_valid"))
        if not extended_raw and has_ext is None and _pick(row, "ticket_validity_type", "ticketValidityType"):
            validity_type = _str(_pick(row, "ticket_validity_type", "ticketValidityType")).upper()
            if validity_type == "UNTIL_DATE":
                valid_until = _clean(_pick(row, "ticket_validity_until_date", "ticketValidityUntilDate"))
                extended_raw = f"Valid until {valid_until or 'the ticket expiry date'}"
                has_ext = True
            elif validity_type == "UNTIL_DAYS_FROM_PURCHASE":
                days = _clean(_pick(row, "ticket_validity_until_days_from_purchase", "ticketValidityUntilDaysFromPurchase"))
                extended_raw = f"Valid for {days or 'a number of'} days from purchase"
                has_ext = True
        return cls(
            booking_id=_str(_pick(row, "booking_id", "bookingId")),
            email_id=_str(_pick(row, "email_id", "emailId")),
            booking_title=_clean(_pick(row, "booking_title", "bookingTitle", "tour_group_name", "tourGroupName")),
            booking_date=_clean(_pick(row, "booking_date", "bookingDate", "inventory_date_time", "inventoryDateTime", "booking_start_date")),
            booking_status=_clean(_pick(row, "booking_status", "bookingStatus", "status")),
            is_cancellable=_parse_bool(_pick(row, "is_cancellable", "isCancellable", "is_cancelable")),
            is_reschedulable=_parse_bool(_pick(row, "is_reschedulable", "isReschedulable")),
            has_extended_validity=has_ext,
            extended_validity=_extended_text(extended_raw) if extended_raw else None,
            ticket_download_link=_clean(_pick(row, "ticket_download_link", "ticketDownloadLink")),
            scenario_text=_clean(_pick(row, "scenario_text", "scenarioText")),
            l1=_clean(_pick(row, "L1", "l1")),
            l2=_clean(_pick(row, "L2", "l2")),
            l3=_clean(_pick(row, "L3", "l3")),
            mood=_parse_mood(_pick(row, "mood", "Mood")),
            raw={k: _str(v) for k, v in row.items()},
        )


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean(value: Any) -> str | None:
    value = _str(value)
    return value if value else None


# Guest mood as authored in the sheet's `mood` column, driving how the simulated
# customer behaves (see user_engine.py) independently of which fact is being tested.
VALID_MOODS = ("happy", "okay", "frustrated", "angry")


def _parse_mood(value: Any) -> str | None:
    v = _str(value).lower()
    return v if v in VALID_MOODS else None


def _parse_bool(value: Any) -> bool | None:
    v = _str(value).upper()
    if v in ("TRUE", "YES", "1", "Y"):
        return True
    if v in ("FALSE", "NO", "0", "N", "NONE", "N/A"):
        return False
    return None


def _parse_extended(raw: str) -> bool | None:
    v = raw.upper()
    if v in ("TRUE", "FALSE", "NO", "N", "NONE", "N/A"):
        return _parse_bool(raw)
    return raw is not None


def _extended_text(raw: str) -> str | None:
    if raw.upper() in ("TRUE", "FALSE", "NO", "N", "NONE", "N/A"):
        return None
    return raw


async def fetch_bookings(settings: Settings, client: httpx.AsyncClient) -> list[Booking]:
    if settings.backup_api_url:
        return await _fetch_backup_api(settings, client)
    if settings.google_sheets_api_key:
        return await _fetch_sheets_api(settings, client)
    return await fetch_bookings_csv(settings.sheet_bookings_export_url, client)


async def fetch_bookings_csv(url: str, client: httpx.AsyncClient) -> list[Booking]:
    resp = await client.get(url, headers={"User-Agent": BROWSER_UA}, follow_redirects=True)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    return [
        Booking.from_row(row)
        for row in reader
        if _str(_pick(row, "booking_id", "bookingId"))
        and not (
            _pick(row, "is_cancellable", "isCancellable", "is_cancelable") in (None, "")
            and _pick(row, "is_reschedulable", "isReschedulable") in (None, "")
        )
    ]


async def _fetch_backup_api(settings: Settings, client: httpx.AsyncClient) -> list[Booking]:
    headers = {"User-Agent": BROWSER_UA}
    if settings.backup_api_token:
        headers["Authorization"] = f"Bearer {settings.backup_api_token}"
    resp = await client.get(settings.backup_api_url, headers=headers, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("bookings") or data.get("data") or data.get("rows") or []
    else:
        raise ValueError("backup API returned unexpected shape")
    return [Booking.from_row(row) for row in rows]


async def _fetch_sheets_api(settings: Settings, client: httpx.AsyncClient) -> list[Booking]:
    params = {"key": settings.google_sheets_api_key}
    resp = await client.get(settings.sheet_bookings_values_url, params=params)
    resp.raise_for_status()
    values = resp.json().get("values", [])
    if not values:
        return []
    headers = [str(h) for h in values[0]]
    return [
        Booking.from_row({headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))})
        for row in values[1:]
        if row
    ]