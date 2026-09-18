# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Reading measurement files: MDF, and the MF4 that is its current version.

A CAN log holds frames and an MDF holds signals, and the confusion between
those two is most of what this reader exists to clear up. A measurement tool
opening a bus log shows a wall of frame plumbing and no measurements; a CAN
tool opening a measurement export shows nothing at all. Neither file is
broken, and both look it.

The files here are made rather than committed. A real export is full of the
quirks that matter -- empty groups, one channel group per signal, channels
that claim samples and read back with none -- but a real export is also
somebody's product data, so the shapes are reproduced and the data is not.
"""

import numpy as np
import pytest

from pycangui.core import mdf

asammdf = pytest.importorskip("asammdf", reason="MDF support is the pycangui[mf4] extra")

DURATION_S = 10.0
RATE_HZ = 100.0


def shaped_signals():
    """Signals whose shape is recognisable, so a wrong read is obvious.

    A ramp, a sine, a staircase and a square: each wrong in a different and
    visible way if the times and the values ever come apart.
    """
    from asammdf import Signal

    t = np.arange(0.0, DURATION_S, 1.0 / RATE_HZ)
    return [
        Signal(np.linspace(800.0, 3200.0, t.size), t, name="EngineSpeed", unit="rpm"),
        Signal(90.0 + 12.0 * np.sin(2 * np.pi * t / 4.0), t, name="CoolantTemp", unit="degC"),
        Signal(np.floor(t / 2.0) * 20.0, t, name="VehicleSpeed", unit="km/h"),
        Signal(((t % 2.0) < 1.0).astype(np.uint8), t, name="PumpEnable", unit=""),
    ]


@pytest.fixture(scope="module")
def measurement(tmp_path_factory):
    """Decoded signals: what a measurement tool records."""
    from asammdf import MDF

    path = tmp_path_factory.mktemp("mdf") / "measurement.mf4"
    with MDF(version="4.10") as m:
        m.append(shaped_signals(), comment="pycangui test measurement")
        m.save(path, overwrite=True)
    return path


@pytest.fixture(scope="module")
def old_measurement(tmp_path_factory):
    """The same, as MDF 3 -- which a great deal of archived data still is."""
    from asammdf import MDF

    path = tmp_path_factory.mktemp("mdf") / "measurement.mdf"
    with MDF(version="3.30") as m:
        m.append(shaped_signals())
        m.save(path, overwrite=True)
    return path


@pytest.fixture(scope="module")
def bus_log(tmp_path_factory):
    """Raw frames: what a CAN logger records.

    Written through python-can's own MF4 writer rather than by hand, because
    that is the layout a reader will actually meet and one we invented would
    prove nothing.
    """
    import can

    path = tmp_path_factory.mktemp("mdf") / "buslog.mf4"
    with can.MF4Writer(str(path)) as writer:
        for i in range(100):
            writer.on_message_received(
                can.Message(timestamp=i / 20.0, arbitration_id=0x123, data=bytes([i % 256] * 8))
            )
    return path


@pytest.fixture(scope="module")
def plumbing_only(tmp_path_factory):
    """Frame fields as separate scalar channels, and no payload.

    The layout a real export produced: CAN_DataFrame.ID and DLC as ordinary
    signals, with the data bytes dropped. It has the shape of a bus log and
    none of the substance.
    """
    from asammdf import MDF, Signal

    path = tmp_path_factory.mktemp("mdf") / "plumbing.mf4"
    t = np.arange(0.0, 1.0, 0.1)
    with MDF(version="4.10") as m:
        m.append(
            [
                Signal(np.full(t.size, 0x123, dtype=np.uint32), t, name="CAN_DataFrame.ID"),
                Signal(np.full(t.size, 8, dtype=np.uint8), t, name="CAN_DataFrame.DLC"),
                Signal(np.arange(t.size, dtype=float), t, name="RealSignal", unit="V"),
            ]
        )
        m.save(path, overwrite=True)
    return path


# --- what a file is, before reading any of it ------------------------------------------
def test_the_first_bytes_say_whether_it_is_one_at_all(measurement, tmp_path):
    """Checked before the library is asked for, so that picking the wrong file
    is a sentence rather than sixty megabytes and then a sentence."""
    assert mdf.looks_like_mdf(measurement)
    not_one = tmp_path / "notes.txt"
    not_one.write_text("this is not a measurement\n", encoding="utf-8")
    assert not mdf.looks_like_mdf(not_one)
    assert not mdf.looks_like_mdf(tmp_path / "does-not-exist.mf4")


def test_an_unfinalised_file_is_recognised(tmp_path):
    """A logger that lost power, or one that writes this way by design. Most
    tools refuse one outright, so it is worth saying rather than being the
    difference nobody can see."""
    half = tmp_path / "half.mf4"
    half.write_bytes(b"UnFinMF " + b"\x00" * 64)
    assert mdf.unfinalised(half)
    assert mdf.looks_like_mdf(half), "still an MDF, just not a closed one"


def test_a_measurement_says_what_it_holds(measurement):
    got = mdf.summarise(measurement)
    assert got.version == "4.10"
    assert got.channels >= 4
    assert got.samples > 0
    assert not got.has_frames, "signals, not frames"


def test_a_bus_log_is_told_apart_from_a_measurement(bus_log):
    """The distinction the whole module exists for."""
    got = mdf.summarise(bus_log)
    assert got.has_frames, "payload bytes are in there, so it could be replayed"


def test_frame_metadata_without_the_payload_is_not_frames(plumbing_only):
    """A real export can carry CAN_DataFrame.ID and DLC and no DataBytes. It
    has the shape of a bus log and none of the substance: nothing in it can be
    replayed, and calling it a bus log would send somebody to the wrong pane."""
    assert not mdf.summarise(plumbing_only).has_frames


# --- listing what is in it -----------------------------------------------------------------
def test_channels_are_listed_without_reading_their_samples(measurement):
    """A real file holds thousands, and reading them all to find out what is
    there would answer from gigabytes a question the header can answer."""
    names = {c.name for c in mdf.channels(measurement)}
    assert {"EngineSpeed", "CoolantTemp", "VehicleSpeed", "PumpEnable"} <= names
    assert "t" not in names and "time" not in names, "the time base is not a signal"


def test_a_channel_says_its_unit_and_how_many_samples(measurement):
    by_name = {c.name: c for c in mdf.channels(measurement)}
    assert by_name["EngineSpeed"].unit == "rpm"
    assert by_name["EngineSpeed"].samples == int(DURATION_S * RATE_HZ)


def test_frame_plumbing_is_marked_rather_than_hidden(bus_log):
    """Somebody who wants the ids can have them; a signals list with three
    hundred of them in it is a signals list nobody can use."""
    listed = mdf.channels(bus_log)
    assert any(c.bus_metadata for c in listed)
    assert all(c.name.startswith(mdf.BUS_PREFIXES) for c in listed if c.bus_metadata)


# --- reading it ------------------------------------------------------------------------------
def test_the_values_come_back_as_they_went_in(measurement):
    series = {s.name: s for s in mdf.read(measurement)}
    ramp = series["EngineSpeed"]
    assert len(ramp) == int(DURATION_S * RATE_HZ)
    assert ramp.unit == "rpm"
    assert ramp.values[0] == pytest.approx(800.0)
    assert ramp.values[-1] == pytest.approx(3200.0)
    assert np.all(np.diff(ramp.values) > 0), "a ramp only goes up"


def test_times_and_values_stay_together(measurement):
    """The failure that would be hardest to see on a plot: right shape, wrong
    time base."""
    sine = {s.name: s for s in mdf.read(measurement)}["CoolantTemp"]
    assert sine.times[0] == pytest.approx(0.0)
    assert sine.times[-1] == pytest.approx(DURATION_S - 1 / RATE_HZ)
    # A 4-second period sampled at 100 Hz peaks one second in.
    assert sine.values[100] == pytest.approx(102.0, abs=0.5)


def test_only_the_named_signals_are_read(measurement):
    """A file with thousands of channels is not something to load whole on the
    chance that six of them were wanted."""
    got = mdf.read(measurement, names=["EngineSpeed"])
    assert [s.name for s in got] == ["EngineSpeed"]


def test_a_bus_log_yields_no_signals_at_all(bus_log):
    """Not a bug: a log written by python-can holds one composed record per
    frame -- id, length and payload together -- which is a frame rather than a
    measurement. There is nothing in it to plot, and inventing something
    would be worse than saying so."""
    assert mdf.read(bus_log) == []
    assert mdf.read(bus_log, include_bus_metadata=True) == []


def test_frame_plumbing_is_left_out_unless_it_is_asked_for(plumbing_only):
    """Some tools write the frame fields as separate scalar channels rather
    than one record. Those *can* be plotted -- an id against time is a real
    question -- so they are offered, out of the way."""
    plain = {s.name for s in mdf.read(plumbing_only)}
    assert not any(n.startswith(mdf.BUS_PREFIXES) for n in plain)

    with_it = {s.name for s in mdf.read(plumbing_only, include_bus_metadata=True)}
    assert "CAN_DataFrame.ID" in with_it


def test_mdf_version_3_reads_the_same_way(old_measurement):
    """Which is the argument for the library over python-can's reader: one
    dependency covers MDF 2, 3 and 4, and archived data is full of 3."""
    assert mdf.summarise(old_measurement).version.startswith("3")
    series = {s.name: s for s in mdf.read(old_measurement)}
    assert series["EngineSpeed"].values[-1] == pytest.approx(3200.0)


def test_a_signal_with_no_samples_is_not_returned(measurement):
    """Real exports produce them -- a channel whose group claims cycles and
    reads back with nothing. Plotted, it is a curve with no points and a name
    in the legend suggesting otherwise."""
    assert all(len(s) > 0 for s in mdf.read(measurement))


def test_text_channels_are_left_out(measurement):
    """There is no honest way to plot a string."""
    from asammdf import MDF, Signal

    path = measurement.with_name("with-text.mf4")
    t = np.arange(0.0, 1.0, 0.1)
    with MDF(version="4.10") as m:
        m.append(
            [
                Signal(np.arange(t.size, dtype=float), t, name="Number"),
                Signal(
                    np.array([b"on"] * t.size, dtype="S2"),
                    t,
                    name="Words",
                    encoding="latin-1",
                ),
            ]
        )
        m.save(path, overwrite=True)
    assert [s.name for s in mdf.read(path)] == ["Number"]
