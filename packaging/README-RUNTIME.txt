LMThermal Desktop — private development preview

Linux: extract the complete directory and run ./LMThermal-Desktop.
Windows: open LMThermal-Desktop.exe after a separately qualified native build.
Keep _internal beside the executable. No Python environment or administrator access
is needed at runtime. This build is qualified on its Fedora host only.

Home opens thermal stills (LMTX/legacy), read-only LMTR recordings or historical
recording directories. Stored measurements remain authoritative. Palette and ROI
changes do not alter your source. Save rendered PNG writes a separate file.

The Live camera tab currently requires Linux. It does not connect automatically:
use Connect, then Initialize radiometric explicitly when appropriate. No common
LMTR recording control is available. Existing legacy recording remains experimental.
Close waits for owned readers/workers to finish safely.

Help / Local diagnostics shows the local rotating log location. Review logs before
sharing; no automatic upload is added. Delete the extracted application to remove
this prototype. Your external captures and local logs are kept separately.

This is not a public release. Licensing, signing and native platform qualification
remain pending. Do not treat the preview as a calibrated instrument.
Native-equivalent temperatures; absolute physical accuracy not yet independently validated.
