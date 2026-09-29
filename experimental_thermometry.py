#!/usr/bin/env python3
"""Compatibility CLI for the reconstructed official thermometry branch.

The reusable implementation lives in native_equivalent_thermometry. Its
agreement with APK arithmetic does not establish physical accuracy.
"""

import argparse
import json
from pathlib import Path

from native_equivalent_thermometry import (  # Historical import API.
    build_lookup,
    calc_fix_raw,
    lookup_frame,
    temperature_matrix,
)


def main() -> None:
    """Print the established saved-frame diagnostic in its original format."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("frame", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.frame:
        print(json.dumps({"frame": str(path), **lookup_frame(path.read_bytes())}, allow_nan=False))


if __name__ == "__main__":
    main()
