# QA ground-truth sheet rebuild

`bookings_rebuilt.csv` is the rebuilt `bookings` tab for the QA ground-truth
Google Sheet (`1hGrZbcsOjNHNnidsZxp-QdVTTQcSHtOHqz6_HJMyqgA`), built from the
Q3 2026 real support-chat export (`Q32026_transcript_metadata.csv`, 4,549 rows).

It replaces the old 54-row sheet (one boilerplate row per L1/L2/L3 leaf, no
guest-mood variation) with **308 rows** — every one of the 77 real L1/L2/L3
leaves (including the "Fradulent" placeholder) **times all 4 moods**
(`happy` / `okay` / `frustrated` / `angry`), covering:

- All 11 real L1 categories seen in production (the old sheet only had 6 —
  Ticket Redemption Details, Refund Related, Payment Failure, Reserve Now Pay
  Later, Vendor Query, and Service Issues were missing entirely).
- **Full mood coverage, not just what the sample happened to show.** Moods
  are a guest-behavior axis independent of category — any leaf can plausibly
  happen angry even if this quarter's sample only caught it calm — so every
  leaf gets all 4 mood rows. 200/308 rows are **grounded**: their mock-API
  fields come from the real per-(leaf, mood) majority vote in the transcript
  data. The other 104 are **fallback**: that specific (leaf, mood) slice had
  zero real examples this quarter, so the fields fall back to the leaf's
  overall (mood-blind) majority vote instead of being fabricated from
  nothing. `leaf_mood_data.json` carries a `grounded`/`n` field per group if
  you want to see which is which.
- Mock-API fields (`isCancellable`, `isReschedulable`, `bookingStatus`,
  `isSLABreached`, etc.) seeded from those majority values, with the
  cancel/reschedule/extend fact deliberately set by mood (denied <->
  frustrated/angry, approved <-> okay/happy) to keep both TRUE/FALSE coverage
  for grading — verified with a full pass that all 308 rows' scenario_text
  claims agree with their boolean ground truth (zero mismatches).
- Hand-templated, mood-calibrated `scenario_text` per row (not the old
  "You are an automated tester..." boilerplate, which `user_engine.py`'s own
  `_is_meta_instruction` filter was silently stripping for the scripted
  engine) — each one now spells out the concrete booking, a specific
  situational ask, whether the thing being asked for is actually true, and a
  turn-by-turn mood brief (avg. ~600 characters, up from ~150).
- Cross-checked against a real production Zendesk booking-payload sample:
  `bookingStatus` now uses the real `CANCELLED` (double-L) spelling instead
  of the old sheet's `CANCELED`; `alternatesLink` is populated whenever
  `alternatesStatus` is actually `SENT` (previously blank in every row,
  including the ones claiming alternates were sent); `resolutionTime` now
  varies (shorter when `isSLABreached` is false, longer when true) instead of
  being hardcoded to "30 minutes" everywhere. `statusCode` stays blank —
  that matches the original sheet's own convention and isn't a field the
  code reads.
- Fixed a pre-chat-state consistency bug: for any L1 where the guest is asking
  about something that HASN'T happened yet (cancel/modify/redeem/etc. — every
  L1 except "Amended Booking Response" and "Refund Related", which are
  inherently about a booking whose cancellation/refund already happened and
  the guest is following up), `bookingStatus` can no longer come out
  `CANCELLED` and `refundReferenceNumber` can no longer be pre-populated —
  neither can be a fact of the booking before a conversation that is itself
  deciding whether a cancellation/refund happens. Also moved "Amended Booking
  Response" off the `cancel` grading node (it was being asked "is this
  cancellable" when the real question is a refund follow-up, which
  `grader.py` doesn't have a dedicated fact for) onto `general`/`ticket` as
  appropriate per leaf.
- Fixed a real bug this surfaced along the way: `scenarios.py` derives which
  fact gets graded by scanning `scenario_text` for keywords, and a couple of
  leaf labels ("Flight/train Cancellation" under Modification Request, "Tour
  Cancelled By SP" under Service Issues) contained "cancel" as an incidental
  word, silently misdirecting grading onto the wrong fact. Fixed with
  targeted detail-text overrides for those two leaves; verified every L1 now
  resolves to one single, correct node.

## How to apply it

This session has no tool that can write cell values into an existing Google
Sheet — the available Drive tools can only "create a new file" or "edit a
file's title/folder", nothing that touches cell contents. Writing to Sheets
in place requires OAuth2 or a service-account credential; the repo's own
`google_sheets_api_key` setting is a plain API key, which Google's Sheets API
only allows for *reading* a publicly-viewable sheet, not writing. So the
update to `1hGrZbcsOjNHNnidsZxp-QdVTTQcSHtOHqz6_HJMyqgA` has to be applied by
hand, in place, rather than done here automatically.

To update the **existing** `bookings` tab (gid=0) without spinning up another
separate spreadsheet (which is what happens if you import as "new
spreadsheet" or "insert new sheet"):

1. Open the sheet: https://docs.google.com/spreadsheets/d/1hGrZbcsOjNHNnidsZxp-QdVTTQcSHtOHqz6_HJMyqgA/edit?gid=0
2. Make sure the `bookings` tab (gid=0) is the active tab.
3. File > Import > Upload `bookings_rebuilt.csv`.
4. On the import dialog, set **Import location: "Replace current sheet"**
   (not "Insert new sheet(s)" and not "Create new spreadsheet") — this
   overwrites the active tab's contents in place, keeping the same tab/gid
   and URL, and leaves the `scenarios` tab untouched.
5. Confirm the header row now ends in `L1, L2, L3, mood` (45 columns) and
   there are 308 data rows.
6. Re-run `headout-qa run --dry-run` to confirm the new rows parse.

## Regenerating

`extract_leaf_mood_data.py` parses the raw transcript CSV into per-(leaf,
mood) mock-API field majorities (`leaf_mood_data.json`), and
`build_bookings_sheet.py` turns that into the final `bookings_rebuilt.csv`.
Both scripts hardcode the source CSV path from the session that produced this
data (`/root/.claude/uploads/.../Q32026_transcript_metadata.csv`) — update
`CSV_PATH`/`DATA_PATH` at the top of each script to point at a fresh export
before re-running.
