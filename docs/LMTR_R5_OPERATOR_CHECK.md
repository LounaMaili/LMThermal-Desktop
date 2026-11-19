# Camera-free R5 operator check (Linux / Windows)

Use the exact corrective branch/commit being reviewed, Python3.10+ and a venv with
`requirements.txt`. No phone, USB camera, recording or source conversion is needed.
Do not install or use ADB. Keep input files read-only; save PNG to another new path.

Linux:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python lmthermal_viewer.py --lmtr tests/fixtures/lmtr/files/illustration-01-stored.lmtr
```

For headless tests only, prefix the first command with `QT_QPA_PLATFORM=offscreen`.
A real window requires a usable local Qt platform/display; offscreen is not operator
acceptance. On Windows PowerShell use `.venv\Scripts\python.exe` in both commands.
Record Git SHA, Python/Qt versions, OS/build/scaling and pass/fail observations.
Windows must rerun the full suite on this new code; older Windows runs do not qualify it.

1. Normal open selects the first initial-CHUNK observation automatically: a3×2 synthetic masked
   matrix: max30 at(1,0), min−0 at(2,0); (0,1) is invalid. Hover, draw/clear a small
   ROI, resize, switch palettes and Auto/Locked. Same native cells keep their values;
   mask pixels have no temperature. Previous returns to the blank segment start;
   Next restores the observation. Invalid Locked bounds show a warning; Auto works.
   `illustration-02-stored.lmtr` similarly opens a384×288 plane whose samples are all
   25°C: a uniform color is correct. Neither canonical file is a photographic scene.
2. Play/pause/step/seek, then rapidly scrub while opening `illustration-02-stored.lmtr`.
   Only the new source may publish. Verify entire file changes integrity scope to full;
   normal opening alone only certifies opened/navigation records and selected CHUNKs.
3. Save separate PNG while navigating in the destination dialog. The PNG must use
   the frame/colors frozen before the dialog. Existing targets/source paths refuse.
4. Open `missing-end.lmtr` normally (refusal), then explicitly enable verified-prefix
   recovery and reopen. It must say RECOVERED PREFIX / unknown tail, never completed.
   `committed-body-corrupt.lmtr` and `unknown-required-feature.lmtr` must still refuse,
   including in recovery. No prior image/legend/temperature may remain after failure.
5. Open a legacy `.lmthermal`/LMTX still and a legacy directory/NPZ recording through
   their existing entry points. LMTR uses its separate reader; it is never auto-converted.
   Close each window normally and verify process exit/file release.

The tiny canonical example is not a photographic aiming scene. Optional synthetic
large-grid and >4GiB probes are documented in the qualification report. Real Android
LMTR source/operator interoperability remains a separately authorized future gate.
[One real interrupted Full observation](LMTR_INITIAL_OPEN.md) now passes automated
native Linux readback; this does not replace multi-frame/operator acceptance.

Native-equivalent temperatures; absolute physical accuracy not yet independently validated.
