"""Build the rebuilt `bookings` tab CSV for the QA ground-truth sheet.

Reads leaf_mood_data.json (v2: full cross-product -- every one of the 76 real
L1/L2/L3 leaves x all 4 moods, grounded in real per-slice data where it exists and
falling back to the leaf's overall majority vote where a mood has zero real examples
this quarter) and emits one scenario row per (leaf, mood) pair, with:
  - node derived from L1 (drives which fact grader.py checks)
  - isCancellable / isReschedulable / has-extended-validity set deliberately by mood
    for the node's core fact (denial <-> frustrated/angry, approval <-> okay/happy),
    matching the existing hardcoded frustration-arc logic in scenarios.py/user_engine.py
  - other descriptive fields (booking_status, SLA, same-day, tour_inventory_type,
    validity_type, refund/alternates status) taken from the real per-leaf-mood majority vote
  - a hand-templated, mood-calibrated scenario_text (NOT the old meta "You are an
    automated tester..." boilerplate, which the code's own _is_meta_instruction filter
    strips out for the scripted engine -- this rewrite fixes that silent gap too)

Output: new_bookings.csv, using the exact same 44-column header as the live sheet plus
one new `mood` column at the end (45 columns total).
"""
from __future__ import annotations

import csv
import json
import re

DATA_PATH = "/tmp/claude-0/-home-user-headout-qa-bot/4912fdb0-3cbd-5d0b-943d-608d5f687798/scratchpad/leaf_mood_data.json"
OUT_PATH = "/tmp/claude-0/-home-user-headout-qa-bot/4912fdb0-3cbd-5d0b-943d-608d5f687798/scratchpad/new_bookings.csv"

ALL_MOODS = ("happy", "okay", "frustrated", "angry")

HEADER = [
    "bookingId", "email", "booking_status", "scenario_text", "isCancellable", "bookingStatus",
    "isPendingCancellation", "isReschedulable", "oopCancellationAllowed", "vendorId", "tourId",
    "tourGroupId", "inventoryDateTime", "refundReferenceNumber", "alternatesStatus", "alternatesLink",
    "isMoreInfoRequested", "isSLABreached", "isSameDayBooking", "resolutionTime",
    "isTextEtaBeforeDateOfExp", "ticketValidityType", "ticketId", "vendorRefId", "tourGroupName",
    "primaryCustomerName", "guestCount", "secureBookingId", "fulfillmentType", "statusCode",
    "variantName", "latitude", "longitude", "address", "redemptionInstruction", "tourInventoryType",
    "itineraryId", "secureItineraryId", "machineCodeType", "ticketValidityUntilDate",
    "ticketValidityUntilDaysFromPurchase", "L1", "L2", "L3", "mood",
]

