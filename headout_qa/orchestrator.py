from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .grader import BOT_ESCALATE_MARKERS, ESCALATE_WORDS, CheckResult, Grade, Grader
from .scenarios import Scenario
from .sunshine import SunshineClient
from .transcript import Event, TranscriptStore
from .user_engine import UserEngine, build_user_engine
from .zendesk import ZendeskClient


@dataclass
class ScenarioRun:
    scenario_id: str
    node: str
    variant: str
    status: str
    conversation_id: str | None = None
    user_id: str | None = None
    booking_id: str | None = None
    l1: str | None = None
    l2: str | None = None
    l3: str | None = None
    mood: str | None = None
    ticket_id: int | None = None
    ticket_url: str | None = None
    grade: Grade | None = None
    llm_grade: Grade | None = None
    error: str | None = None
    duration_seconds: float = 0.0
    started_at: str = ""
    ended_at: str = ""
    escalated: bool = False


def _is_escalation(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ESCALATE_WORDS)


def _is_escalated(events: list[Event]) -> bool:
    recent = [e for e in events[-4:] if e.role in ("user", "bot")]
    for event in reversed(recent):
        lowered = event.text.lower()
        if event.role == "user" and _is_escalation(event.text):
            return True
        if event.role == "bot" and any(marker in lowered for marker in BOT_ESCALATE_MARKERS):
            return True
    return False


def _bot_signaled_handoff(events: list[Event]) -> bool:
    # Distinct from _is_escalated: this only looks at whether the *bot itself*
    # just performed a handoff, so the loop can stop right away instead of
    # waiting for a timeout to notice — a real handoff, "completed" naturally
    # when the scripted user simply runs out of turns, used to slip past
    # grading entirely because run.status stayed "completed" with no timeout.
    for event in reversed(events):
        if event.role != "bot":
            continue
        return any(marker in event.text.lower() for marker in BOT_ESCALATE_MARKERS)
    return False


