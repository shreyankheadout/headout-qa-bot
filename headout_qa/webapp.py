from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bookings import fetch_bookings
from .config import Settings
from .orchestrator import RunResult, ScenarioRun
from .report import build_report
from .scenarios import build_scenarios, fetch_scenarios_csv


@dataclass
class RunState:
    task: asyncio.Task | None = None
    running: bool = False
    run_id: str | None = None
    run_dir: str | None = None
    total: int = 0
    done: int = 0
    passed: int = 0
    failed: int = 0
    escalated: int = 0
    incomplete: int = 0
    error: str | None = None
    report_path: str | None = None
    llm_key_set: bool = False
    last_callback: list[ScenarioRun] = field(default_factory=list)
    cleared: bool = False


class StartPayload(BaseModel):
    llm_api_key: str | None = None
    llm_model: str | None = None
    limit: int | None = None
    l1: str | None = None
    l2: str | None = None
    l3: str | None = None


class LlmSettingsPayload(BaseModel):
    api_key: str | None = None
    model: str | None = None


class ZendeskSettingsPayload(BaseModel):
    subdomain: str | None = None
    user_email: str | None = None
    api_token: str | None = None
    booking_field_id: str | None = None
    email_field_id: str | None = None


class SettingsPayload(BaseModel):
    sheet_id: str | None = None
    sunco_base_url: str | None = None
    sunco_app_id: str | None = None
    sunco_key_id: str | None = None
    sunco_key_secret: str | None = None
    ultimate_switchboard_id: str | None = None


_SETTINGS_MAP = {
    "sheet_id": "SHEET_ID",
    "sunco_base_url": "SUNCO_BASE_URL",
    "sunco_app_id": "SUNCO_APP_ID",
    "sunco_key_id": "SUNCO_KEY_ID",
    "sunco_key_secret": "SUNCO_KEY_SECRET",
    "ultimate_switchboard_id": "ULTIMATE_SWITCHBOARD_ID",
}


STATE = RunState()


@dataclass
class _Job:
    settings: Settings
    on_done: object


def _apply_overrides(payload: StartPayload | None, settings: Settings) -> None:
    if payload is None:
        return
    if payload.llm_api_key:
        settings.llm_api_key = payload.llm_api_key
        settings.llm_provider = "compatible"
        STATE.llm_key_set = True
    if payload.llm_model:
        settings.llm_model = payload.llm_model


def _update_env(updates: dict[str, str]) -> None:
    path = Path(".env")
    lines = path.read_text().splitlines() if path.exists() else []
    out: list[str] = []
    remaining = dict(updates)
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")


def _filter_bookings(bookings, l1: str | None, l2: str | None, l3: str | None):  # type: ignore[no-untyped-def]
    if l1:
        bookings = [b for b in bookings if (b.l1 or "") == l1]
    if l2:
        bookings = [b for b in bookings if (b.l2 or "") == l2]
    if l3:
        bookings = [b for b in bookings if (b.l3 or "") == l3]
    return bookings


async def _job(settings: Settings, limit: int | None = None, l1: str | None = None, l2: str | None = None, l3: str | None = None) -> None:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        bookings = await fetch_bookings(settings, client)
        if not bookings:
            STATE.error = "no bookings found in the sheet"
            return
        scenario_rows = None
        if settings.sheet_scenarios_export_url:
            scenario_rows = await fetch_scenarios_csv(settings.sheet_scenarios_export_url, client)
    bookings = _filter_bookings(bookings, l1, l2, l3)
    if not bookings:
        STATE.error = f"no bookings match filter L1={l1 or 'any'} L2={l2 or 'any'} L3={l3 or 'any'}"
        return
    scenarios = build_scenarios(bookings, scenario_rows)
    if limit is not None and limit > 0:
        scenarios = scenarios[:limit]
    STATE.total = len(scenarios)

    from .orchestrator import Orchestrator

    orchestrator = Orchestrator(settings)
    STATE.run_id = orchestrator.run_id
    STATE.run_dir = str(orchestrator.run_dir)
    try:
        result: RunResult = await orchestrator.run(scenarios, on_scenario_done=_on_scenario_done)
        report_path = build_report(result, result.run_dir)
        STATE.report_path = str(report_path)
    except asyncio.CancelledError:
        STATE.error = "stopped by user"
        raise
    except Exception as exc:  # noqa: BLE001
        STATE.error = f"{type(exc).__name__}: {exc}"
    finally:
        await orchestrator.aclose()
        STATE.running = False
        STATE.task = None


