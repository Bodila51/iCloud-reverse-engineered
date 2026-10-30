import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, HERE)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def homecloud_home(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.homecloud."""
    home = tmp_path / "homecloud-home"
    monkeypatch.setenv("HOMECLOUD_HOME", str(home))
    return home
