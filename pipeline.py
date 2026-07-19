#!/usr/bin/env python3
"""Backward-compatible launcher for :mod:`a_stock_tracker.cli`."""

import sys

from a_stock_tracker import cli as _cli


if __name__ == "__main__":
    _cli.run()
else:
    # Preserve the historical ``import pipeline`` seam used by downstream code.
    sys.modules[__name__] = _cli
