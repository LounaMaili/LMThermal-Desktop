# Shared Android LMTX conformance corpus

These eleven `.lmtx` files and `corpus.json` are copied **byte for byte** from
[LMThermal core/src/test/resources/lmtx](https://github.com/LounaMaili/LMThermal/tree/1cbdd776a485c43e1e7cfa104e3741d1b7e99658/core/src/test/resources/lmtx)
at Android export commit `1cbdd776a485c43e1e7cfa104e3741d1b7e99658`.
`corpus.json` is the original producer inventory: filename, whole-archive
SHA-256, size and expected result. Desktop tests check every recorded hash
and size before testing import. Do not regenerate equivalent Desktop files
or edit these archives to satisfy the reader.

Eight captures are valid: temperature-only, validity-mask, no-valid-pixels,
preview-only 160×120, alternate 7×19, HT-301-rich sanitized, newer compatible
minor and unknown optional extension/shape/palette. Three must fail with
`unsupported_major`, `unsupported_required_feature` and `integrity_mismatch`.

The HT fixture retains spatial room/ceiling structure from the previously
sanitized research frame, source SHA-256
`fde6a4b803b68fcea07ab7b428f4769d22e896f01055969f43102bdc9270f5d6`.
It is not a hand-target measurement or proof of physical calibration. Other
files are producer-generated synthetic examples. The real current Android
SAF export and its independent source-bit proof remain private local
validation files and are deliberately excluded from this corpus.

To refresh, select an explicitly reviewed producer commit, copy its complete
corpus and inventory unchanged, update this source reference, and run
`test_lmtx_import.py`. A producer/consumer mismatch must be investigated
against the [canonical contract](https://github.com/LounaMaili/LMThermal/blob/1cbdd776a485c43e1e7cfa104e3741d1b7e99658/docs/LMTX_FORMAT_V1.md),
not hidden by changing expected outcomes.
