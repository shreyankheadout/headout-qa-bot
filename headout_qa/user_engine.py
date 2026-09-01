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
    # Current authoring style (data/build_bookings_sheet.py) writes third-person
    # persona/context prose for the LLMUserEngine's system prompt -- "The guest
    # has a booking for X and wants to... Mood: ..." -- which doesn't match the
    # older imperative markers above but is just as much QA-authoring content,
    # not something a real customer would ever type into a chat.
    "the guest has", "the guest wants", "the guest reports", "the guest is",
    "they open the chat", "mood:",
)


def _is_meta_instruction(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _META_INSTRUCTION_MARKERS)


class UserEngine(Protocol):
    async def next_message(self, scenario: Scenario, transcript: list[Event]) -> str | None:
        ...


def opening_line(booking) -> str:
    # The Zendesk AI agent never sends a real, API-visible proactive greeting (its
    # website-widget "instant reply" is a client-side widget feature with no
    # backend message behind it -- confirmed by polling for one directly against
    # the API), so waiting for one is pointless. Lead with booking ID + email
    # up front instead, so the bot can identify the booking without a
    # back-and-forth identity-verification detour eating into every scenario.
    parts = [f"Booking ID {booking.booking_id}"]
    if booking.email_id:
        parts.append(f"email {booking.email_id}")
    return "Hey, I need help. " + ", ".join(parts) + "."


# The bot may still ask again anyway (e.g. to confirm) even though we already
# volunteered this in the opener -- kept as a reactive safety net so the engine
# answers rather than stalling on a repeated identity question.
_BOOKING_ID_ASK_MARKERS = (
    "booking id", "booking reference", "reference number", "confirmation number",
    "order id", "booking number",
)
_EMAIL_ASK_MARKERS = ("email address", "email id", "your email", "the email")


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

    def _real_ask(self, scenario: Scenario) -> str:
        text = self._question(scenario.node, scenario.booking)
        if scenario.scenario_text and not _is_meta_instruction(scenario.scenario_text):
            text = f"{text} {scenario.scenario_text}"
        return text

    async def next_message(self, scenario: Scenario, transcript: list[Event]) -> str | None:
        user_turns = [e for e in transcript if e.role == "user"]
        bot_turns = [e for e in transcript if e.role == "bot"]
        booking = scenario.booking
        mood = booking.mood or "okay"
        opener = opening_line(booking)

        if not user_turns:
            return opener

        last_bot_text = (bot_turns[-1].text or "").lower() if bot_turns else ""
        booking_id_answer = f"Sure, my booking ID is {booking.booking_id}."
        email_answer = f"It's {booking.email_id}." if booking.email_id else None
        given_texts = {e.text for e in user_turns}
        real_ask = self._real_ask(scenario)

        # React to whatever the bot is actually asking for right now, whenever it
        # asks -- before the scenario starts, or mid-negotiation if it re-asks.
        if any(m in last_bot_text for m in _BOOKING_ID_ASK_MARKERS) and booking_id_answer not in given_texts:
            return booking_id_answer
        if email_answer and any(m in last_bot_text for m in _EMAIL_ASK_MARKERS) and email_answer not in given_texts:
            return email_answer

        if real_ask not in given_texts:
            return real_ask

        # The real ask has been delivered; count only the substantive turns since
        # then (skip the opener and any identity-verification answers) to decide
        # the accept / pushback / escalate beat.
        skip = {opener, booking_id_answer}
        if email_answer:
            skip.add(email_answer)
        substantive = [t for t in (e.text for e in user_turns) if t not in skip]
        turns_since_ask = len(substantive) - substantive.index(real_ask) - 1

        denied = scenario.node in ("cancel", "extend", "modify", "reschedule") and not booking.is_cancellable
        if turns_since_ask == 0:
            if denied and mood == "angry":
                return "This is unacceptable. I need this sorted out right now."
            if denied and mood == "frustrated":
                return "That's really frustrating. I've never had this problem before. Is there anything you can do?"
            if mood == "happy":
                return "No worries, thank you so much for the help!"
            return "Okay, please go ahead with that."
        if turns_since_ask == 1:
            if denied and mood in ("angry", "frustrated"):
                return "I'd like to speak to a supervisor about this."
            return None
        return None


