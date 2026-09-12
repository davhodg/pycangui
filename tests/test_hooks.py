"""Hook loader behaviour: copy-on-first-run, override, fallback, isolation, stubs."""

from pathlib import Path

import pytest

from pycangui.canopen import NodeIdentity
from pycangui.core import workspaces
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
    (workspaces.hooks_dir() / "canopen.py").write_text(
        "from pycangui.core.hooks import hook\n" + body, encoding="utf-8"
    )


def test_registry_has_canopen_hooks():
    assert {"node_name", "eds_for_node"} <= set(registry()["canopen"])


def test_first_run_copies_defaults(home, hooks):
    user_file = workspaces.hooks_dir() / "canopen.py"
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
    assert hooks.call("canopen", "eds_for_node", IDENT) == workspaces.eds_dir() / "a.eds"


def test_update_stubs_appends_missing_without_touching_existing(home, hooks):
    write_user(home, "def node_name(identity, *, ctx):\n    return 'mine'\n")
    added = hooks.update_stubs()
    # everything the module defines except the one the user already wrote
    expected = sorted(set(registry()["canopen"]) - {"node_name"})
    assert sorted(added["canopen"]) == expected
    text = (workspaces.hooks_dir() / "canopen.py").read_text()
    assert "return 'mine'" in text and "def eds_for_node" in text
    hooks.reload()
    assert hooks.call("canopen", "node_name", IDENT) == "mine"
    assert hooks.update_stubs() == {}


def test_update_stubs_adds_the_imports_the_new_code_needs(home, hooks):
    """A stub pasted into a bare user file must not break it.

    The default hook signatures mention Path and NodeIdentity.  Before Python
    3.14 annotations are evaluated as the function is defined, so without the
    imports (and the future import) the whole file would fail to load and every
    hook in it would silently fall back to the default.
    """
    import ast

    user_file = workspaces.hooks_dir() / "canopen.py"
    user_file.write_text('def node_name(identity, *, ctx):\n    return "mine"\n')
    hooks.reload()
    hooks.update_stubs()

    text = user_file.read_text()
    assert ast.parse(text)  # still valid Python
    lines = text.splitlines()
    assert lines[0] == "from __future__ import annotations"  # must come first
    header = "\n".join(lines[:6])
    assert "from pathlib import Path" in header
    assert "from pycangui.canopen import NodeIdentity" in header
    assert "from pycangui.core.hooks import hook" in header
    assert 'return "mine"' in text  # the user's own code is untouched

    hooks.reload()
    assert hooks.errors() == {}  # the file really does load
    assert hooks.call("canopen", "node_name", IDENT) == "mine"
    assert hooks.update_stubs() == {}  # and running it again changes nothing


def test_a_hook_written_for_an_older_signature_is_not_called(home, log):
    """The file loads, the name is there, and the call would fail.

    Left to the call, that is a traceback in the middle of a flash session
    with the default used instead.  Found at load, it is a line in the log
    before anything has happened.
    """
    ctx = Context(log=log.append)
    Hooks(ctx)
    write_user(home, "def node_name(identity):\n    return 'mine'\n")  # no ctx
    hooks = Hooks(ctx)

    assert not hooks.is_user_defined("canopen", "node_name")
    assert hooks.call("canopen", "node_name", IDENT) is None  # the default ran
    assert hooks.mismatches() == {("canopen", "node_name"): "it does not take ctx"}
    said = "\n".join(log)
    assert "node_name(identity)" in said, "what they wrote"
    assert "node_name(identity, *, ctx)" in said, "what pycangui calls"


def test_a_hook_that_takes_anything_is_left_alone(home, log):
    ctx = Context(log=log.append)
    write_user(home, "def node_name(*args, **kwargs):\n    return 'mine'\n")
    hooks = Hooks(ctx)
    assert hooks.mismatches() == {}
    assert hooks.call("canopen", "node_name", IDENT) == "mine"


def test_a_signature_this_version_changed_says_so(home, log):
    """Whose fault it is decides what the reader does next."""
    ctx = Context(log=log.append)
    Hooks(ctx)
    write_user(home, "def node_name(identity):\n    return 'mine'\n")
    known = ctx.settings.get("hooks.known")
    known["canopen"]["node_name"] = "node_name(identity)"  # what they wrote it against
    ctx.settings.set("hooks.known", known)

    Hooks(Context(log=log.append))
    assert any("this version of pycangui changed it" in line for line in log)


def test_hooks_a_new_version_adds_arrive_by_themselves(home, log):
    """Keeping up with pycangui is pycangui's job, not a menu entry."""
    ctx = Context(log=log.append)
    Hooks(ctx)
    write_user(home, "def node_name(identity, *, ctx):\n    return 'mine'\n")
    known = ctx.settings.get("hooks.known")
    known["canopen"] = {"node_name": known["canopen"]["node_name"]}  # the rest are new
    ctx.settings.set("hooks.known", known)

    hooks = Hooks(Context(log=log.append))
    text = (workspaces.hooks_dir() / "canopen.py").read_text()
    assert "def eds_for_node" in text
    assert "return 'mine'" in text, "and the hand-written one is untouched"
    assert hooks.call("canopen", "node_name", IDENT) == "mine"


def test_a_hook_deleted_on_purpose_stays_deleted(home, log):
    """Which is why what arrives is decided by what is new, not by what
    is missing -- otherwise every startup would put it back."""
    ctx = Context(log=log.append)
    Hooks(ctx)
    write_user(home, "def node_name(identity, *, ctx):\n    return 'mine'\n")

    Hooks(Context(log=log.append))
    text = (workspaces.hooks_dir() / "canopen.py").read_text()
    assert "def eds_for_node" not in text


def test_a_file_that_would_break_is_put_back_as_it_was(home, log, monkeypatch):
    """This runs unattended at startup, so it has to be able to undo itself."""
    from pycangui.core import hooks as hooks_module

    ctx = Context(log=log.append)
    Hooks(ctx)
    write_user(home, "def node_name(identity, *, ctx):\n    return 'mine'\n")
    before = (workspaces.hooks_dir() / "canopen.py").read_text()
    monkeypatch.setattr(hooks_module, "_fails_to_load", lambda module, path: "NameError: nope")

    hooks = Hooks(Context(log=log.append))
    assert hooks.update_stubs() == {}, "nothing was added"
    assert (workspaces.hooks_dir() / "canopen.py").read_text() == before
    assert any("left as it was" in line for line in log)