# Reused verbatim from the existing sheet (same 10 destinations, cycled) so the rebuilt
# sheet stays visually/stylistically consistent with what's already there.
TOURS = [
    dict(name="The Colosseum", variant="Direct Entry Ticket", lat=41.8902, lon=12.4922,
         address="Piazza del Colosseo, 1, 00184 Roma RM, Italy",
         redemption="Please present this e-ticket at the main entrance turnstiles. Ensure the barcode is clearly visible on your mobile device. No need to print.",
         machine="BARCODE_UPC_A"),
    dict(name="Vatican Museums", variant="Ticket with Audioguide", lat=41.9065, lon=12.4536,
         address="00120 Vatican City",
         redemption="Head to the audio guide desk located past the security check. Show this voucher to collect your device and headphones.",
         machine="BARCODE_UPC_A"),
    dict(name="Universal Studios Hollywood", variant="Guided Tour", lat=34.1381, lon=-118.3534,
         address="100 Universal City Plaza, Universal City, CA 91608, USA",
         redemption="Meet your guide at the designated meeting point 15 minutes before the tour starts. Look for the guide holding a red umbrella.",
         machine="BARCODE_CODE_39"),
    dict(name="Eiffel Tower", variant="Skip-the-line Ticket", lat=48.8584, lon=2.2945,
         address="Champ de Mars, 5 Ave Anatole France, 75007 Paris, France",
         redemption="Proceed directly to the priority entrance lane marked 'Skip-the-line'. Have your ID and e-ticket ready for scanning.",
         machine="HTML_URL"),
    dict(name="Statue of Liberty", variant="VIP Access", lat=40.6892, lon=-74.0445,
         address="New York, NY 10004, USA",
         redemption="Please use the VIP entrance located on the east side of the building. Present your voucher to the VIP concierge for immediate assistance.",
         machine="WALLET_PASS"),
    dict(name="Louvre Museum", variant="Direct Entry Ticket", lat=48.8606, lon=2.3376,
         address="Rue de Rivoli, 75001 Paris, France",
         redemption="Please present this e-ticket at the main entrance turnstiles. Ensure the barcode is clearly visible on your mobile device. No need to print.",
         machine="PDF_URL"),
    dict(name="Burj Khalifa", variant="Ticket with Audioguide", lat=25.1972, lon=55.2744,
         address="1 Sheikh Mohammed bin Rashid Blvd - Downtown Dubai - Dubai - United Arab Emirates",
         redemption="Head to the audio guide desk located past the security check. Show this voucher to collect your device and headphones.",
         machine="QR_CODE"),
    dict(name="Sydney Opera House", variant="Guided Tour", lat=-33.8568, lon=151.2153,
         address="Bennelong Point, Sydney NSW 2000, Australia",
         redemption="Meet your guide at the designated meeting point 15 minutes before the tour starts. Look for the guide holding a red umbrella.",
         machine="TEXT"),
    dict(name="Taj Mahal", variant="Skip-the-line Ticket", lat=27.1751, lon=78.0421,
         address="Dharmapuri, Forest Colony, Tajganj, Agra, Uttar Pradesh 282001, India",
         redemption="Proceed directly to the priority entrance lane marked 'Skip-the-line'. Have your ID and e-ticket ready for scanning.",
         machine="BARCODE_CODE_128"),
    dict(name="Sagrada Familia", variant="VIP Access", lat=41.4036, lon=2.1744,
         address="C/ de Mallorca, 401, l'Eixample, 08013 Barcelona, Spain",
         redemption="Please use the VIP entrance located on the east side of the building. Present your voucher to the VIP concierge for immediate assistance.",
         machine="BARCODE_UPC_A"),
]

RESOLUTION_TIMES_OK = ["30 minutes", "1 hour", "2 hours", "4 hours"]
RESOLUTION_TIMES_BREACHED = ["1 day", "7 days", "30 days"]

FIRST_NAMES = ["John", "Jane", "Michael", "Emily", "Christopher", "Ashley", "Matthew", "Amanda",
               "Joshua", "Brittany", "William", "Megan", "David", "Rachel", "James", "Samantha",
               "Daniel", "Sarah", "Andrew", "Jessica", "Ryan", "Melissa", "Kevin", "Danielle",
               "Brian", "Kimberly", "Timothy", "Amy", "Jason", "Rebecca"]
LAST_NAMES = ["Doe", "Smith", "Johnson", "Davis", "Taylor", "Jackson", "Brown", "Wilson", "Thomas",
              "Martinez", "Robinson", "Clark", "Rodriguez", "Lewis", "Lee", "Thompson", "Martin",
              "Harris", "White", "Walker", "Scott", "Green", "Adams", "Baker", "Nelson", "Carter",
              "Mitchell", "Perez", "Roberts", "Turner"]

