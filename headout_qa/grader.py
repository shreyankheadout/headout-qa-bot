from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings
from .scenarios import Scenario
from .transcript import Event

# Shared with orchestrator.py, which decides *when* a conversation ends on
# escalation; this module decides whether that escalation was *justified*.
ESCALATE_WORDS = ("supervisor", "manager", "human agent", "live agent", "someone else", "escalate to")
BOT_ESCALATE_MARKERS = ("escalat", "transferring", "to an agent", "to a human", "an expert")

POS_CANCEL = (
    "can be cancelled", "can be canceled", "you can cancel", "eligible for cancellation",
    "we can cancel", "will help you cancel", "happy to cancel", "cancellation is possible",
    "you may cancel", "yes", "able to cancel",
)
NEG_CANCEL = (
    "cannot be cancelled", "cannot be canceled", "can't be cancelled", "cancellations are not permitted",
    "not permitted", "not eligible", "not possible to cancel", "unable to cancel", "cannot cancel",
    "can't cancel", "not cancellable", "non-cancellable", "not be cancelled", "not cancelled",
)
POS_RESCHEDULE = (
    "reschedul", "change the date", "move the date", "postpon", "rebook", "can be rescheduled",
)
NEG_RESCHEDULE = (
    "cannot be reschedul", "can't be reschedul", "not possible to reschedul", "unable to reschedul",
    "cannot be changed", "can't be changed", "not eligible to reschedul", "not be rescheduled",
    "cannot change the date", "cannot change your",
)
POS_EXTEND = (
    "can be extended", "extend your", "extension", "postpon", "valid for", "prolong", "you can extend",
)
NEG_EXTEND = (
    "cannot be extended", "can't be extended", "not be extended", "no extension", "extension is not",
    "not eligible for an extension", "can't extend", "cannot give you an extension", "unable to extend",
)
DEAD_ENDS = (
    "not able to answer", "unable to answer", "cannot answer", "can't answer", "not available to help",
    "unable to help", "cannot help with", "cannot assist", "not in my ability", "fallback",
    "error processing that request", "encountered an error",
    "something went wrong", "temporary glitch",
)
# Tolerant of small wording insertions ("ran into a quick snag" should still match).
DEAD_END_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"ran? into (?:\w+ ){0,2}(?:a |an )?(?:snag|error|issue|glitch|problem)",
    )
)

# Sentences containing these are describing an *intent to check*, not a claim — they
# must never count as an affirmative or negative statement of fact.
HEDGE_MARKERS = (
    "check if", "checking if", "check whether", "checking whether", "will check",
    "let me check", "verify if", "verifying if", "find out if", "look into whether",
    "look into if", "confirm if", "confirming if",
)
POS_TICKET = (
    "ticket will be", "ticket has been", "ticket is ready", "ticket was",
    "receive your ticket", "you'll receive", "you will receive", "you'll get",
    "you will get", "delivered", "delivery", "sent to", "sent via",
    "will be sent", "will be emailed", "emailed to", "e-ticket", "eticket",
    "download", "check your email", "qr code", "mobile ticket",
)
NEG_TICKET = ()
LANGUAGE_NOISE = ("{{", "}}", "```", "<p>", "<br", "lorem ipsum", "placeholder text", "unsubscribe {{")

