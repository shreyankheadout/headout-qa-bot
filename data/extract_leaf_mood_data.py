"""Extract per-(leaf, mood) mock-API ground truth from the real transcript CSV.

Reads Q32026_transcript_metadata.csv, buckets rows into significant (L1/L2/L3, mood)
groups using the same threshold used during planning (tier needs >=2 occurrences or
>=8% share within the leaf), then for each group parses the embedded "Booking Details"
block inside the Transcripts cell to get majority/representative mock-API field values,
plus a couple of paraphrased real opening messages for scenario_text inspiration.

Output: leaf_mood_data.json - list of {l1, l2, l3, mood, n, fields: {...}, sample_openers: [...]}
"""
from __future__ import annotations

import csv
import json
import re
import collections

CSV_PATH = "/root/.claude/uploads/4912fdb0-3cbd-5d0b-943d-608d5f687798/111a4c04-Q32026_transcript_metadata.csv"
OUT_PATH = "/tmp/claude-0/-home-user-headout-qa-bot/4912fdb0-3cbd-5d0b-943d-608d5f687798/scratchpad/leaf_mood_data.json"

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
    # Real transcripts look like: "(HH:MM:SS) Web User <id>: <text>"
    m = re.search(r"\(\d{2}:\d{2}:\d{2}\)\s*Web User [^:]+:\s*(.+)", transcript)
    if m:
        line = m.group(1).strip()
        return line[:220]
    return None


def leaf_key(l1, l2, l3):
    return (l1.strip(), l2.strip(), l3.strip())


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
            mood = SENTIMENT_TO_MOOD.get(sentiment_tag)
            per_row.append(
                {
                    "leaf": leaf_key(l1, l2, l3),
                    "mood": mood,
                    "transcript": row["Transcripts"],
                }
            )

    # bucket by leaf -> mood -> count, to find significant tiers (same rule as planning)
    leaf_mood_counts = collections.defaultdict(collections.Counter)
    leaf_totals = collections.Counter()
    for r in per_row:
        leaf_totals[r["leaf"]] += 1
        if r["mood"]:
            leaf_mood_counts[r["leaf"]][r["mood"]] += 1

    significant = {}
    for leaf, total in leaf_totals.items():
        tiers = set()
        for mood, cnt in leaf_mood_counts[leaf].items():
            if cnt >= 2 or (total and cnt / total >= 0.08):
                tiers.add(mood)
        if not tiers:
            tiers = {"okay"}
        significant[leaf] = tiers

    # group rows by (leaf, mood) for the significant tiers only
    groups = collections.defaultdict(list)
    for r in per_row:
        leaf = r["leaf"]
        mood = r["mood"]
        if mood and mood in significant[leaf]:
            groups[(leaf, mood)].append(r)
        elif mood is None and "okay" in significant[leaf] and not leaf_mood_counts[leaf]:
            # leaf had literally zero tagged sentiment rows anywhere; still fine to leave alone,
            # handled by fallback below.
            pass

    output = []
    for (leaf, mood), rows in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        l1, l2, l3 = leaf
        field_votes = collections.defaultdict(collections.Counter)
        openers = []
        for r in rows:
            details = parse_booking_details(r["transcript"])
            for k, v in details.items():
                field_votes[k][v] += 1
            if len(openers) < 3:
                line = first_customer_line(r["transcript"])
                if line:
                    openers.append(line)
        majority_fields = {k: c.most_common(1)[0][0] for k, c in field_votes.items()}
        output.append(
            {
                "l1": l1,
                "l2": l2,
                "l3": l3,
                "mood": mood,
                "n": len(rows),
                "fields": majority_fields,
                "sample_openers": openers,
            }
        )

    # also include leaves entirely absent from significant grouping due to zero tags
    # (defensive - shouldn't happen given fallback to 'okay' above, but double check coverage)
    covered_leaves = {(g["l1"], g["l2"], g["l3"]) for g in output}
    for leaf in leaf_totals:
        if leaf not in covered_leaves:
            l1, l2, l3 = leaf
            rows = [r for r in per_row if r["leaf"] == leaf]
            field_votes = collections.defaultdict(collections.Counter)
            openers = []
            for r in rows:
                details = parse_booking_details(r["transcript"])
                for k, v in details.items():
                    field_votes[k][v] += 1
                if len(openers) < 3:
                    line = first_customer_line(r["transcript"])
                    if line:
                        openers.append(line)
            majority_fields = {k: c.most_common(1)[0][0] for k, c in field_votes.items()}
            output.append(
                {
                    "l1": l1, "l2": l2, "l3": l3, "mood": "okay",
                    "n": len(rows), "fields": majority_fields, "sample_openers": openers,
                }
            )

    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=1)

    print(f"Total (leaf, mood) groups: {len(output)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
