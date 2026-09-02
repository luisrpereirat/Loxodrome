# PassLog v0.1 — Spec Review

Review of the PassLog technical specification. Findings are ordered by how much
they change the design, not by how much text they take to fix.

The spec is sound in its overall shape: barcode-over-OCR, a framework-free
parsing core, a mandatory human review step, and an M0 spike gating the primary
capture path are all the right calls. Everything below is a correction or a gap,
not a disagreement with the approach.

---

## Blocking — these change the design

### B1. The year-resolution anchor does not exist for the actual use case

§4.4 step 1 rests on "Screenshots are taken at or near flight time." For the
driving scenario — a user reconstructing ten years of history *today* by
scrolling Wallet's expired passes — every capture, screenshot and recording
alike, carries today's date. The spec acknowledges this for recordings and
then keeps the anchor for screenshots; in practice both are captured in the
same sitting.

Worse, the selection rule as written degrades silently. "Closest to and not
after the anchor" with an anchor of today means *every* candidate year passes
the filter and the most recent one always wins. A 2017 flight resolves to 2025.
That is precisely the "plausible-looking, wrong document submitted to an
immigration authority" failure the section opens by warning against.

Also note the rule is internally contradictory: "closest to and not after the
anchor" and "within a ±3-day tolerance" are different tests, and the ±3-day
window is meaningless once the anchor is months or years away from the flight.

**Fix:** demote the capture date to what it actually is — an *upper bound*
(the flight cannot be after the capture) — and never a positive year signal.
Then add B2 as the real source.

### B2. BCBP already carries the year, and the spec doesn't use it

The conditional (variable-length) section of a BCBP payload contains an item
for the boarding-pass issue date, encoded as four characters where the **first
character is the last digit of the year** and the remaining three are the
Julian day of issue. Since issue date is within roughly a year of the flight,
one digit plus a ten-year window pins the year uniquely in nearly every case.

This turns §4.4 from a heuristic into a lookup for any pass whose airline
populates the field, and it works identically on screenshots, recordings, and
`.pkpass`. It should be step 1 of the resolution strategy, ahead of the
capture-date anchor and well ahead of OCR.

Verify the exact item number and offset against the Resolution 792
implementation guide before implementing, and treat the field as optional —
population varies by airline. But the current spec parses the conditional
section only as a length to be skipped, which discards the single most valuable
piece of data in the payload for this app's central risk.

### B3. Conditional data must be consumed between legs, or multi-leg parsing desynchronises

§4.3 says the block "from *Operating carrier PNR* through *conditional items
size* repeats once per leg." That is true, but incomplete in a way that breaks
exactly the multi-leg case the section is written to protect.

The two-hex-digit size field declares a variable-length section that sits
*immediately after* that leg's mandatory block and *before* the next leg's.
A parser that loops the 37-byte mandatory block without consuming those N bytes
reads leg 2 out of the middle of leg 1's conditional data. It will not fail
loudly — it will produce garbage IATA codes and get rejected by validation, so
the symptom is "multi-leg passes mysteriously yield one leg," which looks like
the truncation bug the spec is trying to avoid.

**Fix:** the loop is: read 37 mandatory bytes → read 2 hex size digits → skip
that many bytes → repeat. State this explicitly in the spec, and add a fixture
(see F1) with non-trivial conditional data on leg 1.

### B4. PHPicker and `PHAsset.creationDate` are mutually exclusive

§4.1 requires `PHAsset.creationDate` on every frame. §10 recommends
`PHPickerViewController` precisely because it needs no permission prompt.
Those cannot both hold: the no-prompt picker hands back an `NSItemProvider`,
not a `PHAsset`, and asset identifiers require configuring the picker against a
photo library, which reintroduces the authorisation prompt.

Three ways out, in order of preference:

1. Drop the dependency. Given B1 the capture date is only an upper bound
   anyway, and B2 supplies the real year — so read the date out of the file's
   own metadata (EXIF `DateTimeOriginal` for stills, the QuickTime creation
   date for video) and keep the no-prompt picker.
2. Keep `PHAsset` and accept the read-authorisation prompt.
3. Keep the picker and treat every capture as unanchored.

The spec should pick one; today it asserts all three in different sections.

### B5. `Source` and `BoardingPassLeg` break the framework-free core

`BoardingPassLeg.swift` lives in BCBPKit, which §3 defines as "Pure Swift. No
UIKit, no Vision," testable with no device. But `Source` references
`PHAsset.LocalIdentifier` (Photos) and `CMTime` (CoreMedia), so BCBPKit imports
two Apple media frameworks and the stated testability guarantee is gone.

`PHAsset.LocalIdentifier` also does not exist as a type — `localIdentifier` is
a plain `String` property.

**Fix:** make the model carry primitives — `case recording(assetID: String,
frameSeconds: Double)` — and convert at the PassImport boundary. Costs nothing
and keeps M1 buildable on any machine, which is the point of the split.

---

## Significant — worth fixing before M1

### S1. `Confidence` conflates two independent axes

`enum Confidence { barcode, ocrHigh, ocrLow, yearUncertain, manual }` mixes
*how the fields were read* with *how well the date resolved*. A record can be
perfectly barcode-decoded and still have an unknown year — the single most
common state in this app — and there is no way to express it.

This is not cosmetic: §6 sorts the review table by confidence, and the sort is
the main UX mechanism for directing the user's attention. With one field, a
barcode row with an uncertain year either hides at the bottom or falsely
reports its fields as unreliable.

