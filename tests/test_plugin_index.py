# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The plugin lists: plugins there are to install, read only when asked.

Every list and zip here is a local file, which the lists allow for exactly
this: nothing in these tests goes online.
"""

import hashlib
import json
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from pycangui.core import plugin_index, plugin_package
from pycangui.core.plugin_index import ListError
from pycangui.ui import plugin_manager
from pycangui.ui.main_window import MainWindow
from pycangui.ui.plugin_manager import ManagePlugins

SOURCE = """
NAME = "{title}"
VERSION = "{version}"
API_VERSION = {api}


def register(app):
    app.add_menu_action("Hello", lambda: None, "")
"""


def make_zip(where: Path, name: str, version: str, api: int = 1, title: str = "") -> Path:
    """A plugin zip, as an author's release would hold it."""
    folder = where / "src" / version / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.py").write_text(
        SOURCE.format(title=title or name.title(), version=version, api=api), encoding="utf-8"
    )
    return plugin_package.pack(folder, where / f"{name}-{version}.zip")


def entry(zip_path: Path, name: str, version: str, api: int = 1, **more) -> dict:
    return {
        "name": name,
        "version": version,
        "api_version": api,
        "url": zip_path.name,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        **more,
    }


def write_list(path: Path, plugins=(), include=(), shape: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"format": shape, "plugins": list(plugins), "include": list(include)}),
        encoding="utf-8",
    )
    return path


# --- reading the lists -------------------------------------------------------------------
def test_a_list_offers_its_plugins_with_their_zips_beside_it(tmp_path):
    zipped = make_zip(tmp_path, "thermistor", "1.2")
    listing = write_list(tmp_path / "plugins.json", [entry(zipped, "thermistor", "1.2", 3)])
    offers, problems = plugin_index.read([str(listing)])
    assert problems == []
    (offer,) = offers
    assert (offer.name, offer.version, offer.api_version) == ("thermistor", "1.2", 3)
    assert offer.url == zipped.resolve().as_uri(), "relative to the list"


def test_a_list_includes_others_and_one_repository_can_offer_several(tmp_path):
    one = make_zip(tmp_path / "repo", "one", "1.0")
    two = make_zip(tmp_path / "repo", "two", "2.0")
    repo = write_list(
        tmp_path / "repo" / "plugins.json", [entry(one, "one", "1.0"), entry(two, "two", "2.0")]
    )
    top = write_list(tmp_path / "index.json", include=[repo.resolve().as_uri()])
    offers, problems = plugin_index.read([str(top)])
    assert problems == []
    assert [o.name for o in offers] == ["one", "two"]


def test_the_first_list_to_offer_a_name_is_the_one_used(tmp_path):
    first = make_zip(tmp_path / "a", "same", "1.0")
    second = make_zip(tmp_path / "b", "same", "9.0")
    a = write_list(tmp_path / "a" / "plugins.json", [entry(first, "same", "1.0")])
    b = write_list(tmp_path / "b" / "plugins.json", [entry(second, "same", "9.0")])
    offers, problems = plugin_index.read([str(a), str(b)])
    assert [o.version for o in offers] == ["1.0"]
    assert len(problems) == 1, "and the second offer is said"


def test_a_list_that_cannot_be_read_does_not_hide_the_others(tmp_path):
    good = make_zip(tmp_path, "good", "1.0")
    listing = write_list(tmp_path / "plugins.json", [entry(good, "good", "1.0")])
    (tmp_path / "broken.json").write_text("not json", encoding="utf-8")
    newer = write_list(tmp_path / "newer.json", shape=plugin_index.FORMAT + 1)
    offers, problems = plugin_index.read(
        [str(tmp_path / "missing.json"), str(tmp_path / "broken.json"), str(newer), str(listing)]
    )
    assert [o.name for o in offers] == ["good"]
    assert len(problems) == 3


def test_an_entry_that_is_not_one_is_skipped_and_said(tmp_path):
    good = make_zip(tmp_path, "good", "1.0")
    listing = write_list(
        tmp_path / "plugins.json",
        [
            entry(good, "good", "1.0"),
            {**entry(good, "nosum", "1.0"), "sha256": "abc"},
            {**entry(good, "../escape", "1.0")},
            {"name": "noversion", "url": "x.zip", "sha256": "0" * 64},
        ],
    )
    offers, problems = plugin_index.read([str(listing)])
    assert [o.name for o in offers] == ["good"]
    assert len(problems) == 3


