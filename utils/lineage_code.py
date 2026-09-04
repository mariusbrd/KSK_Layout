"""Compatibility wrapper for the moved lineage code-index module."""

import sys

from utils.lineage import code_index as _module

sys.modules[__name__] = _module
