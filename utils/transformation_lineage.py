"""Compatibility wrapper for the moved transformation lineage module."""

import sys

from utils.lineage import transformations as _module

sys.modules[__name__] = _module
