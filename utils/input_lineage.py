"""Compatibility wrapper for the moved input lineage module."""

import sys

from utils.lineage import inputs as _module

sys.modules[__name__] = _module