@dataclass
class RunResult:
    run_id: str
    run_dir: Path
    scenarios: list[ScenarioRun]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    def __init__(self, settings: Settings, run_dir: Path | None = None) -> None:
        self.settings = settings
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(2)
        self.run_dir = run_dir or (settings.output_dir / self.run_id)
        self.store = TranscriptStore(self.run_dir)
        self.sunco = SunshineClient(settings)
        self.engine: UserEngine = build_user_engine(settings)
        self.grader = Grader(settings)
        self.zendesk = ZendeskClient(settings) if settings.zendesk_api_token else None

    def _external_id(self, scenario_id: str) -> str:
        return f"qa-{self.run_id}-{re.sub(r'[^a-zA-Z0-9_-]', '-', scenario_id)}"

    async def aclose(self) -> None:
        await self.sunco.aclose()
        await self.grader.aclose()
        if self.zendesk:
            await self.zendesk.aclose()
        if hasattr(self.engine, "aclose"):
            await self.engine.aclose()

    async def run(
        self, scenarios: list[Scenario], on_scenario_done=None
    ) -> RunResult:
        semaphore = asyncio.Semaphore(self.settings.concurrency)

        async def guarded(scenario: Scenario) -> ScenarioRun:
            async with semaphore:
                result = await self.run_one(scenario)
                if on_scenario_done is not None:
                    on_scenario_done(result)
                return result

        runs = await asyncio.gather(*(guarded(s) for s in scenarios))
        self.store.write_run_meta(
            {
                "run_id": self.run_id,
                "started_at": _now(),
                "concurrency": self.settings.concurrency,
                "total": len(runs),
                "passed": sum(1 for r in runs if r.grade and r.grade.passed),
                "failed": sum(1 for r in runs if r.grade and not r.grade.passed),
                "escalated": sum(1 for r in runs if r.escalated),
                "incomplete": sum(1 for r in runs if r.status in ("timeout", "error")),
            }
        )
        return RunResult(run_id=self.run_id, run_dir=self.run_dir, scenarios=list(runs))

    async def run_one(self, scenario: Scenario) -> ScenarioRun:
        started = time.monotonic()
        run = ScenarioRun(
            scenario_id=scenario.scenario_id,
            node=scenario.node,
            variant=scenario.variant,
            status="completed",
            booking_id=scenario.booking.booking_id,
            l1=scenario.booking.l1,
            l2=scenario.booking.l2,
            l3=scenario.booking.l3,
            mood=scenario.booking.mood,
            started_at=_now(),
        )
        events: list[Event] = []
        try:
            user_id = await self.sunco.create_user(
                given_name="Skyler QA Tester",
                external_id=self._external_id(scenario.scenario_id),
                metadata={"qa_scenario": scenario.scenario_id, "booking_id": scenario.booking.booking_id},
            )
            run.user_id = user_id
            metadata = {
                f"zen:ticket_field:{self.settings.booking_field_id}": scenario.booking.booking_id,
                f"zen:ticket_field:{self.settings.email_field_id}": scenario.email,
            }
            conversation_id = await self.sunco.create_conversation(user_id, metadata)
            run.conversation_id = conversation_id
            events.append(Event(role="system", text=f"conversation {conversation_id} created", ts=_now()))

            await self.sunco.pass_control(conversation_id)
            events.append(Event(role="system", text="control passed to AI agent", ts=_now()))

            known_bot_ids: set[str] = set()

            max_turns = scenario.max_turns or self.settings.max_turns
            turns = 0
            deadline_total = time.monotonic() + self.settings.conversation_timeout_seconds

            while turns < max_turns and time.monotonic() < deadline_total:
                next_text = await self.engine.next_message(scenario, events)
                if next_text is None:
                    break
                sent = await self.sunco.send_user_message(conversation_id, user_id, next_text)
                events.append(Event(role="user", text=next_text, ts=sent.received, message_id=sent.id))
                turns += 1

                replied, timeout = await self._wait_for_bot(conversation_id, known_bot_ids, events)
                if timeout:
                    if _is_escalated(events):
                        events.append(
                            Event(
                                role="system",
                                text="escalated to supervisor (handoff): no further AI reply expected",
                                ts=_now(),
                            )
                        )
                        run.status = "escalated"
                        run.escalated = True
                    else:
                        events.append(Event(role="system", text="no bot reply within timeout", ts=_now()))
                        run.status = "timeout"
                    break

                # A bot-initiated handoff ends the conversation right away instead
                # of waiting to time out — previously this case only stopped via
                # the (much slower) timeout branch above, and a scripted user
                # engine that simply ran out of turns right after a handoff would
                # leave run.status="completed" with no escalation recorded at all.
                if _bot_signaled_handoff(events):
                    events.append(
                        Event(role="system", text="escalated to supervisor (handoff)", ts=_now())
                    )
                    run.status = "escalated"
                    run.escalated = True
                    break

            # Grade any conversation that reached a real endpoint — including an
            # escalation/handoff, which is graded for whether it was *justified*
            # (see grader.py's escalation_justified check) rather than excluded
            # from pass/fail entirely. Only timeout/error (no real endpoint) stay
            # ungraded/"incomplete".
            if run.status in ("completed", "escalated"):
                run.grade = self.grader.grade(scenario, events)
                run.llm_grade = await self.grader.grade_llm(scenario, events)
                if run.llm_grade is not None:
                    # Fold the LLM judge's verdict in unconditionally — it also
                    # checks grammar/tone, hallucination and real resolution, none
                    # of which the keyword grader can see. Previously this was
                    # only applied when the scenario defined pass_criteria, so on
                    # every scenario without pass_criteria the LLM grade was
                    # computed and then silently discarded.
                    detail = "; ".join(run.llm_grade.notes) if run.llm_grade.notes else (
                        "met" if run.llm_grade.passed else "not met"
                    )
                    check_name = "pass_criteria" if scenario.pass_criteria else "llm_judge"
                    run.grade.checks.append(CheckResult(check_name, run.llm_grade.passed, detail))
                    run.grade.passed = run.grade.passed and run.llm_grade.passed
                elif scenario.pass_criteria:
                    run.grade.notes.append(
                        "pass_criteria defined in the sheet but not evaluated — "
                        "set an LLM API key in Settings to grade it"
                    )
            run.ticket_id, run.ticket_url = await self._resolve_ticket(scenario)
        except Exception as exc:  # noqa: BLE001
            run.status = "error"
            run.error = f"{type(exc).__name__}: {exc}"
            events.append(Event(role="system", text=run.error, ts=_now()))

        run.ended_at = _now()
        run.duration_seconds = round(time.monotonic() - started, 2)
        payload = {
            "scenario_id": run.scenario_id,
            "node": run.node,
            "variant": run.variant,
            "status": run.status,
            "escalated": run.escalated,
            "conversation_id": run.conversation_id,
            "user_id": run.user_id,
            "booking_id": run.booking_id,
            "l1": run.l1,
            "l2": run.l2,
            "l3": run.l3,
            "mood": run.mood,
            "scenario_text": scenario.scenario_text,
            "pass_criteria": scenario.pass_criteria,
            "ticket_id": run.ticket_id,
            "ticket_url": run.ticket_url,
            "error": run.error,
            "duration_seconds": run.duration_seconds,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "grade": _grade_to_dict(run.grade),
            "llm_grade": _grade_to_dict(run.llm_grade),
            "transcript": [e.__dict__ for e in events],
        }
        self.store.write_scenario(payload)
        self.store.append_events(run.scenario_id, events)
        return run

    async def _wait_for_bot(
        self, conversation_id: str, known_bot_ids: set[str], events: list[Event]
    ) -> tuple[list[Event], bool]:
        if not self._escalation_pending(events):
            deadline = time.monotonic() + self.settings.message_timeout_seconds
        else:
            deadline = time.monotonic() + self.settings.escalation_grace_seconds
        while time.monotonic() < deadline:
            messages = await self.sunco.list_messages(conversation_id)
            new = [m for m in messages if m.is_bot and m.id not in known_bot_ids]
            if new:
                for message in new:
                    known_bot_ids.add(message.id)
                    text = message.text or f"[{message.content_type or 'unknown'}]"
                    events.append(Event(role="bot", text=text, ts=message.received, message_id=message.id, source=message.source_type))
                return events, False
            await asyncio.sleep(self.settings.poll_interval_seconds)
        return events, True

    @staticmethod
    def _escalation_pending(events: list[Event]) -> bool:
        return _is_escalated(events)

    async def _resolve_ticket(self, scenario: Scenario) -> tuple[int | None, str | None]:
        if self.zendesk is None:
            return None, None
        try:
            for attempt in range(3):
                ticket_id = await self.zendesk.search_ticket_by_field(
                    self.settings.booking_field_id, scenario.booking.booking_id
                )
                if ticket_id is not None:
                    url = f"https://{self.settings.zendesk_subdomain}.zendesk.com/agent/tickets/{ticket_id}"
                    return ticket_id, url
                await asyncio.sleep(5)
        except Exception:  # noqa: BLE001
            return None, None
        return None, None


def _grade_to_dict(grade: Grade | None) -> dict | None:
    if grade is None:
        return None
    return {
        "passed": grade.passed,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in grade.checks],
        "notes": grade.notes,
    }
