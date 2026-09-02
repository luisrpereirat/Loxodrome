# PassLog v0.1 — Spec Review

Review of the PassLog technical specification, updated with measurements from
a real Wallet capture (2026-09-02).

The spec's shape is right: barcode-over-OCR, a framework-free parsing core, a
mandatory human review step, and an M0 spike gating the primary capture path.
Everything below is a correction or a gap, not a disagreement with the approach.

Findings are marked **CONFIRMED** where a real capture settled them,
**OPEN** where they remain reasoning from the spec alone.

---

## 0. Evidence: capture 1

A 27-second capture — the Expired list swept, then one United pass opened —
processed by `spike/m0_probe.py`. The capture came through iPhone Mirroring on
a Mac and so arrived at 416×888, roughly a third of the iPhone's native
resolution in each dimension. Results are therefore a **floor**, not a ceiling
(see §10).

| Observation | Consequence |
| :- | :- |
| Payload carried issue date `3273`; flight Julian day 274, issue day 273 | The year is in the barcode — B2 confirmed |
| Wallet renders expired barcodes at ~12% local contrast | New finding B6; 1/32 frames decode raw, 24/32 with CLAHE |
| Wallet list row read `Oct 4, 2023`; barcode read `2023-10-01` | List year is reliable, list day is not — S6 |
| **346 expired passes** in the library | Coverage is a corpus, not a handful — changes the workflow economics |
| List shows absolute dates with years back to 2015 | Tier‑1 capture is viable on old passes — B7 |
| Decoded pass was for a second passenger, not the account holder | Multi-passenger is real, not hypothetical — S7 |
| List contains transit cards, hotel, insurance, event tickets | Pass-type filtering is unspecified work — S8 |
| One ~3s dwell on a pass yielded ~25 frames | Frame-redundancy premise holds |

Two questions remain unanswered by this capture: whether downscaling to 1600 px
costs payloads (the capture was already below that), and whether multi-leg
conditional handling works on a real payload (this pass was single-leg).

---

## Blocking

### B1. The year-resolution anchor does not exist for the actual use case — **CONFIRMED, downgraded**

§4.4 step 1 rests on "Screenshots are taken at or near flight time." The real
scenario is a user reconstructing a decade of history today, so every capture
carries today's date. The capture confirms it: passes from 2015 through 2026
were all swept in one 27-second sitting.

The selection rule also degrades silently. "Closest to and not after the anchor"
with an anchor of today passes *every* candidate year and always picks the most
recent — a 2017 flight resolving to 2026. That is precisely the
"plausible-looking, wrong document submitted to an immigration authority"
failure the section opens by warning against. The rule is internally
contradictory too: "not after the anchor" and "within ±3 days" are different
tests, and the ±3-day window is meaningless once the anchor is years away.

**Fix:** demote the capture date to what it is — an upper bound, never a
positive year signal. B2 and B7 supply the actual year.

### B2. BCBP carries the year, and the spec doesn't use it — **CONFIRMED**

The conditional section carries a boarding-pass issue date: four characters
whose **first is the last digit of the year**, followed by the Julian day of
issue. One digit plus a ten-year window pins the year uniquely.

Measured on the capture: `3273`. The flight is day 274 and issue is day 273 —
the day before, exactly as check-in should be. That coherence is what confirms
the offset is right rather than coincidence. Year digit 3 → **2023-10-01**.

This turns §4.4 from a heuristic into a lookup, and works identically on
screenshots, recordings and `.pkpass`. Make it step 1 of the resolution
strategy, ahead of both the capture anchor and OCR.

Caveats: one pass, one airline. Population varies by carrier, so treat the item
as optional and keep B7 as the fallback. The spec currently parses the
conditional section only as a length to skip, discarding the single most
valuable field in the payload for its own central risk.

### B3. Conditional data must be consumed between legs — **OPEN (proven synthetically)**

§4.3 says the block "from *Operating carrier PNR* through *conditional items
size* repeats once per leg." True but incomplete, in a way that breaks exactly
the multi-leg case the section exists to protect.

The two-hex-digit size field declares a variable-length section sitting
*immediately after* that leg's mandatory block and *before* the next leg's. A
parser that loops the 37-byte block without consuming those N bytes reads leg 2
out of the middle of leg 1's conditional data.

Demonstrated against a synthetic two-leg fixture in `spike/m0_probe.py`. With
the skip: `JFK→LHR`, `LHR→MAD`. Without it, leg 2 reads `('523', '40B')` —
garbage that validation then rejects, so the symptom is "multi-leg passes
mysteriously yield one leg," which looks like the truncation bug rather than a
desync.

The real capture had 93 bytes of conditional data on a single-leg pass, so the
section is substantial in practice — but a real multi-leg payload has not yet
been tested. **Fix:** read 37 mandatory bytes → read 2 hex digits → skip that
many → repeat. Add fixture F1.

### B4. PHPicker and `PHAsset.creationDate` are mutually exclusive — **OPEN**

§4.1 requires `PHAsset.creationDate` on every frame. §10 recommends
`PHPickerViewController` precisely because it needs no permission prompt. Both
cannot hold: the no-prompt picker returns an `NSItemProvider`, not a `PHAsset`,
and asset identifiers require configuring the picker against a photo library,
which reintroduces the prompt.

