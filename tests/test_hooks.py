"""Hook loader behaviour: copy-on-first-run, override, fallback, isolation, stubs."""

from pathlib import Path

import pytest

from pycangui.canopen import NodeIdentity
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks, registry

IDENT = NodeIdentity(node_id=5, vendor_id=0x1A2, product_code=0x1234, revision=0x10000)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def log():
    lines: list[str] = []
    return lines


@pytest.fixture
def hooks(home, log):
    return Hooks(Context(log=log.append))


def write_user(home: Path, body: str) -> None:
    (home / "hooks" / "canopen.py").write_text(
        "from pycangui.core.hooks import hook\n" + body, encoding="utf-8"
    )


def test_registry_has_canopen_hooks():
    assert {"node_name", "eds_for_node"} <= set(registry()["canopen"])


def test_first_run_copies_defaults(home, hooks):
    user_file = home / "hooks" / "canopen.py"
    assert user_file.exists()
    assert "def eds_for_node" in user_file.read_text()
    assert hooks.is_user_defined("canopen", "eds_for_node")  # the copy defines it
    assert hooks.call("canopen", "node_name", IDENT) is None


def test_user_override_wins(home, hooks):
    write_user(home, "def node_name(identity, *, ctx):\n    return f'Drive {identity.node_id}'\n")
    hooks.reload()
    assert hooks.is_user_defined("canopen", "node_name")
    assert hooks.call("canopen", "node_name", IDENT) == "Drive 5"


def test_none_falls_through_to_default(home, hooks):
    write_user(home, "def eds_for_node(identity, *, ctx):\n    return None\n")
    hooks.reload()
    assert hooks.call("canopen", "eds_for_node", IDENT) is None  # default also None


def test_exception_is_isolated_and_logged_once(home, hooks, log):
    write_user(home, "def node_name(identity, *, ctx):\n    raise RuntimeError('boom')\n")
    hooks.reload()
    assert hooks.call("canopen", "node_name", IDENT) is None
    assert hooks.call("canopen", "node_name", IDENT) is None
    assert sum("boom" in line for line in log) == 1


def test_syntax_error_file_uses_defaults(home, hooks, log):
    write_user(home, "def node_name(identity, *, ctx)\n    return 'x'\n")  # missing colon
    hooks.reload()
    assert hooks.errors().keys() == {"canopen"}
    assert hooks.call("canopen", "node_name", IDENT) is None
    assert any("failed to load" in line for line in log)


def test_hook_receives_ctx(home, hooks):
    write_user(home, "def eds_for_node(identity, *, ctx):\n    return ctx.eds_dir / 'a.eds'\n")
    hooks.reload()
    assert hooks.call("canopen", "eds_for_node", IDENT) == home / "eds" / "a.eds"


def test_update_stubs_appends_missing_without_touching_existing(home, hooks):
    write_user(home, "def node_name(identity, *, ctx):\n    return 'mine'\n")
    added = hooks.update_stubs()
    # everything the module defines except the one the user already wrote
    expected = sorted(set(registry()["canopen"]) - {"node_name"})
    assert sorted(added["canopen"]) == expected
    text = (home / "hooks" / "canopen.py").read_text()
    assert "return 'mine'" in text and "def eds_for_node" in text
    hooks.reload()
    assert hooks.call("canopen", "node_name", IDENT) == "mine"
    assert hooks.update_stubs() == {}
