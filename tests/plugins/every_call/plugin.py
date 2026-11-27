# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""A plugin that uses every call of the plugin API, for tests/test_plugin_every_call.py.

It imports nothing of pycangui's -- only Qt, the standard library and what its
own lib/ brings -- as a plugin in its own repo has to. What each call gave it
goes in ``app.seen``, for the test to look at. A call added to the API and not
used here fails that test, so this stays the whole API.
"""

import every_call_lib  # from its own lib/
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QLabel, QPushButton

NAME = "Every call"
DESCRIPTION = "Uses every call of the plugin API, for the tests."
VERSION = "1.0"
API_VERSION = 3
REQUIRES = ["python-can", "every-call-lib>=1"]


class Shown(QLabel):
    """A way for a custom pane to show an object: add_field_widget."""


class Provider(QObject):
    """Devices of its own, by a protocol pycangui does not speak: add_device_provider."""

    changed = Signal()

    def devices(self):
        return []

    def device(self, key):
        return None


def register(app):
    seen = app.seen = {"lib": every_call_lib.VERSION, "api": app.api_version}
    note = seen.__setitem__
    seen.update(shown=[], pauses=[], stops=0, closing=0, shutdown=0, done=None, ready=None)

    # a screen of its own
    def build(_name):
        pane = QLabel("every call")
        pane.setFont(app.mono_font())
        seen["button"] = app.with_icon(QPushButton("Start", pane), "start")
        return pane

    def shutdown(_pane):
        seen["shutdown"] += 1

    seen["pane"] = app.add_pane("main", "Every call", build, "right", False, shutdown=shutdown)
    app.on_pane_shown(lambda name, on: seen["shown"].append(on))
    app.on_closing(lambda: note("closing", seen["closing"] + 1))
    app.open_pane("main")
    app.show_panes()

    # somewhere to press
    seen["menu"] = app.add_menu_action("Do it", lambda: note("pressed", "menu"), "Does it")
    seen["toolbar"] = app.add_toolbar_button(
        "Every", lambda: note("pressed", "toolbar"), "Every call", checkable=True
    )

    # joining in
    seen["labeller"] = lambda frame: "every" if frame.arbitration_id == 0x7E5 else None
    app.add_trace_labeller(seen["labeller"])
    app.add_field_widget("every_call", Shown)

    # something slow, and pycangui's own traffic
    app.run_in_background(lambda: 6 * 7, lambda result, error: note("done", (result, error)))
    seen["paused"] = app.paused
    app.on_pause(seen["pauses"].append)
    app.on_stop_all(lambda: note("stops", seen["stops"] + 1))
    app.hold_traffic("every call")
    seen["held"] = app.paused
    app.release_traffic("every call")
    app.hold_traffic("every call, still")  # given back when it is unloaded

    # doing what pycangui does
    seen["emcy"] = app.emcy_text(0x8130)
    seen["poller"] = app.poller()
    seen["devices"] = app.devices()
    seen["device"] = app.device(127)
    app.on_devices_changed(lambda: note("devices", app.devices()))
    seen["provider"] = Provider()
    app.add_device_provider(seen["provider"])

    # its own settings, name, hooks and helper
    app.settings.set("count", app.settings.get("count", 0) + 1)
    seen["count"] = app.settings.get("count")
    app.add_console_name("every_call", app)
    app.add_hooks("every_call", "hooks.py")
    seen["hooked"] = app.hooks.call("every_call", "greeting", "Ada")
    seen["helper"] = app.helper("helper.py", on_ready=lambda port: note("ready", port))

    # saying something
    app.log("loaded")
    app.warn("a warning")
    app.error("an error, on purpose")

    # the live objects, and what pycangui asks before disturbing equipment
    seen["live"] = [
        app.plugin, app.window, app.ctx, app.panes, app.channels, app.bus, app.signals,
        app.canopen, app.uds, app.j1939, app.xcp, app.dbc, app.confirm,
    ]  # fmt: skip