NODE_BY_L1 = {
    "Cancellation Request": "cancel",
    "Modification Request": "modify",
    "Delay Fulfilment": "ticket",
    "Ticket Redemption Details": "ticket",
    # Not "cancel": the guest here isn't asking whether they CAN cancel -- the
    # amendment already happened, they're following up on its outcome (refund status).
    # There's no dedicated refund fact in grader.py's NODE_FACTS, so this stays general
    # and gets judged qualitatively rather than against a fabricated cancellability claim.
    "Amended Booking Response": "general",
    "Refund Related": "general",
    "Payment Failure": "general",
    "Reserve Now Pay Later": "general",
    "Vendor Query": "general",
    "Service Issues": "general",
    "General Information": "general",
    "Fraudulent": "general",
}

# Per-L1 narrative: `situation` sets up why the guest is contacting support, `ask` is
# the concrete opening question they lead with — kept separate so the generated text
# reads like a real support-chat setup, not a category label restated as a sentence.
L1_CONTEXT = {
    "Cancellation Request": dict(
        situation="wants to cancel their upcoming booking",
        ask="They open the chat by explaining why, and ask directly whether the booking can be cancelled and refunded.",
    ),
    "Modification Request": dict(
        situation="wants to keep this booking but needs a detail on it changed",
        ask="They open the chat describing exactly what needs to change and ask whether the agent can update the booking for them.",
    ),
    "Delay Fulfilment": dict(
        situation="paid for this booking but still hasn't received their tickets or voucher",
        ask="They open the chat asking where their tickets are and when they'll actually receive them.",
    ),
    "Ticket Redemption Details": dict(
        situation="already has a valid ticket for this booking but isn't sure exactly how or where to use it",
        ask="They open the chat with a practical redemption question so they know what to do on the day.",
    ),
    "Amended Booking Response": dict(
        situation="had Headout previously amend this booking (e.g. adjust or reverse part of it) and is following up",
        ask="They open the chat asking for a status update on that amendment, specifically their refund.",
    ),
    "Refund Related": dict(
        situation="is waiting on, or disputing, a refund tied to this booking",
        ask="They open the chat asking directly about the refund — its status, amount, or why it doesn't match what they expected.",
    ),
    "Payment Failure": dict(
        situation="tried to pay for this booking and the payment didn't go through cleanly",
        ask="They open the chat explaining what happened at checkout and asking the agent to help them complete or fix the booking.",
    ),
    "Reserve Now Pay Later": dict(
        situation="used the Reserve Now, Pay Later option on this booking and has a question about the payment terms",
        ask="They open the chat asking the agent to clarify exactly how or when they'll actually be charged.",
    ),
    "Vendor Query": dict(
        situation="is a supplier-side contact, not the guest, asking about this specific booking on their platform",
        ask="They open the chat referencing the guest's booking and asking the agent to confirm or action something on the supplier's behalf.",
    ),
    "Service Issues": dict(
        situation="already went on the tour, and something about the actual experience fell short",
        ask="They open the chat describing what went wrong on the day and asking what Headout can do about it after the fact.",
    ),
    "General Information": dict(
        situation="needs a factual answer about this booking (or about booking with Headout in general) before deciding what to do next",
        ask="They open the chat with a direct question and expect a clear, accurate answer.",
    ),
    "Fraudulent": dict(
        situation="believes their account or payment method was used without their knowledge to make this booking",
        ask="They open the chat reporting the suspicious booking and asking for it to be investigated and reversed.",
    ),
}

FACT_AWARENESS = {
    "cancel": (
        "This booking genuinely IS cancellable — a correct agent should confirm that and go ahead with the cancellation.",
        "This booking genuinely is NOT cancellable — a correct agent should clearly and firmly explain that (not hedge or guess) and offer any real alternative available.",
    ),
    "modify": (
        "This booking genuinely CAN be modified/rescheduled — a correct agent should confirm that and help make the change.",
        "This booking genuinely CANNOT be modified/rescheduled — a correct agent should clearly and firmly explain that and offer any real alternative available.",
    ),
    "extend": (
        "This ticket's validity genuinely CAN be extended — a correct agent should confirm that and explain the new validity.",
        "This ticket's validity genuinely CANNOT be extended — a correct agent should clearly and firmly explain that.",
    ),
}