Given B1 and B2, the cleanest resolution is to **drop the dependency**: the
capture date is only an upper bound anyway, and the year now comes from the
payload. Read what's needed from the file's own metadata and keep the no-prompt
picker. The spec should pick one path; today it asserts three in three sections.

### B5. `Source` and `BoardingPassLeg` break the framework-free core — **OPEN**

`BoardingPassLeg.swift` lives in BCBPKit, which §3 defines as "Pure Swift. No
UIKit, no Vision," testable with no device. But `Source` references
`PHAsset.LocalIdentifier` (Photos) and `CMTime` (CoreMedia), so BCBPKit pulls in
two media frameworks and the testability guarantee is gone.
`PHAsset.LocalIdentifier` is also not a real type — `localIdentifier` is a
`String`.

**Fix:** carry primitives — `case recording(assetID: String, frameSeconds:
Double)` — and convert at the PassImport boundary.

### B6. Wallet dims expired barcodes; decoding requires contrast recovery — **CONFIRMED, new**

§8 lists "Airlines void barcodes on expired passes" as a High risk. That is not
what happens, at least for United. The barcode is fully intact — **Wallet
renders it at roughly 12% of the contrast range**, a pale grey on white.

The distinction matters because the mitigation is completely different, and
much better: not "fall back to OCR" but "normalise contrast before decoding."

| Preprocessing | Frames decoded |
| :- | :- |
| raw | 1 / 32 |
| global contrast stretch | 1 / 32 |
| **CLAHE, tile-local** | **24 / 32** |

Global stretch is a no-op because the surrounding frame still spans the full
range; only the barcode tile is flat. Tile-local equalisation is what recovers
it. A 24× decode rate for a handful of lines.

**Fix:** add a contrast-equalisation stage to §4.2, applied when a first decode
pass returns nothing. Downgrade the §8 "voided barcodes" risk from High —
it is a rendering artifact with a cheap, verified mitigation.

### B7. The Wallet list view is an unexploited data source — **CONFIRMED, new**

The spec treats the pass face as the only visual source. But every row of
Wallet's Expired list carries a **full absolute date including the year**,
rendered by Wallet — verified on the capture back to 2015 — and, for airline
passes that title themselves as routes (`MAD ✈ LIS`, `IAH ✈ PHX`), the origin
and destination too.

This is the field BCBP does not encode, available without opening a single
pass. It pairs exactly with the barcode: **year from the list row, exact day
from the payload.**

It also restructures capture into two tiers, which matters enormously at 346
passes:

- **Tier 1 — one scroll of the Expired list.** Large, high-contrast system
  text; near-perfect OCR. Yields date and route for every route-titled pass in
  about a minute. This is the product.
- **Tier 2 — open individual passes.** Adds carrier, flight number, PNR, seat
  and the exact day. At roughly 3 seconds per pass, doing this for all 346 is
  20+ minutes of tapping and a video far too long to process comfortably.

Tier 2 must therefore be **opt-in per row**, driven by the review table, not a
precondition for using the app. Rewrite §2 around this split.

---

## Significant

### S1. `Confidence` conflates two independent axes — **OPEN**

`enum Confidence { barcode, ocrHigh, ocrLow, yearUncertain, manual }` mixes how
the fields were read with how well the date resolved. A record can be perfectly
barcode-decoded and still have an unknown year — now the *common* state, given
B7 supplies years for some rows and not others — and there is no way to say so.

Not cosmetic: §6 sorts the review table on this, and the sort is the main
mechanism for directing attention.

**Fix:** two fields. `fieldConfidence: .barcode | .ocrHigh | .ocrLow | .manual`
and `dateConfidence: .exact | .inferred | .uncertain | .manual`. Sort on the
worse of the two.

### S2. Deduplication key is not unique for multi-leg passes — **OPEN**

§4.5 makes the raw payload the primary key, but a two-leg pass yields two legs
with identical payloads — collapsing a connecting itinerary to one leg.
**Fix:** key on `(rawPayload, legIndex)`; add `legIndex` to the model.

The OCR secondary key includes `resolvedDate`, which is `nil` until §4.4
succeeds and may stay `nil`. Key on `julianDay` and merge year evidence after.

### S3. Downscaling to 1600 px may break PDF417 — **UNTESTED**

§4.1 downscales the long edge to 1600 px. A native iPhone recording is ~2600 px
on the long edge, so this is a ~0.6× reduction, and PDF417 as rendered in Wallet
has narrow modules. Aztec tolerates this far better. Screen recordings are also
lossy-compressed, which is unkind to high-frequency patterns.

Capture 1 could not test this — it arrived at 888 px, already below the
threshold. **Fix pending measurement:** run barcode detection at native
resolution and OCR at 1600 px. Detection is the cheaper request and the
authoritative one, so spending compute there is the right trade.

### S4. M0's exit criterion tests the wrong thing — **OPEN**

"A screen recording of Wallet *visibly* contains *decodable* barcodes" — those
are different claims, and B6 is the gap between them. A barcode a human can see
may still fail detection.

