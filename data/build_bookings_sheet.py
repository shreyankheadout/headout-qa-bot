"""Build the rebuilt `bookings` tab CSV for the QA ground-truth sheet.

Reads leaf_mood_data.json (160 significant (L1,L2,L3,mood) groups extracted from the
real Q3 2026 transcript data) and emits one scenario row per group, with:
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
    "Amended Booking Response": "cancel",
    "Refund Related": "general",
    "Payment Failure": "general",
    "Reserve Now Pay Later": "general",
    "Vendor Query": "general",
    "Service Issues": "general",
    "General Information": "general",
    "Fraudulent": "general",
}

L1_SITUATION = {
    "Cancellation Request": "wants to cancel their booking",
    "Modification Request": "wants to modify details on their booking",
    "Delay Fulfilment": "still hasn't received their tickets and wants an update",
    "Ticket Redemption Details": "has a question about how to redeem their ticket",
    "Amended Booking Response": "is following up on a booking that was already amended",
    "Refund Related": "is asking about a refund",
    "Payment Failure": "ran into a problem paying for their booking",
    "Reserve Now Pay Later": "has a question about their Reserve Now, Pay Later booking",
    "Vendor Query": "is a supplier contact asking about a guest's booking",
    "Service Issues": "is unhappy with how the experience itself went",
    "General Information": "has a general question before or after booking",
    "Fraudulent": "is reporting suspicious or fraudulent activity on their account",
}

MOOD_BEHAVIOR = {
    "happy": "The guest is relaxed and upbeat, quick to accept whatever the agent says and thanks them warmly once it's sorted.",
    "okay": "The guest is calm and matter-of-fact, asks their question plainly and accepts a clear answer without much back-and-forth.",
    "frustrated": "The guest is annoyed about the situation, pushes back once with a bit of edge if the first answer doesn't satisfy them, but stays civil.",
    "angry": "The guest is upset and impatient from the first message, pushes back firmly at least twice, and threatens to ask for a supervisor if the agent doesn't resolve it quickly.",
}


def humanize(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    # "Didn T Get Tickets" -> "didn't get tickets"; "Sp Related" -> "SP related"
    text = re.sub(r"\bT\b", "'t", text)
    words = [w.upper() if w.upper() == "SP" else w.lower() for w in text.split(" ")]
    return " ".join(words).replace(" 't ", "'t ")


def scenario_text_for(l1: str, l2: str, l3: str, mood: str) -> str:
    detail = humanize(l3) if l3 else (humanize(l2) if l2 else "")
    base = L1_SITUATION.get(l1, "has a question about their booking")
    if detail:
        parts = [f"The guest {base} — specifically, {detail}."]
    else:
        parts = [f"The guest {base}."]
    parts.append(MOOD_BEHAVIOR[mood])
    return " ".join(parts)


def yn_to_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.strip().lower() == "yes"


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

        # Deliberate, mood-driven ground truth for the node's core fact -- mirrors the
        # existing hardcoded frustration-arc pattern (denial -> guest gets upset).
        approved = mood in ("happy", "okay")
        is_cancellable = approved if node == "cancel" else False
        is_reschedulable = approved if node == "modify" else False
        has_extended_validity = approved if node == "extend" else (majority_validity != "NOT_EXTENDABLE")
        validity_type = "UNTIL_DATE" if has_extended_validity else "NOT_EXTENDABLE"

        tour = TOURS[i % len(TOURS)]
        name = f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {LAST_NAMES[(i * 7) % len(LAST_NAMES)]}"
        booking_id = 80100000 + i
        ticket_num = 40100000 + i

        row = {
            "bookingId": str(booking_id),
            "email": "shreyank.prabhu@headout.com",
            "booking_status": majority_status,
            "scenario_text": scenario_text_for(l1, l2, l3, mood),
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
            "alternatesLink": "",
            "isMoreInfoRequested": "FALSE",
            "isSLABreached": str(bool(majority_sla)).upper() if majority_sla is not None else "FALSE",
            "isSameDayBooking": str(bool(majority_same_day)).upper() if majority_same_day is not None else "FALSE",
            "resolutionTime": "30 minutes",
            "isTextEtaBeforeDateOfExp": "FALSE",
            "ticketValidityType": validity_type,
            "ticketId": str(500100 + i),
            "vendorRefId": f"VREF-{1100 + i}",
            "tourGroupName": tour["name"],
            "primaryCustomerName": name,
            "guestCount": str((i % 5) + 1),
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
    # quarter's real data -- keep the existing sheet's single row as a best-effort
    # placeholder rather than fabricate mood variants with no grounding (flagged to the
    # user as an open gap rather than guessed at).
    i = len(rows)
    tour = TOURS[i % len(TOURS)]
    name = f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {LAST_NAMES[(i * 7) % len(LAST_NAMES)]}"
    booking_id = 80100000 + i
    ticket_num = 40100000 + i
    rows.append({
        "bookingId": str(booking_id), "email": "shreyank.prabhu@headout.com",
        "booking_status": "PENDING",
        "scenario_text": (
            "The guest reports that they never made this booking and suspects their account or "
            "payment details were used fraudulently. The guest is anxious and wants the booking "
            "investigated and reversed as soon as possible."
        ),
        "isCancellable": "TRUE", "bookingStatus": "PENDING", "isPendingCancellation": "FALSE",
        "isReschedulable": "FALSE", "oopCancellationAllowed": "TRUE", "vendorId": str(1200 + i),
        "tourId": str(2400 + i), "tourGroupId": str(3600 + i),
        "inventoryDateTime": f"2026-09-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00Z",
        "refundReferenceNumber": "", "alternatesStatus": "", "alternatesLink": "",
        "isMoreInfoRequested": "TRUE", "isSLABreached": "FALSE", "isSameDayBooking": "FALSE",
        "resolutionTime": "2 hours", "isTextEtaBeforeDateOfExp": "FALSE",
        "ticketValidityType": "NOT_EXTENDABLE", "ticketId": str(500100 + i),
        "vendorRefId": f"VREF-{1100 + i}", "tourGroupName": tour["name"],
        "primaryCustomerName": name, "guestCount": "1", "secureBookingId": f"SEC-{ticket_num}",
        "fulfillmentType": "MANUAL", "statusCode": "", "variantName": tour["variant"],
        "latitude": str(tour["lat"]), "longitude": str(tour["lon"]), "address": tour["address"],
        "redemptionInstruction": tour["redemption"], "tourInventoryType": "FLEXIBLE_START_FLEXIBLE_DURATION",
        "itineraryId": str(7100000 + i), "secureItineraryId": f"SECITIN-{7100000 + i}",
        "machineCodeType": tour["machine"], "ticketValidityUntilDate": "",
        "ticketValidityUntilDaysFromPurchase": "", "L1": "Fradulent", "L2": "", "L3": "",
        "mood": "okay",
    })

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    print(f"Wrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    main()