# Mood -> concrete behavioral brief for the LLM playing the guest. Deliberately spells
# out the trigger condition and the escalation trajectory (turn-by-turn), not just a
# tone adjective, so the simulated guest's reactions are consistent and testable.
MOOD_BEHAVIOR = {
    "happy": (
        "Mood: happy. They're relaxed and good-humoured about the whole thing. They accept the agent's "
        "first reasonable, correct answer without any pushback, and close the conversation with a genuine "
        "thank-you once it's resolved."
    ),
    "okay": (
        "Mood: okay/neutral. They're calm and businesslike. They state their question plainly, listen to "
        "the answer, and accept it without complaint as long as it's a clear, correct answer to what they "
        "actually asked — no need to manufacture friction."
    ),
    "frustrated": (
        "Mood: frustrated. This situation is genuinely annoying them, and it shows in their tone from the "
        "first message. If the agent's first answer doesn't actually resolve things, they push back once, "
        "more sharply, before either accepting a real resolution or — if they still don't get one — asking "
        "to speak to a supervisor."
    ),
    "angry": (
        "Mood: angry. They're upset and impatient from their very first message, and they say so directly. "
        "If they aren't given a clear, satisfying answer right away, they push back firmly at least twice, "
        "and if it's still not resolved they explicitly say they want to speak to a supervisor or manager."
    ),
}


# build_default_scenarios() (scenarios.py) derives `node` -- which fact grader.py
# checks -- by scanning scenario_text for keywords in a fixed priority order (cancel,
# extend, reschedule/postpone, modify/change, ticket/deliver, else general). A couple of
# leaf names contain "cancel*" or "ticket" as an incidental word even though the leaf
# itself isn't a cancellation/ticket-delivery scenario, which would silently misdirect
# grading onto the wrong fact. Override just those leaves' detail phrasing to route to
# the right node instead of literally restating the raw L3 label.
DETAIL_OVERRIDES = {
    ("Modification Request", "Customer Related", "Flight/train Cancellation"):
        "their flight or train got rescheduled by the airline/operator, so they need Headout to change their booking to match",
    ("Service Issues", "Sp Related", "Tour Cancelled By Sp"):
        "the tour operator called off the tour on the day, after the guest had already gone through with the booking",
}

# A handful of leaves need their whole situation/ask rewritten, not just the detail
# phrase -- the generic L1-level framing doesn't actually match what the leaf is about
# (e.g. "Ticket Redemption Details" is framed as "how do I use my ticket", but the
# "Extended Validity" leaf is really about a guest who can't make their original date
# and wants to know if the ticket's validity can be pushed out instead).
LEAF_CONTEXT_OVERRIDES = {
    ("Ticket Redemption Details", "Extended Validity", ""): dict(
        situation="can no longer make the originally booked date for this ticket",
        ask=(
            "They open the chat explaining they can't make it on the scheduled date and ask directly "
            "whether the ticket's validity can be extended so they can use it on a later date instead."
        ),
    ),
}