def _on_scenario_done(run: ScenarioRun) -> None:
    STATE.done += 1
    STATE.last_callback.append(run)
    # `escalated` is a tag, not a bucket: an escalated conversation is now graded
    # (see orchestrator.py's escalation_justified check) so it belongs in passed
    # or failed like any other scenario. Checking grade first, and tracking
    # `escalated` separately below, keeps a bad unprompted handoff from silently
    # disappearing into a neutral "escalated" count that used to sit outside the
    # pass/fail totals entirely.
    if run.grade and run.grade.passed:
        STATE.passed += 1
    elif run.grade and not run.grade.passed:
        STATE.failed += 1
    else:
        STATE.incomplete += 1
    if run.escalated:
        STATE.escalated += 1


async def _fetch_data_summary(settings: Settings) -> dict:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        bookings = await fetch_bookings(settings, client)
        scenario_rows = None
        if settings.sheet_scenarios_export_url:
            scenario_rows = await fetch_scenarios_csv(settings.sheet_scenarios_export_url, client)
    scenarios = build_scenarios(bookings, scenario_rows)
    def _dist(col):
        c = Counter(getattr(b, col) or "(blank)" for b in bookings)
        return [{"value": k, "count": v} for k, v in sorted(c.items())]
    return {
        "bookings": len(bookings),
        "scenarios": len(scenarios),
        "nodes": dict(Counter(s.node for s in scenarios)),
        "statuses": dict(Counter(b.booking_status for b in bookings if b.booking_status)),
        "cancellable": sum(1 for b in bookings if b.is_cancellable is True),
        "not_cancellable": sum(1 for b in bookings if b.is_cancellable is False),
        "reschedulable": sum(1 for b in bookings if b.is_reschedulable is True),
        "extended_validity": sum(1 for b in bookings if b.has_extended_validity is True),
        "filters": {
            "l1": _dist("l1"),
            "l2": _dist("l2"),
            "l3": _dist("l3"),
            "mood": _dist("mood"),
        },
        "bookings_preview": [
            {"l1": b.l1 or "", "l2": b.l2 or "", "l3": b.l3 or "", "mood": b.mood or ""} for b in bookings
        ],
    }


_ERROR_PATTERNS: list[tuple[str, str]] = [
    ("SuncoError", "Couldn't reach the AI agent conversation service."),
    ("ZendeskError", "Couldn't look up the Zendesk ticket for this booking."),
    ("ReadTimeout", "The AI agent didn't respond in time."),
    ("ConnectTimeout", "Couldn't connect to the AI agent service in time."),
    ("TimeoutException", "The AI agent didn't respond in time."),
    ("ConnectError", "Couldn't connect to the AI agent service."),
    ("JSONDecodeError", "The AI agent sent back a response we couldn't read."),
]


def _humanize_error(raw: str | None) -> str | None:
    """Translate an internal exception repr into a message a non-engineer can act on."""
    if not raw:
        return None
    for marker, message in _ERROR_PATTERNS:
        if marker in raw:
            return message
    return "This scenario didn't finish running — see details for what went wrong."


def _scenario_status(r: ScenarioRun) -> dict:
    reasons = []
    passed_facts: list[str] = []
    checks: list[dict] = []
    if r.grade:
        checks = [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in r.grade.checks]
        for c in r.grade.checks:
            if not c.passed:
                reasons.append(f"{c.name}: {c.detail}")
            elif c.passed and c.name.startswith("fact_") and ("sheet says" in c.detail or "bot stated" in c.detail):
                label = c.name.replace("fact_", "")
                passed_facts.append(label)
    error_human = _humanize_error(r.error)
    if error_human:
        reasons.append(error_human)
    if r.escalated:
        reasons.append("escalated to supervisor (handoff)")
    return {
        "scenario_id": r.scenario_id,
        "booking_id": r.booking_id,
        "node": r.node,
        "l1": r.l1,
        "l2": r.l2,
        "l3": r.l3,
        "mood": r.mood,
        "status": r.status,
        "passed": r.grade.passed if r.grade else None,
        "escalated": r.escalated,
        "ticket_id": r.ticket_id,
        "ticket_url": r.ticket_url,
        "checks": checks,
        "reasons": reasons,
        "passed_facts": passed_facts,
        "error": error_human,
        "error_detail": r.error,
    }


def _persisted_scenarios() -> list[dict]:
    latest = _latest_run_dir()
    if latest is None:
        return []
    from .report import _load_scenarios

    try:
        return [_scenario_status(r) for r in _load_scenarios(latest)]
    except Exception:  # noqa: BLE001
        return []