def test_includes_that_go_round_in_a_circle_are_read_once(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_list(a, include=[b.resolve().as_uri()])
    write_list(b, include=[a.resolve().as_uri()])
    assert plugin_index.read([str(a)]) == ([], [])


def test_plain_http_is_refused():
    with pytest.raises(ListError):
        plugin_index.fetch("http://example.org/plugins.json", plugin_index.LIST_LIMIT)


def test_a_download_must_be_the_file_its_list_describes(tmp_path):
    zipped = make_zip(tmp_path, "checked", "1.0")
    listing = write_list(tmp_path / "plugins.json", [entry(zipped, "checked", "1.0")])
    (offer,) = plugin_index.read([str(listing)])[0]
    got = plugin_index.download(offer, tmp_path / "down")
    assert got.read_bytes() == zipped.read_bytes()

    zipped.write_bytes(zipped.read_bytes() + b"changed")
    with pytest.raises(ListError):
        plugin_index.download(offer, tmp_path / "again")


# --- in Manage plugins ---------------------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path / "home"))
    QSettings().clear()
    monkeypatch.setattr(plugin_index, "INDEX", str(tmp_path / "lists" / "index.json"))
    monkeypatch.setattr(plugin_manager.messages, "warning", lambda *_a, **_k: QMessageBox.Yes)
    win = MainWindow()
    win.show()
    app.processEvents()
    yield win
    win.close()


def offered(tmp_path: Path, version: str = "1.0", api: int = 1, name: str = "thermistor") -> Path:
    zipped = make_zip(tmp_path / "lists", name, version, api, title="Thermistor setup")
    return write_list(tmp_path / "lists" / "index.json", [entry(zipped, name, version, api)])


def wait(app, actions, seconds=10.0) -> None:
    end = time.monotonic() + seconds
    while actions.busy and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_nothing_is_read_until_somebody_checks(app, window, tmp_path):
    offered(tmp_path)
    actions = window.plugin_actions
    assert actions.offers is None, "not at start-up"
    dialog = ManagePlugins(window, actions)
    assert not dialog.offered.item(0).flags() & dialog.offered.item(0).flags().ItemIsSelectable
    dialog.close()


def test_a_plugin_from_the_lists_is_installed_and_then_not_offered_again(app, window, tmp_path):
    offered(tmp_path)
    actions = window.plugin_actions
    dialog = ManagePlugins(window, actions)
    dialog._check()
    wait(app, actions)
    assert [o.name for o in actions.available()] == ["thermistor"]

    dialog.offered.setCurrentRow(0)
    dialog._install_offered()
    wait(app, actions)
    record = window.plugins.loaded["thermistor"]
    assert record.ok, record.error
    assert actions.available() == []
    dialog.close()


def test_update_is_live_only_where_a_list_has_a_newer_one(app, window, tmp_path):
    offered(tmp_path, "1.0")
    actions = window.plugin_actions
    actions.check()
    wait(app, actions)
    actions.install_offer("thermistor")
    wait(app, actions)
    dialog = ManagePlugins(window, actions)
    dialog.installed.setCurrentRow(0)
    assert not dialog.update_button.isEnabled(), "level with the list"

    offered(tmp_path, "1.1")
    dialog._check()
    wait(app, actions)
    dialog.installed.setCurrentRow(0)
    assert dialog.update_button.isEnabled()
    dialog._update()
    wait(app, actions)
    assert window.plugins.loaded["thermistor"].version == "1.1"
    dialog.close()


def test_an_edited_copy_is_kept_when_an_update_replaces_it(app, window, tmp_path):
    offered(tmp_path, "1.0")
    actions = window.plugin_actions
    actions.check()
    wait(app, actions)
    actions.install_offer("thermistor")
    wait(app, actions)
    folder = window.ctx.workspace_dir / "plugins" / "thermistor"
    (folder / "plugin.py").write_text(
        (folder / "plugin.py").read_text(encoding="utf-8") + "\n# mine\n", encoding="utf-8"
    )

    offered(tmp_path, "1.1")
    actions.check()
    wait(app, actions)
    actions.install_offer("thermistor")
    wait(app, actions)
    kept = folder.with_name("_thermistor.bak")
    assert "# mine" in (kept / "plugin.py").read_text(encoding="utf-8")
    assert "# mine" not in (folder / "plugin.py").read_text(encoding="utf-8")


def test_an_untouched_copy_is_replaced_without_a_pile_of_copies(app, window, tmp_path):
    offered(tmp_path, "1.0")
    actions = window.plugin_actions
    actions.check()
    wait(app, actions)
    actions.install_offer("thermistor")
    wait(app, actions)
    offered(tmp_path, "1.1")
    actions.check()
    wait(app, actions)
    actions.install_offer("thermistor")
    wait(app, actions)
    assert not (window.ctx.workspace_dir / "plugins" / "_thermistor.bak").exists()


def test_one_for_a_newer_pycangui_is_listed_and_not_installed(app, window, tmp_path):
    from pycangui.core.plugins import API_VERSION

    offered(tmp_path, api=API_VERSION + 1)
    actions = window.plugin_actions
    actions.check()
    wait(app, actions)
    assert [o.name for o in actions.available()] == ["thermistor"]
    assert actions.install_offer("thermistor") is False
    assert "thermistor" not in window.plugins.loaded


def test_a_zip_holding_another_plugin_than_its_list_says_is_refused(app, window, tmp_path):
    zipped = make_zip(tmp_path / "lists", "impostor", "1.0")
    write_list(tmp_path / "lists" / "index.json", [entry(zipped, "thermistor", "1.0")])
    actions = window.plugin_actions
    actions.check()
    wait(app, actions)
    actions.install_offer("thermistor")
    wait(app, actions)
    assert window.plugins.loaded == {}


def test_a_site_of_your_own_is_read_beside_pycanguis(app, window, tmp_path):
    offered(tmp_path)
    mine = make_zip(tmp_path / "mine", "own", "1.0")
    site = write_list(tmp_path / "mine" / "plugins.json", [entry(mine, "own", "1.0")])
    actions = window.plugin_actions
    actions.set_sites([str(site), "  "])
    assert actions.sites() == [str(site)]
    actions.check()
    wait(app, actions)
    assert sorted(o.name for o in actions.available()) == ["own", "thermistor"]
