"""Explicit runtime input checks, not native or statistical capability evidence."""
import hashlib
from pathlib import Path

import pytest

from tehm.orfs_runtime_resources import VERSION, verify_runtime_resources


def resource_binding(root: Path) -> dict:
    terminfo, openssl = root / "terminfo/d/dumb", root / "openssl/openssl.cnf"
    for path, data in ((terminfo, b"test-pinned-opaque-resource"),
                       (openssl, b"# no active .include\nopenssl_conf = openssl_init\n[openssl_init]\n")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"version": VERSION,
            "environment": {"TERM": "dumb", "TERMINFO": str(root / "terminfo"),
                            "TERMINFO_DIRS": str(root / "terminfo"), "OPENSSL_CONF": str(openssl)},
            "file_sha256": {str(path): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in (terminfo, openssl)}}


def test_binding_is_copied_and_does_not_infer_ambient_resources(tmp_path, monkeypatch):
    value = resource_binding(tmp_path)
    monkeypatch.setenv("OPENSSL_CONF", "/host/never/read")
    copied = verify_runtime_resources(value)
    assert copied == value and copied is not value
    copied["environment"]["TERM"] = "changed"
    assert value["environment"]["TERM"] == "dumb"


@pytest.mark.parametrize("change", ["extra_flag", "missing_file", "extra_file", "short_pin",
                                  "wrong_pin", "ambient_env", "multiple_terminfo", "wrong_term"])
def test_binding_rejects_ambiguous_and_unpinned_inputs(tmp_path, change):
    value = resource_binding(tmp_path)
    first = next(iter(value["file_sha256"]))
    if change == "extra_flag":
        value["valid"] = True
    elif change == "missing_file":
        value["file_sha256"].pop(first)
    elif change == "extra_file":
        value["file_sha256"][str(tmp_path / "extra")] = "sha256:" + "0" * 64
    elif change == "short_pin":
        value["file_sha256"][first] = "sha256:pin"
    elif change == "wrong_pin":
        value["file_sha256"][first] = "sha256:" + "0" * 64
    elif change == "ambient_env":
        value["environment"]["LD_LIBRARY_PATH"] = "/host/libraries"
    elif change == "multiple_terminfo":
        value["environment"]["TERMINFO_DIRS"] += ":/host/terminfo"
    else:
        value["environment"]["TERM"] = "xterm"
    with pytest.raises(ValueError):
        verify_runtime_resources(value)


@pytest.mark.parametrize("suffix", ["../escape", "with space", "inject$var", "inject;command"])
def test_resource_paths_reject_make_shell_or_lexical_ambiguity(tmp_path, suffix):
    value = resource_binding(tmp_path)
    value["environment"]["OPENSSL_CONF"] = str(tmp_path) + "/" + suffix
    with pytest.raises(ValueError, match="shell-safe"):
        verify_runtime_resources(value)


@pytest.mark.parametrize("change", ["missing", "symlink", "empty", "directory", "fifo"])
def test_pinned_resource_must_still_be_a_regular_file(tmp_path, change):
    import os
    value = resource_binding(tmp_path)
    path = Path(value["environment"]["OPENSSL_CONF"])
    original = path.read_bytes()
    path.unlink()
    if change == "symlink":
        target = tmp_path / "replacement.cnf"
        target.write_bytes(original)
        path.symlink_to(target)
    elif change == "empty":
        path.write_bytes(b"")
    elif change == "directory":
        path.mkdir()
    elif change == "fifo":
        os.mkfifo(path)
    with pytest.raises(ValueError):
        verify_runtime_resources(value)


@pytest.mark.parametrize("data", [b".include /host/unbound.cnf\n", b"  .include = /host/foo\n",
                                b"[init]\n\x00", b"\xff\n"])
def test_even_correctly_pinned_invalid_or_unbound_config_is_rejected(tmp_path, data):
    value = resource_binding(tmp_path)
    path = Path(value["environment"]["OPENSSL_CONF"])
    path.write_bytes(data)
    value["file_sha256"][str(path)] = "sha256:" + hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError):
        verify_runtime_resources(value)
