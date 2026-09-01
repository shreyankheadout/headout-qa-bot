# QA ground-truth sheet rebuild

`bookings_rebuilt.csv` is the rebuilt `bookings` tab for the QA ground-truth
Google Sheet (`1hGrZbcsOjNHNnidsZxp-QdVTTQcSHtOHqz6_HJMyqgA`), built from the
Q3 2026 real support-chat export (`Q32026_transcript_metadata.csv`, 4,549 rows).

It replaces the old 54-row sheet (one boilerplate row per L1/L2/L3 leaf, no
guest-mood variation) with 161 rows covering:

- All 11 real L1 categories seen in production (the old sheet only had 6 —
  Ticket Redemption Details, Refund Related, Payment Failure, Reserve Now Pay
  Later, Vendor Query, and Service Issues were missing entirely).
- A new `mood` column (`happy` / `okay` / `frustrated` / `angry`), one row per
  (L1/L2/L3 leaf, significant mood tier) combination actually observed in the
  real sentiment data (tier counted as "significant" if it has >=2 occurrences
  or >=8% share within that leaf — avoids manufacturing e.g. an "angry" row
  off a single outlier chat).
- Mock-API fields (`isCancellable`, `isReschedulable`, `bookingStatus`,
  `isSLABreached`, etc.) seeded from the majority values found in the real
  transcripts' embedded "Booking Details" block for that leaf, with the
  cancel/reschedule/extend fact deliberately set by mood (denied <->
  frustrated/angry, approved <-> okay/happy) to keep both TRUE/FALSE coverage
  for grading.
- Hand-templated, mood-calibrated `scenario_text` per row (not the old
  "You are an automated tester..." boilerplate, which `user_engine.py`'s own
  `_is_meta_instruction` filter was silently stripping for the scripted
  engine).
- The pre-existing "Fradulent" row (kept, unchanged spelling) is carried
  forward as a single placeholder since this quarter's real data has zero
  examples of it — not fabricated.

## How to apply it

This repo has no write access to the live Google Sheet. To use the rebuilt
data:

1. Open the `bookings` tab of the ground-truth sheet.
2. Replace its contents with `bookings_rebuilt.csv` (import > replace current
   sheet, or paste over the existing range) — the column order matches the
   existing sheet exactly, with `mood` appended as a new final column.
3. Re-run `headout-qa run --dry-run` to confirm the new rows parse.

## Regenerating

`extract_leaf_mood_data.py` parses the raw transcript CSV into per-(leaf,
mood) mock-API field majorities (`leaf_mood_data.json`), and
`build_bookings_sheet.py` turns that into the final `bookings_rebuilt.csv`.
Both scripts hardcode the source CSV path from the session that produced this
data (`/root/.claude/uploads/.../Q32026_transcript_metadata.csv`) — update
`CSV_PATH`/`DATA_PATH` at the top of each script to point at a fresh export
before re-running.
