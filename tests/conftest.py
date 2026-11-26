import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest


@pytest.fixture(autouse=True)
def _checks_state_in_tmp(tmp_path, monkeypatch):
    """Každé save() hlásí dokončený graf do $DATA_DIR/meta/checks.json (last_checked.py).
    Testy generátorů nesmějí psát do skutečného data/ — stav jde do adresáře testu."""
    import last_checked
    monkeypatch.setattr(last_checked, "STATE_FILE", tmp_path / "meta" / "checks.json")
