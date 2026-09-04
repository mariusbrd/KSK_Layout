from __future__ import annotations

from contextlib import nullcontext

import pandas as pd

from components import ui_compat


def test_lazy_excel_download_does_not_build_until_prepare_click(monkeypatch):
    calls = {"builder": 0, "download": 0, "html": 0}
    button_labels = []

    class _Components:
        class v1:
            @staticmethod
            def html(*_args, **_kwargs):
                calls["html"] += 1

    class _FakeStreamlit:
        session_state = {}
        components = _Components

        @staticmethod
        def spinner(_label):
            return nullcontext()

        @staticmethod
        def error(_message):
            raise AssertionError("unexpected error")

    monkeypatch.setattr(ui_compat, "st", _FakeStreamlit)
    monkeypatch.setattr(
        ui_compat,
        "button_compat",
        lambda label, **_kwargs: button_labels.append(label) or False,
    )

    def _download(*_args, **_kwargs):
        calls["download"] += 1

    monkeypatch.setattr(ui_compat, "download_button_compat", _download)

    def _builder():
        calls["builder"] += 1
        return b"xlsx"

    assert not ui_compat.lazy_excel_download_button_compat(
        label="Excel Download",
        data_builder=_builder,
        file_name="export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="lazy_export",
        fingerprint=("v1",),
    )
    assert calls == {"builder": 0, "download": 0, "html": 0}
    assert button_labels == ["Excel Download"]


def test_lazy_excel_download_builds_once_after_prepare_click(monkeypatch):
    calls = {"builder": 0, "download": 0, "html": 0}

    class _Components:
        class v1:
            @staticmethod
            def html(markup, **kwargs):
                calls["html"] += 1
                assert "download" in markup
                assert "window.parent.document" in markup
                assert kwargs["height"] == 0

    class _FakeStreamlit:
        session_state = {}
        components = _Components

        @staticmethod
        def spinner(_label):
            return nullcontext()

        @staticmethod
        def error(_message):
            raise AssertionError("unexpected error")

    monkeypatch.setattr(ui_compat, "st", _FakeStreamlit)
    monkeypatch.setattr(ui_compat, "button_compat", lambda *_args, **_kwargs: True)

    def _download(*_args, **_kwargs):
        calls["download"] += 1
        return True

    monkeypatch.setattr(ui_compat, "download_button_compat", _download)

    def _builder():
        calls["builder"] += 1
        return b"xlsx"

    assert ui_compat.lazy_excel_download_button_compat(
        label="Excel Download",
        data_builder=_builder,
        file_name="export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="lazy_export_click",
        fingerprint=("v1",),
    )
    assert calls == {"builder": 1, "download": 0, "html": 1}


def test_lazy_excel_download_reuses_payload_for_same_fingerprint(monkeypatch):
    calls = {"builder": 0, "html": 0}

    class _Components:
        class v1:
            @staticmethod
            def html(*_args, **_kwargs):
                calls["html"] += 1

    class _FakeStreamlit:
        session_state = {}
        components = _Components

        @staticmethod
        def spinner(_label):
            return nullcontext()

        @staticmethod
        def error(_message):
            raise AssertionError("unexpected error")

    monkeypatch.setattr(ui_compat, "st", _FakeStreamlit)
    monkeypatch.setattr(ui_compat, "button_compat", lambda *_args, **_kwargs: True)

    def _builder():
        calls["builder"] += 1
        return f"xlsx-{calls['builder']}".encode()

    for _ in range(2):
        assert ui_compat.lazy_excel_download_button_compat(
            label="Excel Download",
            data_builder=_builder,
            file_name="export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="lazy_export_cached",
            fingerprint=("v1",),
        )

    assert calls == {"builder": 1, "html": 2}


def test_lazy_excel_download_rebuilds_payload_when_fingerprint_changes(monkeypatch):
    calls = {"builder": 0, "html": 0}

    class _Components:
        class v1:
            @staticmethod
            def html(*_args, **_kwargs):
                calls["html"] += 1

    class _FakeStreamlit:
        session_state = {}
        components = _Components

        @staticmethod
        def spinner(_label):
            return nullcontext()

        @staticmethod
        def error(_message):
            raise AssertionError("unexpected error")

    monkeypatch.setattr(ui_compat, "st", _FakeStreamlit)
    monkeypatch.setattr(ui_compat, "button_compat", lambda *_args, **_kwargs: True)

    def _builder():
        calls["builder"] += 1
        return b"xlsx"

    for fingerprint in [("v1",), ("v2",)]:
        assert ui_compat.lazy_excel_download_button_compat(
            label="Excel Download",
            data_builder=_builder,
            file_name="export.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="lazy_export_rebuild",
            fingerprint=fingerprint,
        )

    assert calls == {"builder": 2, "html": 2}


def test_dataframe_export_fingerprint_changes_with_values():
    first = pd.DataFrame({"Organisationseinheit": ["A"], "MAK": [1.0]})
    second = pd.DataFrame({"Organisationseinheit": ["A"], "MAK": [2.0]})

    assert ui_compat.dataframe_export_fingerprint(first, "org") != ui_compat.dataframe_export_fingerprint(second, "org")
