from pathlib import Path

from utils import settings_loader


def test_load_user_settings_migrates_old_exclusions_schema(tmp_path, monkeypatch):
    settings_file = tmp_path / "user_settings.json"
    settings_file.write_text(
        """
        {
            "stichtag": "2025-12-31",
            "simulation": { "horizon_months": 99 },
            "include_future_hires": true,
            "exclusions": {
                "vorstand": true,
                "ruhend_bv": true,
                "org_units": ["9900", "99XX"]
            }
        }
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(settings_loader, "SETTINGS_FILE", Path(settings_file))

    loaded = settings_loader.load_user_settings()

    assert loaded["exclusions"]["vorstand"] is True
    assert loaded["exclusions"]["ruhend_bv"] is True
    assert loaded["exclusions"]["org_units"] == ["9900", "99XX"]
    assert loaded["exclusions"]["planstellen_follow_person"] is False

    persisted = settings_file.read_text(encoding="utf-8")
    assert '"planstellen_follow_person": false' in persisted
