"""Compatibility wrapper for the moved lineage Excel module."""

import sys

from utils.lineage import excel as _module

sys.modules[__name__] = _module
