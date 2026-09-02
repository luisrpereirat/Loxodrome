# M0 capture spike

Settles, against a real capture, four things the spec currently assumes.

```
pip install opencv-python-headless zxing-cpp imageio-ffmpeg
python3 spike/m0_probe.py capture.mov --fps 3
```

| | Question | Spec section |
| :- | :- | :- |
| Q1 | Do barcodes survive a Wallet screen recording? | §8 risk 1, M0 |
| Q2 | Does downscaling to 1600 px break PDF417? | §4.1, review S3 |
| Q3 | Do multi-leg payloads need the conditional section skipped? | §4.3, review B3 |
| Q4 | Do real payloads carry the issue date, i.e. the year? | §4.4, review B2 |

Q3 and Q4 do not need a video — any single decoded payload answers them.

**Fidelity caveat.** The probe decodes with zxing-cpp, not Apple Vision.
A decode here is strong evidence Vision will manage it. A failure is weaker
evidence against Vision, which is generally the better decoder — treat a
negative as "check on device", not "the path is dead".

**Privacy.** Payloads carry passenger name, PNR and frequent flyer number,
and a PNR plus surname opens a booking on many airline sites (§10). Output
is redacted by default; `--raw` disables that. Do not commit captures or
unredacted results — see `.gitignore`.
