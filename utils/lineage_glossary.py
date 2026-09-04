"""Compatibility wrapper for the moved lineage glossary module."""

import sys

from utils.lineage import glossary as _module

sys.modules[__name__] = _module