**Fix:** two fields. `fieldConfidence: .barcode | .ocrHigh | .ocrLow | .manual`
and `dateConfidence: .exact | .inferred | .uncertain | .manual`. Sort on the
worse of the two.

### S2. Deduplication key is not unique for multi-leg passes

§4.5 makes the raw payload the primary key. A two-leg pass produces two
`BoardingPassLeg` records with byte-identical payloads, so the deduplicator
collapses a connecting itinerary to a single leg — reintroducing the truncation
bug from the other direction.

**Fix:** key on `(rawPayload, legIndex)`. Add `legIndex: Int` to the model.

Secondary issue: the OCR secondary key `(carrier, flightNumber, resolvedDate,
origin, destination)` includes `resolvedDate`, which is `nil` until §4.4
succeeds and may stay `nil` permanently. Two OCR records for the same flight
with unresolved years won't match. Key on `julianDay` instead, and merge year
evidence afterwards.

### S3. Downscaling to 1600 px may destroy PDF417 decode

§4.1 downscales the long edge to 1600 px. A modern iPhone screen recording is
~2500–2800 px on the long edge, so this is roughly a 0.6× reduction — and
PDF417 as rendered in Wallet is already narrow, with module widths that may be
1–2 px after scaling. Aztec tolerates this far better than PDF417 does.

Compounding it: screen recordings are H.264/HEVC, and lossy compression is
unkind to exactly the high-frequency patterns barcodes are made of.

**Fix:** run barcode detection at native resolution and OCR at 1600 px. Barcode
detection is the cheaper of the two requests, and it's the authoritative one —
spending compute there is the right trade. Make the resolution threshold a
tunable constant and measure it during M0 rather than fixing it in the spec.

### S4. M0's exit criterion tests the wrong thing

"A screen recording of Wallet visibly contains decodable barcodes" — *visibly*
and *decodable* are not the same claim, and the gap between them is exactly
what S3 describes. A barcode that a human can see in a video frame may still
fail `DetectBarcodesRequest` after compression and scaling.

**Fix:** the exit criterion should be a number, not an impression: run barcode
detection over frames sampled from a real recording and require ≥1 successful
payload per pass across ≥90% of passes captured. This is a twenty-line
command-line tool and it also produces the first real fixture set for M1.

Extend the spike to answer two more questions while you're there: do Wallet's
*expired* passes still render their barcodes at all (several airlines blank
them), and does the pass barcode render in the scrolling list view or only when
a pass is opened? If it's the latter, the "sloppy 30-second scroll" premise of
§2.1 doesn't hold and the recording becomes one-tap-per-pass — still better
than screenshots, but a different UX.

### S5. Vision may return no string for binary Aztec payloads

Some carriers encode BCBP as raw bytes in Aztec rather than as an ASCII string.
`payloadStringValue` is `nil` for those; the payload is only reachable through
the raw `payloadData`. A pipeline that reads only the string value will drop
these passes with no error.

**Fix:** fall back to `payloadData` decoded as ASCII/ISO-8859-1 when the string
value is absent.

---

## Minor

- **`Package.swift` alone can't produce an iOS app.** SwiftPM has no iOS
  application product; `PassLogApp` needs an Xcode app target that depends on
  the package, and that's also where `Info.plist` and the
  `CFBundleDocumentTypes` entry for `com.apple.pkpass` (§4.6) must live.
  Keep the package for BCBPKit and PassImport, which is where the value is.
- **Sideload expiry.** "Personal sideload" on a free Apple ID means a 7-day
  provisioning profile and reinstallation every week. Worth stating in the spec
  so it isn't discovered at M6.
- **Airport DB filter is too aggressive.** OurAirports filtered to
  large/medium drops regional and island airports that appear in real
  itineraries. Filter on "has a non-empty IATA code" instead — roughly 9,000
  rows, still well under a megabyte, and no unresolvable codes in the export.
- **`trips.csv` has a single `country` column** but a trip can visit several
  countries; and `duration_days` needs an explicit inclusive/exclusive
  definition, since visa forms usually count both travel days. Define both, or
  emit one row per country visited within a trip.
- **§4.3 validation should also bound the flight number** (4 digits plus an
  optional alpha suffix) and reject an all-space PNR, both of which are common
  OCR-corruption signatures.
- **Airline conditional data is frequently mis-sized in the wild.** When the
  declared conditional length runs past the end of the payload, don't discard
  the whole record — the mandatory block for leg 1 has already parsed cleanly.
  Emit what was validated and flag the record.

---

## Fixture additions

The appendix list is good. Add these:

- **F1. Two-leg pass with non-empty conditional data on leg 1** — the direct
  regression test for B3. The existing "conditional items present" fixture is
  single-leg and will not catch the desync.
- **F2. Pass carrying an issue-date conditional item** — the B2 path, including
  a case where issue year and flight year differ (a December booking for a
  January flight).
- **F3. Payload whose declared conditional length exceeds the payload** —
  the malformed-length case above.
- **F4. Binary Aztec payload as raw bytes** — S5.
- **F5. Leg count byte of `0` and of `9`** — both outside the valid 1...4 range
  and both must be rejected before any field is read.

---

## Summary

Fix B1–B5 before M1 begins; they touch the data model, the module boundary, and
the year-resolution strategy, and all five get more expensive after code exists.
S1–S5 are cheap now and awkward later. Everything under Minor can be handled
during the milestone it belongs to.

The one change that most improves the finished product is B2: reading the year
out of the barcode's own conditional section converts the app's largest
correctness risk from an inference problem into a parsing problem, and parsing
problems have tests.