def _latest_run_dir() -> Path | None:
    output = Settings().output_dir
    candidates = [p for p in output.glob("*") if any((p / "scenarios").glob("*.json"))]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_status() -> dict:
    settings = Settings()
    source = settings.booking_source_label
    scenarios = [_scenario_status(r) for r in STATE.last_callback]
    if not scenarios and not STATE.cleared and not STATE.running:
        scenarios = _persisted_scenarios()
    return {
        "running": STATE.running,
        "run_id": STATE.run_id,
        "total": STATE.total,
        "done": STATE.done,
        "passed": STATE.passed,
        "failed": STATE.failed,
        "escalated": STATE.escalated,
        "incomplete": STATE.incomplete,
        "error": STATE.error,
        "report_ready": bool(STATE.report_path),
        "scenarios": scenarios,
        "sheet": {
            "id": settings.sheet_id,
            "url": settings.sheet_edit_url,
            "source": source,
            "bookings_tab": settings.sheet_bookings_tab,
            "scenarios_tab": settings.sheet_scenarios_tab,
        },
        "llm": {
            "key_set": bool(settings.llm_api_key) or STATE.llm_key_set,
            "provider": settings.llm_provider,
            "model": settings.llm_model,
        },
        "zendesk": {
            "subdomain": settings.zendesk_subdomain,
            "user_email": settings.zendesk_user_email,
            "api_token_set": bool(settings.zendesk_api_token),
            "booking_field_id": settings.booking_field_id,
            "email_field_id": settings.email_field_id,
            "agent_url": f"https://{settings.zendesk_subdomain}.zendesk.com/agent",
            "basic_auth_configured": bool(settings.zendesk_api_token) and bool(settings.zendesk_user_email),
        },
        "sunshine": {
            "base_url": settings.sunco_base_url,
            "app_id": settings.sunco_app_id,
            "key_id": settings.sunco_key_id,
            "key_secret_set": bool(settings.sunco_key_secret),
            "switchboard_id": settings.ultimate_switchboard_id,
        },
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if STATE.task is not None:
        STATE.task.cancel()
        try:
            await STATE.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Headout AI Agent QA", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html")


@app.get("/api/status")
async def status() -> dict:
    return build_status()


@app.post("/api/start")
async def start(payload: StartPayload | None = None) -> dict:
    if STATE.running:
        return {"ok": False, "error": "a run is already in progress"}
    settings = Settings()
    _apply_overrides(payload, settings)
    STATE.running = True
    STATE.total = 0
    STATE.done = 0
    STATE.passed = 0
    STATE.failed = 0
    STATE.escalated = 0
    STATE.incomplete = 0
    STATE.error = None
    STATE.report_path = None
    STATE.last_callback = []
    STATE.cleared = False
    limit = payload.limit if payload else None
    l1 = (payload.l1.strip() if payload and payload.l1 and payload.l1.strip() else None)
    l2 = (payload.l2.strip() if payload and payload.l2 and payload.l2.strip() else None)
    l3 = (payload.l3.strip() if payload and payload.l3 and payload.l3.strip() else None)
    # treat "(blank)" sentinel as empty
    if l1 == "(blank)":
        l1 = ""
    if l2 == "(blank)":
        l2 = ""
    if l3 == "(blank)":
        l3 = ""
    STATE.task = asyncio.create_task(_job(settings, limit, l1, l2, l3))
    return {"ok": True}


@app.post("/api/stop")
async def stop() -> dict:
    if STATE.task is not None and not STATE.task.done():
        STATE.task.cancel()
        try:
            await STATE.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    return {"ok": True, "running": STATE.running}


@app.post("/api/clear")
async def clear() -> dict:
    if STATE.running:
        return {"ok": False, "error": "cannot clear while a simulation is running — stop it first"}
    STATE.total = 0
    STATE.done = 0
    STATE.passed = 0
    STATE.failed = 0
    STATE.escalated = 0
    STATE.incomplete = 0
    STATE.error = None
    STATE.report_path = None
    STATE.run_id = None
    STATE.run_dir = None
    STATE.last_callback = []
    STATE.cleared = True
    return {"ok": True}


@app.post("/api/refresh")
async def refresh_data() -> dict:
    try:
        summary = await _fetch_data_summary(Settings())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, **summary}


