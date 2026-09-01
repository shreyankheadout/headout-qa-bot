"""Extract per-(leaf, mood) mock-API ground truth from the real transcript CSV.

v2: full cross-product coverage. Every one of the 76 real L1/L2/L3 leaves gets all
4 moods (happy/okay/frustrated/angry), not just the moods that cleared a significance
threshold in the sample -- moods are a guest-behavior axis independent of category, so
a leaf with only "okay" examples this quarter can still plausibly happen angry.

For a (leaf, mood) pair that HAS real transcripts tagged with that sentiment, the
mock-API fields are the real per-slice majority vote (grounded=True, n=actual count).
For a (leaf, mood) pair with zero real examples this quarter, the mock-API fields fall
back to that leaf's overall (mood-blind) majority vote, since we have no slice-specific
signal to draw on (grounded=False, n=0) -- this is flagged explicitly in the output so
downstream consumers (and the generated sheet's own audit) know which rows are
data-grounded vs extrapolated, rather than silently presenting both the same way.

Output: leaf_mood_data.json - list of {l1, l2, l3, mood, n, grounded, fields: {...}, sample_openers: [...]}
"""
from __future__ import annotations

import csv
import json
import re
import collections

CSV_PATH = "/root/.claude/uploads/4912fdb0-3cbd-5d0b-943d-608d5f687798/111a4c04-Q32026_transcript_metadata.csv"
OUT_PATH = "/tmp/claude-0/-home-user-headout-qa-bot/4912fdb0-3cbd-5d0b-943d-608d5f687798/scratchpad/leaf_mood_data.json"

ALL_MOODS = ("happy", "okay", "frustrated", "angry")

SENTIMENT_TO_MOOD = {
    "SENTIMENT__NEUTRAL": "okay",
    "SENTIMENT__POSITIVE": "happy",
    "SENTIMENT__VERY_POSITIVE": "happy",
    "SENTIMENT__NEGATIVE": "frustrated",
    "SENTIMENT__VERY_NEGATIVE": "angry",
}

BOOKING_FIELD_PATTERNS = {
    "is_cancellable": r"Is Cancellable:\s*([A-Za-z/ ]+?)(?:\n|$)",
    "is_reschedulable": r"Is Reschedulable:\s*([A-Za-z/ ]+?)(?:\n|$)",
    "booking_status": r"Booking Status:\s*([A-Za-z_/ ]+?)(?:\n|$)",
    "is_sla_breached": r"Is SLA Breached:\s*([A-Za-z/ ]+?)(?:\n|$)",
    "is_same_day_booking": r"Is Same Day Booking:\s*([A-Za-z/ ]+?)(?:\n|$)",
    "is_past_booking": r"Is Past Booking:\s*([A-Za-z/ ]+?)(?:\n|$)",
    "tour_inventory_type": r"Tour Inventory Type:\s*([A-Za-z_/ ]+?)(?:\n|$)",
    "refund_status": r"Refund Status:\s*([A-Za-z0-9_/ ]+?)(?:\n|$)",
    "alternates_status": r"Alternates Status:\s*([A-Za-z0-9_/ ]+?)(?:\n|$)",
    "validity_type": r"Validity Type:\s*([A-Za-z_/ ]+?)(?:\n|$)",
}


def parse_booking_details(transcript: str) -> dict:
    out = {}
    for field, pattern in BOOKING_FIELD_PATTERNS.items():
        m = re.search(pattern, transcript)
        if m:
            out[field] = m.group(1).strip()
    return out


def first_customer_line(transcript: str) -> str | None:
    m = re.search(r"\(\d{2}:\d{2}:\d{2}\)\s*Web User [^:]+:\s*(.+)", transcript)
    if m:
        return m.group(1).strip()[:220]
    return None


def leaf_key(l1, l2, l3):
    return (l1.strip(), l2.strip(), l3.strip())


def majority_fields(rows: list[dict]) -> dict:
    field_votes = collections.defaultdict(collections.Counter)
    for r in rows:
        for k, v in parse_booking_details(r["transcript"]).items():
            field_votes[k][v] += 1
    return {k: c.most_common(1)[0][0] for k, c in field_votes.items()}


def sample_openers(rows: list[dict], limit=3) -> list[str]:
    out = []
    for r in rows:
        line = first_customer_line(r["transcript"])
        if line:
            out.append(line)
        if len(out) >= limit:
            break
    return out


def main():
    per_row = []
    with open(CSV_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            l1, l2, l3 = row["L1"].strip(), row["L2"].strip(), row["L3"].strip()
            tags = row["Support Queries Tags"]
            sentiment_tag = None
            for t in tags.split(","):
                t = t.strip()
                if t.startswith("SENTIMENT__"):
                    sentiment_tag = t
            per_row.append(
                {
                    "leaf": leaf_key(l1, l2, l3),
                    "mood": SENTIMENT_TO_MOOD.get(sentiment_tag),
                    "transcript": row["Transcripts"],
                }
            )

    rows_by_leaf = collections.defaultdict(list)
    rows_by_leaf_mood = collections.defaultdict(list)
    for r in per_row:
        rows_by_leaf[r["leaf"]].append(r)
        if r["mood"]:
            rows_by_leaf_mood[(r["leaf"], r["mood"])].append(r)

    output = []
    for leaf in sorted(rows_by_leaf):
        leaf_rows = rows_by_leaf[leaf]
        leaf_fallback_fields = majority_fields(leaf_rows)
        leaf_fallback_openers = sample_openers(leaf_rows)
        for mood in ALL_MOODS:
            slice_rows = rows_by_leaf_mood.get((leaf, mood), [])
            l1, l2, l3 = leaf
            if slice_rows:
                output.append(
                    {
                        "l1": l1, "l2": l2, "l3": l3, "mood": mood,
                        "n": len(slice_rows), "grounded": True,
                        "fields": majority_fields(slice_rows),
                        "sample_openers": sample_openers(slice_rows),
                    }
                )
            else:
                # No real example of this leaf at this mood this quarter -- fall back to
                # the leaf's overall (mood-blind) majority fields rather than fabricate a
                # slice-specific vote from nothing. Marked grounded=False so it's traceable.
                output.append(
                    {
                        "l1": l1, "l2": l2, "l3": l3, "mood": mood,
                        "n": 0, "grounded": False,
                        "fields": dict(leaf_fallback_fields),
                        "sample_openers": list(leaf_fallback_openers),
                    }
                )

    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=1)

    grounded = sum(1 for g in output if g["grounded"])
    print(f"Total (leaf, mood) rows: {len(output)} ({grounded} grounded in real data, {len(output) - grounded} fallback)")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