**Fix:** make it a number — decode over sampled frames and require ≥1 payload
per pass across ≥90% of passes captured. `spike/m0_probe.py` reports exactly
this.

### S5. Vision may return no string for binary Aztec payloads — **OPEN**

Some carriers encode BCBP as raw bytes rather than an ASCII string;
`payloadStringValue` is `nil` for those and the data is only reachable through
`payloadData`. A pipeline reading only the string value drops them silently.
**Fix:** fall back to `payloadData` decoded as ASCII/ISO-8859-1.

### S6. Wallet's list date is not the flight date — **CONFIRMED, new**

The list row for the decoded pass read **Oct 4, 2023**; the barcode read
**2023-10-01**. Same year, three days apart. Wallet is likely showing a
relevance or expiry date rather than departure.

So B7's list date is authoritative for the **year** and only approximate for
the **day**. **Fix:** take the year from the list row, the day from the payload,
and flag any disagreement beyond ~1 day into the review table. Where only the
list row exists, mark `dateConfidence = .inferred` — good enough for a trip
boundary, not for an exact entry date.

### S7. Multiple passengers share one Wallet — **CONFIRMED, new**

The decoded pass belongs to a second passenger, not the account holder. The
model has `passengerName` but no UI for choosing whose history is being built,
and §5.1's "sort legs chronologically per passenger" assumes a selection step
that does not exist.

**Fix:** after import, offer the distinct passenger names found and let the user
pick their own. For rows with no name (list-only, Tier 1), collapsing on
`(date, origin, destination)` is correct — two passes for the same flight are
one movement for the person filing the form.

### S8. Most expired passes are not travel — **CONFIRMED, new**

The Expired list held transit cards, a hotel loyalty card, insurance documents,
event tickets and an Amtrak eTicket alongside boarding passes. The spec assumes
the list is flights.

**Fix:** filter on pass type. Note that rail passes (Amtrak, and Eurostar by
extension) *should* be kept — they are border crossings and partially close the
ground-travel gap in §5.1.

---

## Minor

- **`Package.swift` alone cannot produce an iOS app.** SwiftPM has no iOS
  application product; `PassLogApp` needs an Xcode app target, which is also
  where `Info.plist` and the `CFBundleDocumentTypes` entry for
  `com.apple.pkpass` must live.
- **Sideload expiry.** A free Apple ID gives a 7-day provisioning profile and
  weekly reinstallation. State it in the spec rather than discovering it at M6.
- **Airport DB filter is too aggressive.** Large/medium drops regional and
  island airports that appear in real itineraries. Filter on "has a non-empty
  IATA code" — roughly 9,000 rows, still under a megabyte.
- **`trips.csv` has a single `country` column** but a trip can visit several,
  and `duration_days` needs an explicit inclusive/exclusive definition since
  visa forms usually count both travel days.
- **Validation should bound the flight number** (4 digits plus optional alpha
  suffix) and reject an all-space PNR — common OCR-corruption signatures.
- **Conditional lengths are often wrong in the wild.** When the declared length
  runs past the payload end, don't discard the record — leg 1 has already
  parsed cleanly. Emit what validated and flag it.
- **Processing volume.** 346 passes at Tier 2 would be a 20-minute recording and
  several thousand frames. Another reason Tier 1 must carry the workload.

---

## Fixtures

The appendix list is good. Add:

- **F1. Two-leg pass with non-empty conditional data on leg 1** — the direct
  regression for B3. The existing "conditional items present" fixture is
  single-leg and will not catch the desync.
- **F2. Pass carrying an issue-date item** — the B2 path, including a case
  where issue year and flight year differ (a December booking for a January
  flight).
- **F3. Declared conditional length exceeding the payload** — the malformed
  case above.
- **F4. Binary Aztec payload as raw bytes** — S5.
- **F5. Leg count of `0` and of `9`** — rejected before any field is read.

The real payload from capture 1 should be checked in as a fixture with the
passenger name and PNR replaced, keeping the conditional section intact.

---

## 10. Platform: capture must happen on-device

Capture 1 came through iPhone Mirroring, recorded with the Mac's screen
recorder. iPhone Mirroring presents the phone at roughly 1× point size, so the
capture was 416×888 — about a third of the iPhone's native resolution in each
dimension, and the Aztec was 159×159 px.

That is a property of the spike rig, not of the product. The shipping app runs
on the iPhone, where the built-in recorder captures at native pixel resolution:
the same barcode would be roughly 460×460 px, about 9× the pixel area, with no
mirroring transcode in front of it. **The 24/32 decode rate is a floor.**

The Mac path stays useful for development — it is a far more convenient loop
than working on the device — but three things must be measured on-device before
they can be trusted: the decode rate (S4), the 1600 px downscale question (S3),
and whether B6's contrast correction is still needed at native resolution. It
almost certainly is; Wallet dims the barcode at render time, so resolution does
not change the contrast, only the sampling of it.

Nothing here argues for a macOS target. Automating Wallet over Mirroring would
add resolution loss, a second platform, and UI automation fragility to a
problem that on-device capture solves with the system recorder and no code.
