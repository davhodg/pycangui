# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
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

    The default hook signatures mention Path and NodeIdentity. Before Python
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


def test_added_imports_go_under_the_licence_header_and_docstring(home, hooks):
    """Put above them, the SPDX line moved down the file to where licence
    scanners do not look, and the docstring became a stray string."""
    import ast

    user_file = workspaces.hooks_dir() / "canopen.py"
    user_file.write_text(
        "# SPDX-License-Identifier: MIT-0\n"
        '"""My hooks."""\n'
        "\n"
        'def node_name(identity, *, ctx):\n    return "mine"\n'
    )
    hooks.update_stubs()

    text = user_file.read_text()
    assert text.startswith("# SPDX-License-Identifier: MIT-0\n")
    assert ast.get_docstring(ast.parse(text)) == "My hooks."
    hooks.reload()
    assert hooks.errors() == {}, "a future import after the docstring is still legal"
    assert hooks.call("canopen", "node_name", IDENT) == "mine"


def test_a_hook_written_for_an_older_signature_is_not_called(home, log):
    """The file loads, the name is there, and the call would fail.

    Left to the call, that is a traceback in the middle of a flash session
    with the default used instead. Found at load, it is a line in the log
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


def test_restoring_a_hook_file_keeps_the_old_one(home, log):
    ctx = Context(log=log.append)
    hooks = Hooks(ctx)
    write_user(home, "def node_name(identity, *, ctx):\n    return 'mine'\n")
    hooks.reload()
    assert hooks.call("canopen", "node_name", IDENT) == "mine"

    kept = hooks.restore("canopen")
    assert kept.name == "canopen.py.bak"
    assert "return 'mine'" in kept.read_text(), "code somebody wrote is not deleted"
    hooks.reload()
    assert hooks.call("canopen", "node_name", IDENT) is None  # the default is back
    assert "def eds_for_node" in (workspaces.hooks_dir() / "canopen.py").read_text()


def test_restoring_twice_does_not_eat_the_first_copy(home, log):
    ctx = Context(log=log.append)
    hooks = Hooks(ctx)
    write_user(home, "def node_name(identity, *, ctx):\n    return 'first'\n")
    assert hooks.restore("canopen").name == "canopen.py.bak"
    write_user(home, "def node_name(identity, *, ctx):\n    return 'second'\n")
    second = hooks.restore("canopen")

    assert second.name == "canopen.py.bak2"
    assert "first" in (workspaces.hooks_dir() / "canopen.py.bak").read_text()
    assert "second" in second.read_text()


def test_an_untouched_hook_file_keeps_up_and_an_edited_one_is_left(home, log):
    """What was copied, and nobody has touched, is still pycangui's."""
    from pycangui.core.supplied import fingerprint

    ctx = Context(log=log.append)
    Hooks(ctx)
    folder = workspaces.hooks_dir()
    shipped_canopen = (folder / "canopen.py").read_bytes()
    older = b"from pycangui.core.hooks import hook\n# an older canopen.py\n"
    (folder / "canopen.py").write_bytes(older)
    (folder / "uds.py").write_text("# mine\n", encoding="utf-8")
    record = ctx.settings.get("supplied.hooks")
    record["copied"]["canopen.py"] = fingerprint(older)
    # Copied from an older pycangui and edited since, so a newer one does ship.
    record["copied"]["uds.py"] = fingerprint(b"# an older uds.py\n")
    ctx.settings.set("supplied.hooks", record)
    log.clear()

    Hooks(ctx)
    assert (folder / "canopen.py").read_bytes() == shipped_canopen
    assert (folder / "uds.py").read_text(encoding="utf-8").startswith("# mine")
    assert any("hooks/canopen.py updated" in line for line in log)
    assert any("hooks/uds.py" in line and "left as it is" in line for line in log)


@pytest.mark.parametrize(
    "opening",
    [
        '"""My UDS hooks."""\n\nfrom __future__ import annotations\n\n',
        "# SPDX-License-Identifier: MIT-0\n#\n# yours\n\nfrom __future__ import annotations\n\n",
        '"""My UDS hooks."""\n\n',
        "",
    ],
    ids=["docstring then future", "comments then future", "docstring only", "nothing"],
)
def test_added_imports_never_go_above_a_future_import(opening):
    """Python takes ``from __future__`` only as a file's first statement, so an
    import put above one breaks the file -- whatever else the file opens with."""
    import ast

    from pycangui.core import hooks as hooks_module

    hook_source = (
        "from pycangui.core.hooks import hook\n\n\n@hook\ndef x(*, ctx):\n    return None\n"
    )
    text = opening + hook_source
    grown = hooks_module._with_imports_for("uds", text)
    compile(grown, "uds.py", "exec")  # raises if the future import is not first

    body = ast.parse(grown).body
    statements = body[1:] if isinstance(body[0], ast.Expr) else body
    assert isinstance(statements[0], ast.ImportFrom) and statements[0].module == "__future__"
    assert grown.count("from __future__ import annotations") == 1


def test_an_older_uds_file_with_its_own_future_import_gains_the_new_hooks(home, log):
    """The file an older pycangui supplied: a docstring, then its own future
    import. Adding the newer hooks used to be refused as breaking it."""
    Hooks(Context(log=log.append))
    path = workspaces.hooks_dir() / "uds.py"
    path.write_text(
        '"""pycangui UDS hooks -- edit freely, this file is yours."""\n\n'
        "from __future__ import annotations\n\n"
        "from pycangui.core.hooks import hook\n\n\n"
        "@hook\n"
        "def security_key(level: int, seed: bytes, *, ctx) -> bytes | None:\n"
        "    return bytes(b ^ 0xFF for b in seed)\n",
        encoding="utf-8",
    )
    log.clear()

    hooks = Hooks(Context(log=log.append))
    added = hooks.update_stubs()
    text = path.read_text(encoding="utf-8")

    assert "did_label" in added.get("uds", []), "the newer hooks arrived"
    assert not any("would have broken it" in line for line in log), log
    compile(text, "uds.py", "exec")
    assert "return bytes(b ^ 0xFF for b in seed)" in text, "and what was there is untouched"


def test_an_untouched_file_is_not_offered_as_edited(home, log):
    """What the dialog greys out: a file nobody has been near."""
    ctx = Context(log=log.append)
    hooks = Hooks(ctx)
    assert hooks.edited() == []
    write_user(home, "def node_name(identity, *, ctx):\n    return 'mine'\n")
    assert hooks.edited() == ["canopen"]


# --- what ctx carries --------------------------------------------------------------------
def test_ctx_carries_the_managers_the_shipped_examples_use(app, home):
    """A hook is handed ctx and nothing else, so an example that reaches
    for ctx.canopen has to find it there. The shipped files show exactly
    that, and it was not true until the window put them on."""
    from pycangui.ui.main_window import MainWindow

    window = MainWindow()
    try:
        assert window.ctx.canopen is window.canopen
        assert window.ctx.uds is window.uds
        assert window.ctx.j1939 is window.j1939
        assert window.ctx.xcp is window.xcp
        assert window.ctx.channels is window.channels
        assert window.ctx.bus is window.bus
    finally:
        window.close()


def test_a_context_without_a_window_says_so_rather_than_missing_the_name(app, home):
    """A script or a test builds its own Context, and there is nothing for
    these to point at: None is an answer, AttributeError is a traceback."""
    ctx = Context(log=print)
    assert ctx.canopen is None
    assert (ctx.uds, ctx.j1939, ctx.xcp, ctx.channels, ctx.bus) == (None,) * 5