# Mood -> concrete behavioral instruction for the LLM playing the guest. This is the
# thing that actually varies conversational behavior; scenario_text supplies the
# situational content (what happened), mood supplies the delivery (how they react).
MOOD_INSTRUCTIONS = {
    "happy": (
        "Your mood is happy: you're relaxed and easygoing about this. Accept the agent's first "
        "reasonable answer without pushing back, and thank them warmly once it's resolved."
    ),
    "okay": (
        "Your mood is okay/neutral: you're calm and matter-of-fact. State your question plainly "
        "and accept a clear, correct answer without much back-and-forth."
    ),
    "frustrated": (
        "Your mood is frustrated: this situation is annoying you. If the agent's first answer "
        "doesn't actually resolve things, push back once with a bit of edge before accepting a "
        "clear resolution or, if none comes, asking to speak to a supervisor."
    ),
    "angry": (
        "Your mood is angry: you're upset and impatient from your very first message. Push back "
        "firmly at least twice if you're not immediately satisfied, and explicitly threaten to "
        "ask for a supervisor if the agent doesn't resolve this quickly."
    ),
}


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
        mood_instruction = MOOD_INSTRUCTIONS.get(booking.mood or "okay", MOOD_INSTRUCTIONS["okay"])
        return (
            "You are role-playing as a real customer contacting Headout support about your booking.\n"
            f"Booking context (your booking):\n{facts}\n\n"
            f"Your scenario:\n{scenario_text}\n\n"
            f"Your mood:\n{mood_instruction}\n\n"
            "Rules:\n"
            "- Read the support agent's last message carefully and reply directly to what they "
            "specifically said (answer their question, react to their offer/denial) -- never ignore "
            "it or fall back to a generic line, while staying true to your scenario and mood.\n"
            "- Speak naturally as a customer, 1-2 short sentences per message.\n"
            "- Do not reveal you are a test or a bot.\n"
            "- Only claim information you would actually know as a customer.\n"
            "- You are the CUSTOMER in this conversation, never the support agent -- do not offer to "
            "investigate, escalate, or resolve anything; that is the agent's job, not yours.\n"
            "- You already gave your booking ID and email in your very first message. Don't repeat "
            "them again unless the agent specifically asks you to confirm or re-provide one.\n"
            "- When the goal of your scenario is resolved (or you've decided to leave/escalate), "
            f"end your reply with {END_MARKER} and nothing else after it.\n"
        )

    def _messages(self, transcript: list[Event]) -> list[dict]:
        # Our own Event.role is "user"=simulated guest, "bot"=the real Zendesk bot. The
        # OpenAI-style roles below are inverted relative to that on purpose: chat-completion
        # APIs always generate the next "assistant" turn, and it's OUR guest that this call
        # is generating the next line for -- so the guest's own past lines must be tagged
        # "assistant" (the model's own prior output) and the real bot's lines "user" (the
        # other party talking to it). Tagging them the other way around (as this used to)
        # made the model complete the "assistant" turn using the *bot's* established voice
        # from the conversation history, overriding the system prompt's persona instruction
        # more and more as the conversation went on -- the simulated guest would visibly
        # drift into talking like a support agent by turn 3-4.
        messages = []
        for event in transcript:
            if event.role == "user":
                messages.append({"role": "assistant", "content": event.text})
            elif event.role == "bot":
                messages.append({"role": "user", "content": event.text})
        return messages

    async def next_message(self, scenario: Scenario, transcript: list[Event]) -> str | None:
        if not any(e.role == "user" for e in transcript):
            return opening_line(scenario.booking)
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
