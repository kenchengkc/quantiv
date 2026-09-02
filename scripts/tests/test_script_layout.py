from __future__ import annotations

from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]


def test_scripts_root_is_an_operational_surface() -> None:
    assert not list(SCRIPTS_DIR.glob("test_*.py"))
    assert not (SCRIPTS_DIR / "archive").exists()


def test_nonproduction_tools_are_grouped() -> None:
    for directory in ("maintenance", "provider_probes", "research", "tests"):
        assert (SCRIPTS_DIR / directory).is_dir()
