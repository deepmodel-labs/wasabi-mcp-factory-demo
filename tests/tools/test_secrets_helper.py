import json
import os
import stat
from pathlib import Path

import pytest

from wasabi_mcp.config import Settings
from wasabi_mcp.safety import ToolRefused
from wasabi_mcp.tools import _secrets


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def settings_for(directory: Path) -> Settings:
    return Settings(secrets_dir=directory)


def test_prepare_returns_none_when_unset():
    assert _secrets.prepare(Settings()) is None


def test_prepare_creates_a_missing_directory_with_mode_0700(tmp_path):
    directory = tmp_path / "secrets"
    assert _secrets.prepare(settings_for(directory)) == directory
    assert directory.is_dir()
    assert mode(directory) == 0o700


def test_prepare_accepts_an_existing_private_directory(tmp_path):
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o700)
    assert _secrets.prepare(settings_for(directory)) == directory


@pytest.mark.parametrize("unsafe", [0o755, 0o750, 0o707, 0o777])
def test_prepare_refuses_a_shared_directory_with_a_chmod_hint(tmp_path, unsafe):
    directory = tmp_path / "secrets"
    directory.mkdir()
    directory.chmod(unsafe)
    with pytest.raises(ToolRefused, match=f"chmod 700 {directory}"):
        _secrets.prepare(settings_for(directory))


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any directory")
def test_prepare_refuses_a_directory_it_cannot_write(tmp_path):
    directory = tmp_path / "secrets"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        with pytest.raises(ToolRefused, match="isn't writable"):
            _secrets.prepare(settings_for(directory))
    finally:
        directory.chmod(0o700)


def test_prepare_refuses_a_file(tmp_path):
    path = tmp_path / "secrets"
    path.write_text("not a directory")
    with pytest.raises(ToolRefused, match="isn't a directory"):
        _secrets.prepare(settings_for(path))


def test_write_creates_a_0600_json_file(tmp_path):
    path = Path(_secrets.write(tmp_path, "iam_access_key_create", {"secret": "S3CR3T"}, "key"))
    assert path.is_absolute()
    assert path.parent == tmp_path
    assert path.name.startswith("iam_access_key_create-")
    assert path.suffix == ".json"
    assert mode(path) == 0o600
    assert json.loads(path.read_text()) == {"secret": "S3CR3T"}


def test_two_writes_create_two_files(tmp_path):
    first = _secrets.write(tmp_path, "tool", {"n": 1}, "one")
    second = _secrets.write(tmp_path, "tool", {"n": 2}, "two")
    assert first != second
    assert json.loads(Path(first).read_text()) == {"n": 1}
    assert json.loads(Path(second).read_text()) == {"n": 2}


def test_write_never_overwrites_an_existing_file(tmp_path, monkeypatch):
    existing = tmp_path / "tool-fixed.json"
    existing.write_text("keep me")
    monkeypatch.setattr(_secrets.secrets, "token_hex", lambda _n: "fixed")
    with pytest.raises(ToolRefused, match="AKIDEXAMPLE was created"):
        _secrets.write(tmp_path, "tool", {"secret": "x"}, "AKIDEXAMPLE")
    assert existing.read_text() == "keep me"


def test_write_failure_names_the_created_resource(tmp_path):
    missing = tmp_path / "gone"
    with pytest.raises(ToolRefused) as raised:
        _secrets.write(missing, "tool", {"secret": "x"}, "Access key AKIDEXAMPLE")
    message = str(raised.value)
    assert "Access key AKIDEXAMPLE was created" in message
    assert "delete or rotate" in message
