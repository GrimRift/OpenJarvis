import pytest


@pytest.fixture(autouse=True)
def _export_into_tmp(tmp_path, monkeypatch):
    """Parts a test makes (the worker auto-exports) never reach the real
    Audiobooks folder or OneDrive."""
    monkeypatch.setenv("SAGE_AUDIOBOOKS_DIR", str(tmp_path / "Audiobooks"))
    monkeypatch.setenv("SAGE_AUDIOBOOKS_ONEDRIVE", "off")
