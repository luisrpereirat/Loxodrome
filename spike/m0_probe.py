#!/usr/bin/env python3
"""
M0 capture spike for PassLog.

Answers, against a real capture, the questions the spec currently assumes:

  Q1  Do barcodes survive a Wallet screen recording well enough to decode?
  Q2  Does downscaling to 1600 px break PDF417 decode?          (review S3)
  Q3  Do multi-leg payloads carry a conditional section that must be
      skipped between legs?                                     (review B3)
  Q4  Do real payloads carry the issue-date item, i.e. the year? (review B2)

Usage:
    python3 m0_probe.py CAPTURE [--fps 3] [--outdir DIR] [--raw]

CAPTURE may be a video (.mov/.mp4) or a still image. Payloads are redacted
unless --raw is given: they contain passenger name, PNR and frequent flyer
number, and the spec's own §10 says not to spill those casually.

NOTE ON FIDELITY: this uses zxing-cpp, not Apple Vision. A decode here is
strong evidence Vision will manage it too. A failure here is weaker evidence
against Vision, which is generally the better decoder. Treat a negative
result as "investigate on device", not "the path is dead".
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter

import cv2
import zxingcpp

SYMBOLOGIES = {"Aztec", "PDF417", "QRCode", "Code128"}
IATA = re.compile(r"^[A-Z]{3}$")

# --- BCBP ---------------------------------------------------------------
# Mandatory header, then a 37-byte mandatory block per leg, each followed by
# a variable-length conditional section whose length the block declares.

HEADER_LEN = 23          # format(1) legs(1) name(20) eticket(1)
LEG_LEN = 37


def parse_bcbp(payload: str) -> dict:
    """Parse a BCBP payload. Returns {'ok': False, 'reason': ...} on reject."""
    b = payload  # BCBP is ASCII; index by position only after this check
    if not b.isascii():
        return {"ok": False, "reason": "non-ascii payload"}
    if len(b) < HEADER_LEN + LEG_LEN:
        return {"ok": False, "reason": f"too short ({len(b)})"}
    if b[0] != "M":
        return {"ok": False, "reason": f"format code {b[0]!r} != 'M'"}
    if not b[1].isdigit() or not (1 <= int(b[1]) <= 4):
        return {"ok": False, "reason": f"leg count {b[1]!r} outside 1..4"}

    n_legs = int(b[1])
    out = {
        "ok": True,
        "passenger": b[2:22].strip(),
        "n_legs": n_legs,
        "legs": [],
        "issue_date_raw": None,
        "conditional_sizes": [],
    }

    pos = HEADER_LEN
    for i in range(n_legs):
        if pos + LEG_LEN > len(b):
            return {"ok": False, "reason": f"truncated before leg {i + 1}"}
        f = b[pos:pos + LEG_LEN]
        leg = {
            "index": i,
            "pnr": f[0:7].strip(),
            "origin": f[7:10],
            "destination": f[10:13],
            "carrier": f[13:16].strip(),
            "flight": f[16:21].strip(),
            "julian": f[21:24],
            "compartment": f[24:25],
            "seat": f[25:29].strip(),
            "sequence": f[29:34].strip(),
            "status": f[34:35],
        }
        for key in ("origin", "destination"):
            if not IATA.match(leg[key]):
                return {"ok": False, "reason": f"leg {i + 1} bad {key} {leg[key]!r}"}
        if not leg["julian"].isdigit() or not (1 <= int(leg["julian"]) <= 366):
            return {"ok": False, "reason": f"leg {i + 1} bad julian {leg['julian']!r}"}
        leg["julian"] = int(leg["julian"])

        try:
            cond_size = int(f[35:37], 16)
        except ValueError:
            return {"ok": False, "reason": f"leg {i + 1} bad conditional size {f[35:37]!r}"}
        out["conditional_sizes"].append(cond_size)
        pos += LEG_LEN

        # B3: the conditional section sits between this leg and the next.
        # Skipping it is what keeps multi-leg parsing in sync.
        conditional = b[pos:pos + cond_size]
        if len(conditional) < cond_size:
            leg["conditional_truncated"] = True
        pos += cond_size

        # B4: leg 1's conditional section should carry the issue date, whose
        # first character is the last digit of the year.
        if i == 0 and conditional[:1] == ">" and len(conditional) >= 11:
            unique_size = int(conditional[2:4], 16) if conditional[2:4].strip() else 0
            if unique_size >= 7:
                out["issue_date_raw"] = conditional[7:11]

        out["legs"].append(leg)

    return out


def redact(payload: str) -> str:
    """Keep structure, drop identity: name and PNR blanked."""
    if len(payload) < HEADER_LEN:
        return "<short>"
    return payload[:2] + "*" * 20 + payload[22:HEADER_LEN] + "*" * 7 + payload[HEADER_LEN + 7:]


# --- capture ------------------------------------------------------------

def extract_frames(path: pathlib.Path, outdir: pathlib.Path, fps: float) -> list:
    import imageio_ffmpeg
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".heic"}:
        return [path]
    outdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(path),
         "-vf", f"fps={fps}", "-q:v", "2", str(outdir / "f%05d.jpg")],
        check=True, capture_output=True,
    )
    return sorted(outdir.glob("f*.jpg"))


def decode(img):
    results = []
    for r in zxingcpp.read_barcodes(img):
        fmt = str(r.format).split(".")[-1]
        if fmt in SYMBOLOGIES and r.text:
            results.append((fmt, r.text))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", type=pathlib.Path)
    ap.add_argument("--fps", type=float, default=3.0)
    ap.add_argument("--max-dim", type=int, default=1600,
                    help="downscale target for the S3 comparison run")
    ap.add_argument("--outdir", type=pathlib.Path, default=pathlib.Path("m0-out"))
    ap.add_argument("--raw", action="store_true", help="print unredacted payloads")
    args = ap.parse_args()

    frames = extract_frames(args.capture, args.outdir / "frames", args.fps)
    print(f"capture : {args.capture.name}")
    print(f"frames  : {len(frames)} at {args.fps} fps\n")

    full, scaled = {}, set()
    frames_with_decode = 0
    symbology_counts = Counter()

    for fp in frames:
        img = cv2.imread(str(fp))
        if img is None:
            continue
        hits = decode(img)
        if hits:
            frames_with_decode += 1
        for fmt, text in hits:
            symbology_counts[fmt] += 1
            full.setdefault(text, {"symbology": fmt, "first_frame": fp.name})

        # Q2: same frame, downscaled the way §4.1 proposes.
        h, w = img.shape[:2]
        if max(h, w) > args.max_dim:
            s = args.max_dim / max(h, w)
            small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
            scaled.update(t for _, t in decode(small))

    print("=== Q1  decode rate ===")
    pct = 100 * frames_with_decode / len(frames) if frames else 0
    print(f"frames yielding >=1 barcode : {frames_with_decode}/{len(frames)} ({pct:.0f}%)")
    print(f"unique payloads             : {len(full)}")
    print(f"symbologies                 : {dict(symbology_counts) or 'none'}\n")

    print(f"=== Q2  downscale to {args.max_dim}px ===")
    lost = set(full) - scaled
    print(f"payloads at native res      : {len(full)}")
    print(f"payloads at {args.max_dim}px{'':9}: {len(scaled)}")
    print(f"LOST by downscaling         : {len(lost)}"
          + ("  <- do barcode detection at native res" if lost else "  <- downscale is safe"))
    for t in lost:
        print(f"    lost: {full[t]['symbology']}")
    print()

    print("=== Q3/Q4  BCBP structure ===")
    valid = rejected = 0
    with_issue_date = multi_leg = 0
    records = []
    for text, meta in full.items():
        p = parse_bcbp(text)
        if not p["ok"]:
            rejected += 1
            print(f"  reject ({meta['symbology']}): {p['reason']}")
            continue
        valid += 1
        if p["n_legs"] > 1:
            multi_leg += 1
        if p["issue_date_raw"]:
            with_issue_date += 1
        records.append({**p, "symbology": meta["symbology"],
                        "payload": text if args.raw else redact(text)})
        route = " ".join(f"{l['origin']}>{l['destination']}" for l in p["legs"])
        carrier = " ".join(f"{l['carrier']}{l['flight']}" for l in p["legs"])
        julians = ",".join(str(l["julian"]) for l in p["legs"])
        print(f"  ok  {route:22} {carrier:16} day={julians:8}"
              f" cond={p['conditional_sizes']}"
              f" issue={p['issue_date_raw'] or '-'}")

    print(f"\nvalid BCBP        : {valid}")
    print(f"rejected          : {rejected}")
    print(f"multi-leg (Q3)    : {multi_leg}"
          + ("  <- conditional-skip logic exercised" if multi_leg else ""))
    print(f"carry issue date (Q4): {with_issue_date}/{valid}"
          + ("  <- year recoverable from the barcode" if with_issue_date else
             "  <- year NOT in barcode; list-row date is the only source"))

    out = args.outdir / "results.json"
    out.write_text(json.dumps(records, indent=2))
    print(f"\nwrote {out}" + ("" if args.raw else "  (payloads redacted; --raw to keep them)"))


if __name__ == "__main__":
    sys.exit(main())
