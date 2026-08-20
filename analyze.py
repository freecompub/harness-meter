#!/usr/bin/env python3
"""Thin wrapper so `python analyze.py` keeps working after the src-layout move.

The implementation lives in `harness_meter.analyze`; it is also exposed as the
`harness-meter-analyze` console script and as `python -m harness_meter.analyze`.
"""

from harness_meter.analyze import main

if __name__ == "__main__":
    main()