@app.post("/api/settings/llm")
async def save_llm(payload: LlmSettingsPayload) -> dict:
    updates: dict[str, str] = {}
    if payload.api_key and payload.api_key.strip():
        updates["LLM_API_KEY"] = payload.api_key.strip()
    if payload.model and payload.model.strip():
        updates["LLM_MODEL"] = payload.model.strip()
    if updates:
        try:
            _update_env(updates)
        except OSError as exc:
            return {"ok": False, "error": f"could not write .env: {exc}"}
    settings = Settings()
    if not settings.llm_api_key:
        return {
            "ok": True, "verified": False, "model": settings.llm_model,
            "error": "saved, but no API key is set — the LLM judge stays off and the checklist grader runs alone",
        }
    # A live round-trip here catches exactly the kind of mistake that otherwise
    # only surfaces mid-run, silently, as every LLM-graded scenario failing —
    # e.g. a model id typo/case mismatch the provider rejects with a 404.
    try:
        async with httpx.AsyncClient(
            base_url=settings.llm_api_base,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(20.0),
        ) as client:
            resp = await client.post(
                "/chat/completions",
                json={
                    "model": settings.llm_model,
                    "messages": [{"role": "user", "content": "Reply with only the word: ok"}],
                    "max_tokens": 5,
                },
            )
            resp.raise_for_status()
        return {"ok": True, "verified": True, "model": settings.llm_model}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": True, "verified": False, "model": settings.llm_model,
            "error": f"{type(exc).__name__}: {exc}",
        }


@app.post("/api/settings/zendesk")
async def save_zendesk(payload: ZendeskSettingsPayload) -> dict:
    updates: dict[str, str] = {}
    if payload.subdomain and payload.subdomain.strip():
        updates["ZENDESK_SUBDOMAIN"] = payload.subdomain.strip().lower()
    if payload.user_email and payload.user_email.strip():
        updates["ZENDESK_USER_EMAIL"] = payload.user_email.strip()
    if payload.api_token and payload.api_token.strip():
        updates["ZENDESK_API_TOKEN"] = payload.api_token.strip()
    if payload.booking_field_id and payload.booking_field_id.strip():
        updates["BOOKING_FIELD_ID"] = payload.booking_field_id.strip()
    if payload.email_field_id and payload.email_field_id.strip():
        updates["EMAIL_FIELD_ID"] = payload.email_field_id.strip()
    if updates:
        try:
            _update_env(updates)
        except OSError as exc:
            return {"ok": False, "error": f"could not write .env: {exc}"}
    settings = Settings()
    if not settings.zendesk_api_token or not settings.zendesk_user_email:
        return {
            "ok": True, "verified": False, "subdomain": settings.zendesk_subdomain,
            "error": "saved, but email / API token are missing — ticket lookup is off",
        }
    from .zendesk import ZendeskClient

    client = ZendeskClient(settings)
    try:
        user = await client.check_auth()
        return {
            "ok": True, "verified": True,
            "user": user.get("name"), "email": user.get("email"),
            "subdomain": settings.zendesk_subdomain,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": True, "verified": False, "subdomain": settings.zendesk_subdomain,
            "error": f"{type(exc).__name__}: {exc}",
        }
    finally:
        await client.aclose()


@app.post("/api/settings")
async def save_settings(payload: SettingsPayload) -> dict:
    updates: dict[str, str] = {}
    for attr, env_key in _SETTINGS_MAP.items():
        value = getattr(payload, attr)
        if value and value.strip():
            updates[env_key] = value.strip()
    if not updates:
        return {"ok": True, "saved": []}
    try:
        _update_env(updates)
    except OSError as exc:
        return {"ok": False, "error": f"could not write .env: {exc}"}
    settings = Settings()
    return {
        "ok": True,
        "saved": list(updates),
        "base_url": settings.sunco_base_url,
        "app_id": settings.sunco_app_id,
        "key_id": settings.sunco_key_id,
        "switchboard_id": settings.ultimate_switchboard_id,
        "sheet_id": settings.sheet_id,
    }


_SAFE_SCENARIO_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


@app.get("/api/transcript/{scenario_id}")
async def get_transcript(scenario_id: str) -> dict:
    if not _SAFE_SCENARIO_ID.match(scenario_id):
        raise HTTPException(status_code=400, detail="invalid scenario id")
    run_dir = Path(STATE.run_dir) if STATE.run_dir else _latest_run_dir()
    if run_dir is None:
        raise HTTPException(status_code=404, detail="no run available yet")
    path = run_dir / "scenarios" / f"{scenario_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="no transcript for this scenario")
    data = json.loads(path.read_text())
    return {
        "transcript": data.get("transcript", []),
        "scenario_text": data.get("scenario_text"),
        "pass_criteria": data.get("pass_criteria", []),
    }


@app.get("/api/report")
async def report() -> FileResponse:
    if not STATE.report_path or not Path(STATE.report_path).exists():
        raise HTTPException(status_code=404, detail="no report available yet")
    return FileResponse(STATE.report_path, media_type="text/html")


def serve() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run(app, host=settings.server_host, port=settings.server_port)


