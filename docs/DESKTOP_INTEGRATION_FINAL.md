# Desktop finalization

The finalization branch starts at `159f96be6fc6e20499970476d36ef64075a5a7cf`.
The follow-up review targets the consolidation branch at
`5c67906448dbbd4675cfa446acc148747c4ad7c8` (PR #4).

Time-range handle drags select A on the first snapped movement to the left of a
coincident instant, or B on the first snapped movement to the right. The rest
of the gesture keeps that handle. Clicking preserves an inclusive zero-duration
range. Chart, summary and exports share the same integer bounds.

The worker claims the analysis token under the same condition lock that removes
it from the queue. Cancel can therefore identify a queued or active request even
during handoff. A cancelled calculation retains the previous complete result.
The independent detailed CSV verifier hashes in bounded blocks with Python 3.10
standard-library APIs. The summary verifier reuses the detailed value and time
checks before independently aggregating each range.

## Short Fedora operator recipe

1. Launch the extracted candidate with `launch.sh`. Open a saved LMTR with
   explicit losses. Check that the first observation, image and timeline appear.
2. Open **Temperature over time**. Add two points and two ROIs, run **Calculate**,
   and enable a compatible Δ. Play and seek to a gap, then pause and return to
   a valid observation. Check that both cursors follow and the gap is blank.
3. Enable **Select range**. Drag a range and bring A onto B. Click the overlapping
   handles, then drag left. Bring B back onto A and drag right. Verify the other
   bound stays in place, and check **Full period**. Try the window size used daily.
4. Run **Calculate**, cancel during a sufficiently long scan and confirm the
   previous series remains. Calculate again and check that the new operation succeeds.
5. Export the detailed CSV and the selected range summary to new paths. Compare
   the table against the summary and check exact integer bounds. The independent
   commands are in the [range guide](TIME_RANGE_STATISTICS.md#focused-validation).

Operator results should record the candidate SHA, Fedora/Qt backend, display size,
and which steps were completed. Native Windows qualification is a separate step.

## Linux candidate validation, 2026-11-27

The executable was built from clean commit
`53008efb3bc529d7664964c10668c9ba9273ab85` on Fedora 45 using the prepared
Ubuntu 22.04 build environment. The private archive is 146,150,657 bytes.
Its SHA-256 is `d4694f577d0231ef6793e17b14490d87d3ce25c7c9c84da6f5064a17fc1a352c`.
All 535 extracted files matched the manifest. The highest observed GLIBC
requirement is 2.35.

- 71 targeted tests passed both on the host and in the build environment.
  Three new cases exercise coincident handles and cancellation at the queue
  and handoff boundaries. The in-flight cancellation test uses explicit
  thread events. The handoff test also rejects a stale UI result after retry.
- The extracted binary passed its self-check and real-recording scenario in
  an isolated runtime without installed Python. The same binary passed in
  an exposed Fedora xcb window using actual Qt file selectors. The scenario
  exercised four selections, Δ, playback, cancellation and retry, and
  expanded coincident handles in both directions.
- On CPython 3.10.22, independent verification decoded and checked 535 detailed
  rows and two five-row summaries. The selected period had 23 valid readings
  and 32 declared losses per series. The gap-only interval had blank
  temperatures. The detailed export matched the previous candidate byte for
  byte and the LMTR retained its original SHA-256.

Local artifacts are in `build/integration-final-20261127/` under the primary
Desktop checkout. The native interactions were automated in the application.
The short recording has an interrupted END and explicit losses. Fedora operator
acceptance and exact-SHA Windows qualification remain pending.