def humanize(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    # "Didn T Get Tickets" -> "didn't get tickets"; "Sp Related" -> "SP related"
    text = re.sub(r"\bT\b", "'t", text)
    words = [w.upper() if w.upper() == "SP" else w.lower() for w in text.split(" ")]
    return " ".join(words).replace(" 't ", "'t ")


def scenario_text_for(
    l1: str, l2: str, l3: str, mood: str, *,
    tour_name: str, guest_count: str, node: str, approved: bool,
) -> str:
    if (l1, l2, l3) in DETAIL_OVERRIDES:
        detail = DETAIL_OVERRIDES[(l1, l2, l3)]
    else:
        detail = humanize(l3) if l3 else (humanize(l2) if l2 else "")
    if (l1, l2, l3) in LEAF_CONTEXT_OVERRIDES:
        ctx = LEAF_CONTEXT_OVERRIDES[(l1, l2, l3)]
    else:
        ctx = L1_CONTEXT.get(l1, dict(
            situation="has a question about their booking",
            ask="They open the chat with their question and expect a clear answer.",
        ))

    party = "a solo booking" if guest_count == "1" else f"a booking for {guest_count} guests"
    parts = [f"The guest has {party} for {tour_name} and {ctx['situation']}."]
    if detail:
        parts.append(f"Specifically, this is about: {detail}.")
    parts.append(ctx["ask"])

    if node in FACT_AWARENESS:
        parts.append(FACT_AWARENESS[node][0 if approved else 1])

    parts.append(MOOD_BEHAVIOR[mood])
    return " ".join(parts)


def yn_to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() == "yes"


# A scenario's mock-API state is a snapshot of the booking BEFORE the guest's message --
# it must describe a state consistent with the guest still having something to ask about
# in this conversation. For most L1s, the guest is asking about something that has NOT
# happened yet (they want to cancel, modify, know when tickets arrive, etc.) -- for those,
# the booking can't already be CANCELLED (that would mean the thing they're about to ask
# for already happened) and there can't already be a refundReferenceNumber (no refund has
# been processed yet; whether one gets issued is the outcome of THIS conversation, not a
# pre-existing fact). Only L1s that are inherently about a booking whose cancellation/
# refund already happened -- and the guest is following up on it -- get to start CANCELLED
# with a real refund reference.
BACKWARD_LOOKING_L1 = {"Amended Booking Response", "Refund Related"}


def sanitize_pre_chat_state(l1: str, majority_status: str, majority_refund_status: str) -> tuple[str, str]:
    if l1 in BACKWARD_LOOKING_L1:
        return majority_status, majority_refund_status
    # Forward-looking: the booking must still be live going into the chat.
    status = "PENDING" if majority_status == "CANCELLED" else majority_status
    return status, "N/A"


# L1s whose scenario_text unconditionally asserts a temporal fact about the booking --
# real per-leaf-mood majority data can disagree with that assertion for a fallback slice
# with no grounded examples (as happened for "Unsatisfactory Tour Experience": the
# narrative says "already went on the tour" but the only real signal available was a
# single unrelated field, and the majority vote came out PENDING). Force bookingStatus to
# agree with what the text itself claims, for every row of that L1, rather than let a
# thin data slice silently contradict the narrative every LLM guest is given.
L1_ASSERTED_STATUS = {
    "Service Issues": "COMPLETED",  # narrative: "already went on the tour"
    "Payment Failure": "PENDING",  # narrative: "the payment didn't go through cleanly"
}


def main():
    with open(DATA_PATH) as f:
        groups = json.load(f)

    rows = []
    for i, g in enumerate(groups):
        l1, l2, l3, mood = g["l1"], g["l2"], g["l3"], g["mood"]
        node = NODE_BY_L1.get(l1, "general")
        if l2 == "Extended Validity":
            node = "extend"

        fields = g.get("fields", {})
        majority_status = fields.get("booking_status") or "COMPLETED"
        majority_sla = yn_to_bool(fields.get("is_sla_breached"))
        majority_same_day = yn_to_bool(fields.get("is_same_day_booking"))
        majority_validity = fields.get("validity_type") or "NOT_EXTENDABLE"
        majority_tour_inv = fields.get("tour_inventory_type") or "FIXED_START_FIXED_DURATION"
        majority_refund_status = fields.get("refund_status") or "N/A"
        majority_alt_status = fields.get("alternates_status") or "N/A"
        majority_status, majority_refund_status = sanitize_pre_chat_state(
            l1, majority_status, majority_refund_status
        )
        if l1 in L1_ASSERTED_STATUS:
            majority_status = L1_ASSERTED_STATUS[l1]

        majority_is_cancellable = yn_to_bool(fields.get("is_cancellable"))
        majority_is_reschedulable = yn_to_bool(fields.get("is_reschedulable"))

        # Deliberate, mood-driven ground truth for the node's core fact -- mirrors the
        # existing hardcoded frustration-arc pattern (denial -> guest gets upset). For
        # every OTHER fact (the guest isn't being tested on it, so it isn't mood-driven),
        # use the real per-leaf-mood majority vote as background truth instead of forcing
        # it to a blanket False -- grader.py checks these opportunistically whenever the
        # bot mentions them, even outside the scenario's own node, so an unconditional
        # False would silently fail the bot on a fact that was never actually false.
        approved = mood in ("happy", "okay")
        is_cancellable = approved if node == "cancel" else bool(majority_is_cancellable)
        is_reschedulable = approved if node == "modify" else bool(majority_is_reschedulable)
        has_extended_validity = approved if node == "extend" else (majority_validity != "NOT_EXTENDABLE")
        validity_type = "UNTIL_DATE" if has_extended_validity else "NOT_EXTENDABLE"

        tour = TOURS[i % len(TOURS)]
        name = f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {LAST_NAMES[(i * 7) % len(LAST_NAMES)]}"
        booking_id = 80100000 + i
        ticket_num = 40100000 + i
        guest_count = str((i % 5) + 1)

        row = {
            "bookingId": str(booking_id),
            "email": "shreyank.prabhu@headout.com",
            "booking_status": majority_status,
            "scenario_text": scenario_text_for(
                l1, l2, l3, mood,
                tour_name=tour["name"], guest_count=guest_count, node=node, approved=approved,
            ),
            "isCancellable": str(is_cancellable).upper(),
            "bookingStatus": majority_status,
            "isPendingCancellation": "FALSE",
            "isReschedulable": str(is_reschedulable).upper(),
            "oopCancellationAllowed": str(is_cancellable).upper(),
            "vendorId": str(1200 + i),
            "tourId": str(2400 + i),
            "tourGroupId": str(3600 + i),
            "inventoryDateTime": f"2026-09-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00Z",
            "refundReferenceNumber": "" if majority_refund_status == "N/A" else f"1234567{ticket_num}",
            "alternatesStatus": "" if majority_alt_status == "N/A" else majority_alt_status,
            # Real payload behavior: a link is only ever present once alternates were
            # actually sent to the guest -- "NOT_SENT_FROM_BMS"/"N/A" stay blank, "SENT"
            # gets a (redacted-style, matching the original sheet's convention) link.
            "alternatesLink": f"[link removed]{ticket_num}" if majority_alt_status == "SENT" else "",
            "isMoreInfoRequested": "FALSE",
            "isSLABreached": str(bool(majority_sla)).upper() if majority_sla is not None else "FALSE",
            "isSameDayBooking": str(bool(majority_same_day)).upper() if majority_same_day is not None else "FALSE",
            "resolutionTime": (
                RESOLUTION_TIMES_BREACHED[i % len(RESOLUTION_TIMES_BREACHED)]
                if majority_sla
                else RESOLUTION_TIMES_OK[i % len(RESOLUTION_TIMES_OK)]
            ),
            "isTextEtaBeforeDateOfExp": "FALSE",
            "ticketValidityType": validity_type,
            "ticketId": str(500100 + i),
            "vendorRefId": f"VREF-{1100 + i}",
            "tourGroupName": tour["name"],
            "primaryCustomerName": name,
            "guestCount": guest_count,
            "secureBookingId": f"SEC-{ticket_num}",
            "fulfillmentType": "VENDOR_API",
            "statusCode": "",
            "variantName": tour["variant"],
            "latitude": str(tour["lat"]),
            "longitude": str(tour["lon"]),
            "address": tour["address"],
            "redemptionInstruction": tour["redemption"],
            "tourInventoryType": majority_tour_inv,
            "itineraryId": str(7100000 + i),
            "secureItineraryId": f"SECITIN-{7100000 + i}",
            "machineCodeType": tour["machine"],
            "ticketValidityUntilDate": "2027-03-01" if has_extended_validity else "",
            "ticketValidityUntilDaysFromPurchase": "30" if has_extended_validity else "",
            "L1": l1,
            "L2": l2,
            "L3": l3,
            "mood": mood,
        }
        rows.append(row)

    # "Fradulent" (sic, matches the existing sheet's spelling) has zero examples in this
    # quarter's real data -- still gets all 4 mood variants (per the same
    # every-leaf-gets-every-mood policy as the rest of the sheet) since a fraud report
    # can plausibly land anywhere from anxious-but-calm to furious; there's just no real
    # transcript to ground the mock-API fields in, so they're a reasonable placeholder.
    FRAUD_MOOD_TEXT = {
        "happy": (
            "Mood: relieved-once-heard. They're worried but trust the process — once the agent "
            "acknowledges the report and explains next steps, they relax and thank the agent."
        ),
        "okay": (
            "Mood: anxious but composed. They want this treated urgently and ask pointed questions "
            "to make sure the agent is actually taking real action, not just noting down a complaint, "
            "but stay measured throughout."
        ),
        "frustrated": (
            "Mood: frustrated. They're rattled by the situation and it shows — if the agent's first "
            "response feels generic or slow, they push back once, more sharply, before accepting a "
            "concrete next step."
        ),
        "angry": (
            "Mood: angry and alarmed. They believe they're the victim of fraud and demand immediate "
            "action; if the agent doesn't clearly commit to investigating and reversing the charge "
            "right away, they push back firmly at least twice and ask for a supervisor."
        ),
    }
    for mood in ALL_MOODS:
        i = len(rows)
        tour = TOURS[i % len(TOURS)]
        name = f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {LAST_NAMES[(i * 7) % len(LAST_NAMES)]}"
        booking_id = 80100000 + i
        ticket_num = 40100000 + i
        rows.append({
            "bookingId": str(booking_id), "email": "shreyank.prabhu@headout.com",
            "booking_status": "PENDING",
            "scenario_text": (
                f"The guest has a booking for {tour['name']} that shows up on their account, but they say "
                "they never made it and believes their account or payment details were used without their "
                "knowledge. They open the chat reporting the suspicious booking and asking for it to be "
                "investigated and reversed immediately, including a refund of any charge. "
                + FRAUD_MOOD_TEXT[mood]
            ),
            "isCancellable": "TRUE", "bookingStatus": "PENDING", "isPendingCancellation": "FALSE",
            "isReschedulable": "FALSE", "oopCancellationAllowed": "TRUE", "vendorId": str(1200 + i),
            "tourId": str(2400 + i), "tourGroupId": str(3600 + i),
            "inventoryDateTime": f"2026-09-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00Z",
            "refundReferenceNumber": "", "alternatesStatus": "", "alternatesLink": "",
            "isMoreInfoRequested": "TRUE", "isSLABreached": "FALSE", "isSameDayBooking": "FALSE",
            "resolutionTime": RESOLUTION_TIMES_OK[i % len(RESOLUTION_TIMES_OK)],
            "isTextEtaBeforeDateOfExp": "FALSE",
            "ticketValidityType": "NOT_EXTENDABLE", "ticketId": str(500100 + i),
            "vendorRefId": f"VREF-{1100 + i}", "tourGroupName": tour["name"],
            "primaryCustomerName": name, "guestCount": "1", "secureBookingId": f"SEC-{ticket_num}",
            "fulfillmentType": "MANUAL", "statusCode": "", "variantName": tour["variant"],
            "latitude": str(tour["lat"]), "longitude": str(tour["lon"]), "address": tour["address"],
            "redemptionInstruction": tour["redemption"], "tourInventoryType": "FLEXIBLE_START_FLEXIBLE_DURATION",
            "itineraryId": str(7100000 + i), "secureItineraryId": f"SECITIN-{7100000 + i}",
            "machineCodeType": tour["machine"], "ticketValidityUntilDate": "",
            "ticketValidityUntilDaysFromPurchase": "", "L1": "Fradulent", "L2": "", "L3": "",
            "mood": mood,
        })

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"Wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
