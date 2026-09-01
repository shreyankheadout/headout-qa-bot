from __future__ import annotations

from typing import Protocol

import httpx

from .config import Settings
from .scenarios import Scenario
from .transcript import Event

END_MARKER = "<END>"

# scenario_text is authored for two different consumers: the LLMUserEngine treats
# it as a system-prompt persona instruction ("act as a customer who..."), but the
# ScriptedUserEngine sends it verbatim as a literal chat message to the real
# production bot. If a QA author only wrote the LLM-style version, that
# instruction text leaks straight into the live conversation ("You are an
# automated tester. Act as a customer who...") and confuses the bot being tested,
# corrupting the scenario. Detect that shape and drop it instead of sending it.
_META_INSTRUCTION_MARKERS = (
    "automated tester", "act as a customer", "act as the customer", "you are an ai",
    "you are a bot", "simulate a customer", "role-play as", "role play as",
    "initiate a conversation to test",
)


def _is_meta_instruction(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _META_INSTRUCTION_MARKERS)


class UserEngine(Protocol):
    async def next_message(self, scenario: Scenario, transcript: list[Event]) -> str | None:
        ...


class ScriptedUserEngine:
    def __init__(self, settings: Settings) -> None:
        self.max_turns = settings.max_turns

    def _question(self, node: str, booking) -> str:
        if node == "extend":
            return "Is it possible to extend my booking? How long is it valid for?"
        if node == "reschedule":
            return "I'd like to reschedule my booking. Is that possible?"
        if node == "modify":
            return "I'd like to modify my booking. Can you help with that?"
        if node == "ticket":
            return "When will I receive my ticket?"
        return "I want to cancel this booking. Is it cancellable?"

    async def next_message(self, scenario: Scenario, transcript: list[Event]) -> str | None:
        user_turns = [e for e in transcript if e.role == "user"]
        booking = scenario.booking
        if not user_turns:
            greeting = f"Hi, I need help with my booking {booking.booking_id}."
            # Surface the sheet's scenario-specific context in the opening message so
            # the AI agent is actually exercised on the nuance the QA author wrote,
            # not just a generic node-shaped question.
            if scenario.scenario_text and not _is_meta_instruction(scenario.scenario_text):
                greeting += f" {scenario.scenario_text}"
            return greeting
        if len(user_turns) == 1:
            return self._question(scenario.node, booking)
        if len(user_turns) == 2:
            if scenario.node in ("cancel", "extend", "modify", "reschedule") and not booking.is_cancellable:
                return "That's really frustrating. I've never had this problem before. Is there anything you can do?"
            return "Okay, please go ahead with that."
        if len(user_turns) == 3:
            if scenario.node in ("cancel", "extend", "modify", "reschedule") and not booking.is_cancellable:
                return "I'd like to speak to a supervisor about this."
            return None
        return None


class LLMUserEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.llm_api_base,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(60.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _system_prompt(self, scenario: Scenario) -> str:
        booking = scenario.booking
        facts = "\n".join(
            [
                f"booking_id: {booking.booking_id}",
                f"email: {booking.email_id}",
                f"title: {booking.booking_title or 'n/a'}",
                f"date: {booking.booking_date or 'n/a'}",
                f"status: {booking.booking_status or 'n/a'}",
                f"is_cancellable: {booking.is_cancellable if booking.is_cancellable is not None else 'unknown'}",
                f"extended_validity: {booking.extended_validity or 'n/a'}",
            ]
        )
        scenario_text = scenario.scenario_text or "The guest needs help with their booking."
        return (
            "You are role-playing as a real customer contacting Headout support about your booking.\n"
            f"Booking context (your booking):\n{facts}\n\n"
            f"Your scenario:\n{scenario_text}\n\n"
            "Rules:\n"
            "- Speak naturally as a customer, 1-2 short sentences per message.\n"
            "- Do not reveal you are a test or a bot.\n"
            "- Only claim information you would actually know as a customer.\n"
            "- React to the agent's answers realistically per your scenario.\n"
            "- When the goal of your scenario is resolved (or you've decided to leave/escalate), "
            f"end your reply with {END_MARKER} and nothing else after it.\n"
        )

    def _messages(self, transcript: list[Event]) -> list[dict]:
        messages = []
        for event in transcript:
            if event.role == "user":
                messages.append({"role": "user", "content": event.text})
            elif event.role == "bot":
                messages.append({"role": "assistant", "content": event.text})
        return messages

    async def next_message(self, scenario: Scenario, transcript: list[Event]) -> str | None:
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": self._system_prompt(scenario)},
                *self._messages(transcript),
            ],
            "temperature": 0.8,
            "max_tokens": 120,
        }
        resp = await self._client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if END_MARKER in content:
            return None
        return content


def build_user_engine(settings: Settings) -> UserEngine:
    if settings.llm_provider in ("openai", "deepseek", "compatible"):
        return LLMUserEngine(settings)
    return ScriptedUserEngine(settings)
