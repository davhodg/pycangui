# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Opening a measurement file and getting its signals onto the plot.

Recording has always written raw CAN, and the manual said so apologetically:
"decoding it again elsewhere to get back what is already on screen here".
This is the other direction -- signals somebody else decoded, brought in to
look at.

The awkward parts are not the reading.  They are that an imported file is
*finite* where a live signal is endless, and that it sits at its own times
rather than at the clock this window is counting on: both of which the plot
was built the other way round for.
"""

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from pycangui.core.signals import MAX_SAMPLES, SignalHub
from pycangui.ui.main_window import MainWindow


def settle(app, times=5):
    for _ in range(times):
        app.processEvents()


# --- a finite series in a hub built for endless ones -------------------------------------
def test_an_imported_series_is_kept_whole():
    """A live signal is trimmed to the newest samples because the old ones
    stop mattering.  Trimming an imported one the same way loses its
    *beginning*, which for something being analysed is the half people are
    usually looking for."""
    hub = SignalHub()
    n = MAX_SAMPLES + 5_000
    hub.set_series("drive.mf4", "EngineSpeed", np.arange(n, dtype=float), np.arange(n), "rpm")

    series = hub.get("drive.mf4/EngineSpeed")
    assert len(series.times) == n, "nothing trimmed off the front"
    assert series.times[0] == 0.0
    assert series.unit == "rpm"


def test_importing_the_same_file_twice_replaces_rather_than_appends():
    hub = SignalHub()
    for _ in range(2):
        hub.set_series("drive.mf4", "Speed", [1.0, 2.0], [10.0, 20.0])
    assert len(hub.get("drive.mf4/Speed").times) == 2


def test_a_file_can_be_forgotten_again():
    """A signals list that only ever grows is one people stop opening."""
    hub = SignalHub()
    hub.set_series("drive.mf4", "Speed", [1.0], [10.0])
    hub.set_series("drive.mf4", "Torque", [1.0], [5.0])
    hub.push("Live", "RPM", 1.0, 900.0)

    assert hub.forget_group("drive.mf4") == 2
    assert hub.keys() == ["Live/RPM"], "the live one is untouched"
    assert hub.forget_group("drive.mf4") == 0, "and again is not an error"


def test_groups_are_listed_in_the_order_they_arrived():
    hub = SignalHub()
    hub.push("Live", "RPM", 1.0, 900.0)
    hub.set_series("drive.mf4", "Speed", [1.0], [10.0])
    assert hub.groups() == ["Live", "drive.mf4"]


# --- and a plot built around "now" ---------------------------------------------------------
@pytest.fixture
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("PYCANGUI_HOME", str(tmp_path))
    QSettings().clear()
    win = MainWindow()
    win.show()
    settle(app)
    yield win
    win.close()


def test_following_shows_only_the_last_few_seconds(app, window):
    """Which is what makes a live plot cheap, and is why imported data needs
    the other behaviour."""
    hub = window.signals
    now = window.bus.now()
    for i in range(100):
        hub.push("Live", "RPM", now - 50 + i * 0.5, float(i))
    key = "Live/RPM"
    plot = window.plot
    plot.set_plotted(key, True)
    plot.follow.setChecked(True)
    plot.window_s.setValue(10.0)
    plot._redraw()

    times, _values = plot._curves[key].getData()
    assert len(times) < 100, "the window is doing something"
    assert times.min() >= now - 11


def test_data_from_a_file_is_invisible_while_following_and_appears_when_not(app, window):
    """The bug this exists to prevent: a file recorded at its own times lies
    outside any window measured back from now, so following shows nothing and
    the pane looks broken when it is behaving."""
    t = np.arange(235.0, 240.0, 0.1)
    key = window.signals.set_series("drive.mf4", "Speed", t, np.arange(t.size))
    plot = window.plot
    plot.set_plotted(key, True)

    plot.follow.setChecked(True)
    plot.window_s.setValue(10.0)
    plot._redraw()
    low, high = plot.plot.viewRange()[0]
    assert not (low <= 235.0 <= high), f"the file is off screen at {low}..{high}"

    plot.follow.setChecked(False)
    plot._fit()
    low, high = plot.plot.viewRange()[0]
    assert low <= 235.0 and high >= 239.0, f"unticked and fitted, it is on screen: {low}..{high}"
    times, _v = plot._curves[key].getData()
    assert len(times) == t.size, "and all of it is plotted"


def test_fit_goes_to_where_the_data_actually_is(app, window):
    """Hunting for a file recorded at 235 seconds by dragging is no way to
    find anything."""
    t = np.arange(235.0, 240.0, 0.1)
    key = window.signals.set_series("drive.mf4", "Speed", t, np.arange(t.size))
    plot = window.plot
    plot.set_plotted(key, True)
    plot._fit()
    settle(app)

    low, high = plot.plot.viewRange()[0]
    assert 230 < low < 236 and 239 < high < 245, (low, high)
    assert not plot.follow.isChecked(), "fitting and following are contradictory"


# --- the way in --------------------------------------------------------------------------------
def test_the_menu_offers_it_beside_the_export(app, window):
    entries = [a.text() for a in window.menuBar().actions() if a.text() == "&File"]
    assert entries, "no File menu"
    file_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "&File")
    texts = [a.text() for a in file_menu.actions()]
    assert "Import signals..." in texts
    assert texts.index("Import signals...") < texts.index("Export signals...")


def test_a_file_that_is_not_an_mdf_is_refused_before_the_library_is_wanted(
    app, window, tmp_path, monkeypatch
):
    """Picking the wrong file should cost a sentence, not a sixty megabyte
    download and then a sentence."""
    from pycangui.ui import folders, import_signals

    notes = tmp_path / "notes.txt"
    notes.write_text("not a measurement\n", encoding="utf-8")
    monkeypatch.setattr(folders, "open_file", lambda *a, **k: str(notes))
    monkeypatch.setattr(
        import_signals, "ensure_available", lambda *a, **k: pytest.fail("asked too early")
    )
    window._import_signals()
    assert "not an MDF file" in window.log.toPlainText()


def test_reading_a_real_measurement_puts_it_under_the_file_name(app, window, tmp_path):
    """End to end, through the same call the menu makes."""
    pytest.importorskip("asammdf", reason="MDF support is the pycangui[mf4] extra")
    from asammdf import MDF, Signal

    t = np.arange(0.0, 5.0, 0.01)
    path = tmp_path / "drive.mf4"
    with MDF(version="4.10") as m:
        m.append([Signal(np.linspace(800, 3200, t.size), t, name="EngineSpeed", unit="rpm")])
        m.save(path, overwrite=True)

    window._read_signals(path, ["EngineSpeed"])
    key = "drive/EngineSpeed"
    assert key in window.signals.keys()
    series = window.signals.get(key)
    assert len(series.times) == t.size
    assert series.unit == "rpm"
    assert "Imported 1 signal" in window.log.toPlainText()


def test_asking_for_a_signal_the_file_does_not_have_is_said_rather_than_silent(
    app, window, tmp_path
):
    pytest.importorskip("asammdf", reason="MDF support is the pycangui[mf4] extra")
    from asammdf import MDF, Signal

    t = np.arange(0.0, 1.0, 0.1)
    path = tmp_path / "drive.mf4"
    with MDF(version="4.10") as m:
        m.append([Signal(np.arange(t.size, dtype=float), t, name="Real")])
        m.save(path, overwrite=True)

    window._read_signals(path, ["Real", "Imaginary"])
    assert "1 held nothing readable" in window.log.toPlainText()
