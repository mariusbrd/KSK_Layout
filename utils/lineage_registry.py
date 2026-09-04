"""Compatibility wrapper for the moved lineage registry module."""

import sys

from utils.lineage import registry as _module

sys.modules[__name__] = _module
