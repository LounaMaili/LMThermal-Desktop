# Desktop product workspace

## Scope and audit

The companion product now has a single Home and document-tab entry point:
`python lmthermal_desktop.py`. It builds on the separately reviewed R5 tip
`63d8076c6297189ab6ec46747b78b4a32cd05407`, which is not assumed merged into main.
The older `lmthermal_viewer.py` diagnostic CLI remains available, including its
historical automatic Connect behavior. Use the new workspace for an explicit connection.

| Operation | Reused engine | Current boundary |
| --- | --- | --- |
| Live, still capture, CSV and historical recording | MainWindow | Linux V4L2 acquisition only; initialization explicit |
| LMTX and historical still | Strict offline adapters | Stored values/validity authoritative; no recomputation |
| LMTR | R5 coalesced worker, lazy bounded CHUNK cache | Read-only; full integrity is an explicit verification |
| Timeline, point/ROI, palettes, metadata, PNG | Existing document widgets | Source immutable; exports separate files |

Home and File menu expose these routes. Each document keeps its engine's established
semantics. The [offline qualification follow-up](OFFLINE_QUALIFICATION.md) omits
live-only actions in saved tabs, compacts saved-source captions and exposes LMTR
technical diagnostics on demand while preserving exact integrity scopes and data.
A shared toolbar/translation,
thumbnail library, edited analysis persistence and a native Windows camera backend
are future product work. LMTR production recording remains disabled. This shell is
a first coherent integration, not a completed redesign of every legacy panel.

## Ownership and failures

At most four documents, including one Live tab, may exist. This is an application
limit, not a total-RSS bound: each reader's existing local resource limits still apply.
Opening a file/creating Live sends no camera control. Opening/verification remains on
existing workers. Closing a tab clears observations and cancels cooperative work;
Qt owners remain alive until worker completion, then tab deletion is queued outside
Close. A queued live callback cannot publish after Close begins. No QThread is forcibly
terminated. A blocked I/O operation may delay final Close. Historical recorder/log
finalization retains its existing refusal/finalization behavior; no latency guarantee.

`--self-check` loads schema/dependencies and renders a synthetic native-size matrix
without hardware or a display. `--verify-lmtr PATH [--recover-lmtr]` performs the same
full reader verification as the existing UI and closes the source. Recovery is explicit,
never repair. Use `--open PATH`, optionally `--legacy-recording` for directory recordings.

## Diagnostics and validation

Local logs rotate at 512 KiB with two backups: Linux `$XDG_STATE_HOME/lmthermal-desktop/logs`
(default `~/.local/state`), Windows `%LOCALAPPDATA%/LMThermal Desktop/logs`, macOS
`~/Library/Logs/LMThermal Desktop/logs`. Unwritable state falls back to stderr. Paths may
appear in exception logs; review before sharing. No telemetry or scene capture is added.

Run `QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v`.
Tests cover immutable source opening, routing, explicit camera ownership, document
limits, busy-worker cancellation, queued callback exclusion and local logging.
Offscreen layout is host evidence, not native-window/operator/Windows qualification.
No hardware is required. The 51 canonical R3 fixtures and reader/wire semantics stay
unchanged. Linux hardware tests require separate authorization; Windows needs a native
regression and operator interaction, never inferred from Linux Qt.

**Native-equivalent temperatures; absolute physical accuracy not yet independently validated.**

## Phase 4 host result (2026-11-12)

The Linux host suite passes 242 tests with no skips. Offscreen Home and synthetic
LMTR screenshots were inspected; controls, native-grid view and legend are visible.
The native xcb attempt failed before window creation because the current display is
unavailable. No graphical configuration was changed. No physical or Windows test ran.

## Phase 5 review

Refused legacy finalization now aborts application Close without disabling the document
permanently; ownership and safety refusals remain intact. LMTR navigation/palette controls
are split into narrower rows without changing native measurements. See
[qualification and the explicit window-check tool](phase5/QUALIFICATION.md).