NODE_FACTS = {
    "cancel": ("fact_cancellable", "cancellable", POS_CANCEL, NEG_CANCEL),
    "reschedule": ("fact_reschedulable", "reschedulable", POS_RESCHEDULE, NEG_RESCHEDULE),
    "modify": ("fact_reschedulable", "changeable", POS_RESCHEDULE, NEG_RESCHEDULE),
    "extend": ("fact_extendable", "extendable", POS_EXTEND, NEG_EXTEND),
    "ticket": ("fact_ticket", "ticket delivery", POS_TICKET, NEG_TICKET),
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Grade:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _sentences(texts: list[str]) -> list[str]:
    out: list[str] = []
    for t in texts:
        out.extend(s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip())
    return out


def _repeated_bot_message(bot_texts: list[str]) -> str | None:
    # Catches the bot re-asking the same question (near-)verbatim after the guest
    # already answered it — a real conversational failure that no keyword/fact
    # check would otherwise notice, since both messages are individually "correct".
    for a, b in zip(bot_texts, bot_texts[1:]):
        if len(a) < 12 or len(b) < 12:
            continue
        ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
        if ratio >= 0.75:
            return b
    return None


def _stance(texts: list[str], positive: tuple[str, ...], negative: tuple[str, ...]) -> str:
    # Drop hedging sentences ("I'll check if it's eligible for cancellation") before
    # scanning — they describe an intent to look something up, not a claim of fact,
    # and were previously misread as an affirmative answer.
    sentences = [s.lower() for s in _sentences(texts) if not any(h in s.lower() for h in HEDGE_MARKERS)]
    joined = " ".join(sentences)
    has_pos = any(p in joined for p in positive)
    has_neg = any(n in joined for n in negative)
    if has_pos and has_neg:
        return "both"
    if has_pos:
        return "pos"
    if has_neg:
        return "neg"
    return "none"


class Grader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        # Grading is independent of which engine drives the simulated user — an LLM
        # key enables LLM grading (incl. pass_criteria) even on the scripted engine.
        if settings.llm_api_key:
            self._client = httpx.AsyncClient(
                base_url=settings.llm_api_base,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                timeout=httpx.Timeout(60.0),
            )

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()

    def grade(self, scenario: Scenario, transcript: list[Event]) -> Grade:
        bot_texts = [e.text for e in transcript if e.role == "bot" and e.text]
        checks: list[CheckResult] = []
        notes: list[str] = []

        checks.append(
            CheckResult("bot_replied", bool(bot_texts), f"{len(bot_texts)} bot replies")
        )

        deadend = [
            t for t in bot_texts
            if any(d in t.lower() for d in DEAD_ENDS) or any(p.search(t.lower()) for p in DEAD_END_PATTERNS)
        ]
        if deadend:
            checks.append(CheckResult("not_a_dead_end", False, "bot fell back to: " + deadend[-1][:80]))
        else:
            checks.append(CheckResult("not_a_dead_end", True, ""))

        ground_truths: list[tuple[str, bool | None, str | None, tuple, tuple]] = []
        booking = scenario.booking
        if booking.is_cancellable is not None:
            ground_truths.append(("fact_cancellable", booking.is_cancellable, "cancellable", POS_CANCEL, NEG_CANCEL))
        if booking.is_reschedulable is not None:
            ground_truths.append(("fact_reschedulable", booking.is_reschedulable, "reschedulable", POS_RESCHEDULE, NEG_RESCHEDULE))
        if booking.has_extended_validity is not None:
            ground_truths.append(("fact_extendable", booking.has_extended_validity, "extendable", POS_EXTEND, NEG_EXTEND))
        elif booking.extended_validity:
            ground_truths.append(("fact_extendable", True, "extendable", POS_EXTEND, NEG_EXTEND))

        required = NODE_FACTS.get(scenario.node)
        facts: dict[str, tuple[str, bool | None, tuple, tuple]] = {}
        for name, truth, label, pos, neg in ground_truths:
            facts[name] = (label, truth, pos, neg)
        if required and required[0] not in facts:
            facts[required[0]] = (required[1], None, required[2], required[3])

        for name, (label, truth, pos, neg) in facts.items():
            stance = _stance(bot_texts, pos, neg)
            is_required = required is not None and name == required[0]
            if stance == "both":
                checks.append(CheckResult(name, False, f"bot gave mixed answers on {label}"))
                continue
            if stance == "none":
                if is_required:
                    checks.append(
                        CheckResult(
                            f"missing_{name}",
                            False,
                            f"bot never stated whether booking is {label} (required answer for '{scenario.node}' scenario)",
                        )
                    )
                else:
                    checks.append(CheckResult(name, True, f"bot did not state whether booking is {label}"))
                continue
            asserted = stance == "pos"
            correct = asserted == truth if truth is not None else asserted
            if truth is not None:
                detail = f"sheet says {label}={truth}; bot asserted {label}={asserted}"
            else:
                detail = f"bot stated booking is {label}={asserted}"
            checks.append(CheckResult(name, correct, detail))
            if not correct and truth is not None:
                notes.append(f"bot said {label}={asserted} but sheet says {label}={truth}")

        noise = [t for t in bot_texts if any(m in t for m in LANGUAGE_NOISE)]
        checks.append(
            CheckResult(
                "template_artifacts",
                not bool(noise),
                "noise/language artifacts detected: " + noise[-1][:60] if noise else "no unrendered template artifacts",
            )
        )
        if noise:
            notes.append("grammar/noise issues detected in bot replies")

        repeated = _repeated_bot_message(bot_texts)
        checks.append(
            CheckResult(
                "no_repeated_question",
                repeated is None,
                f"bot re-asked essentially the same thing: {repeated[:80]}" if repeated else "",
            )
        )
        if repeated:
            notes.append("bot repeated a prior question instead of moving the conversation forward")

        user_texts = [e.text for e in transcript if e.role == "user" and e.text]
        bot_escalated = any(any(m in t.lower() for m in BOT_ESCALATE_MARKERS) for t in bot_texts)
        if bot_escalated:
            user_requested = any(any(w in t.lower() for w in ESCALATE_WORDS) for t in user_texts)
            # An escalation the guest asked for is a legitimate resolution path.
            # An escalation the bot reaches for on its own — typically right after
            # a dead-end/fallback reply — means it gave up rather than helped, and
            # that must count as a failure, not disappear into a neutral "escalated"
            # bucket the way it previously did.
            justified = user_requested or not deadend
            checks.append(
                CheckResult(
                    "escalation_justified",
                    justified,
                    "escalated without the guest asking and after a dead-end reply"
                    if not justified
                    else "escalation followed the guest's request" if user_requested else "escalation not preceded by a dead-end reply",
                )
            )
            if not justified:
                notes.append("bot escalated unprompted instead of resolving or the guest asking for a human")

        passed = all(c.passed for c in checks)
        return Grade(passed=passed, checks=checks, notes=notes)

    async def grade_llm(self, scenario: Scenario, transcript: list[Event]) -> Grade | None:
        if self._client is None:
            return None
        facts = {
            "booking_id": scenario.booking.booking_id,
            "title": scenario.booking.booking_title,
            "date": scenario.booking.booking_date,
            "status": scenario.booking.booking_status,
            "is_cancellable": scenario.booking.is_cancellable,
            "is_reschedulable": scenario.booking.is_reschedulable,
            "extended_validity": scenario.booking.extended_validity,
        }
        history = [{"role": "user" if e.role == "user" else "assistant", "content": e.text} for e in transcript if e.text]
        criteria_block = ""
        if scenario.pass_criteria:
            criteria_block = (
                "\n\nThe QA author who wrote this scenario also defined explicit pass criteria. "
                "These are the authoritative bar for passing — mark passed=false if the transcript "
                "fails to satisfy any one of them:\n"
                + "\n".join(f"- {c}" for c in scenario.pass_criteria)
            )
        system = (
            "You are a QA grader for a customer support chatbot. The bot was tested against real "
            "ground-truth booking facts. Grade the transcript for: (1) factual correctness of the "
            "bot's answers vs the ground-truth facts (cancellable/reschedulable/extendable), "
            "(2) English grammar, spelling, tone and clarity, (3) hallucinated booking details, "
            "(4) escalation handled appropriately, (5) whether the conversation reached a real "
            "resolution — a transcript that just stops mid-conversation without the guest's last "
            "message being addressed should not pass." + criteria_block + "\n\nRespond with JSON only: "
            '{"passed": true|false, "failures": ["..."], "notes": ["..."]}'
        )
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps({"facts": facts, "transcript": history})},
            ],
            "temperature": 0.0,
            # Without this, models like gpt-4o routinely wrap the JSON in a
            # ```json ... ``` fence "for readability" despite being told to
            # respond with JSON only — that fence used to fail json.loads()
            # and silently turn every LLM-graded scenario into a false FAIL,
            # with the raw fenced text reported as the "failure" reason.
            "response_format": {"type": "json_object"},
        }
        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            data = {"passed": False, "failures": [content], "notes": []}
        checks = [CheckResult(f"llm:{f}", not f, f) for f in data.get("failures", [])]
        return Grade(passed=bool(data.get("passed")), checks=checks, notes=data.get("notes", []))