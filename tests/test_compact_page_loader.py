from __future__ import annotations

import inspect

from utils import compact_page_loader


def test_loaded_compact_export_accepts_lineage_ids():
    compact_page_loader.clear_compact_page_module_cache()
    module = compact_page_loader.load_compact_page_module()

    assert "lineage_ids" in inspect.signature(module.export_to_excel).parameters


def test_compact_page_loader_reloads_when_file_signature_changes(monkeypatch):
    compact_page_loader.clear_compact_page_module_cache()

    first = compact_page_loader.load_compact_page_module()
    original_signature = compact_page_loader._file_signature

    def _changed_signature(path):
        mtime_ns, size = original_signature(path)
        return mtime_ns + 1, size

    monkeypatch.setattr(compact_page_loader, "_file_signature", _changed_signature)
    second = compact_page_loader.load_compact_page_module()

    assert second is not first
