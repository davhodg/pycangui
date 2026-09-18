# SPDX-License-Identifier: MIT-0
#
# A starting point, copied into your workspace for you to change. It is
# yours to edit, keep private or give away: pycangui claims nothing in it
# and asks for no credit, so what you write here needs nobody's permission.
"""pycangui startup hooks -- edit freely, this file is yours.

Each function below is called by pycangui at a decision point. Return a value
to take over, or return None to let pycangui do the normal thing. A function
that raises is reported in the Event Log pane and ignored. Tools > Reload hooks
picks up changes without a restart.

This one is different from the others in a way worth knowing: the rest answer a
question, and this one is simply told that the window is open. There is nothing
to return. It is the setup somebody does every single morning -- connect the
channels this product uses, load its database, open the panes the job wants --
written down once instead.
"""

from __future__ import annotations

from pycangui.core.hooks import hook


@hook
def on_startup(window, *, ctx) -> None:
    """Called once, after the window is up and everything in it exists.

    Late on purpose: the channels, the protocol managers, the hooks, the
    plugins and the saved layout are all in place by the time this runs, so a
    pane opened here is not undone by the layout arriving over the top of it,
    and a plugin's pane can be opened by name like any other.

    ``window`` is the main window, and through it everything the Python Console
    pane has: ``window.channels``, ``window.bus``, ``window.canopen``,
    ``window.uds``, ``window.j1939``, ``window.xcp``, ``window.dbc``,
    ``window.panes``, ``window.nodes``. Same objects, same names -- what
    works in the console works here.

    **Connecting.**  Use ``window.connect_channel(...)`` rather than reaching
    for the bus directly. It joins a bus exactly as the Connect button does,
    which means a real interface still raises the question about the bitrate --
    once per session, as it would if you had pressed the button yourself. A
    workspace is a folder that gets copied and shared, and one
    that silently joined a live bus on somebody else's bench because they
    opened it would be a bad thing to have built.

    Nothing here can stop pycangui starting: if this raises, the traceback
    goes to the Event Log and the window opens anyway. The tool you would need
    in order to fix a broken startup hook is the one that would not start.

    Examples:

        # Connect the channel this product lives on. Asks about the bitrate
        # for a real adapter; a virtual one never asks.
        # window.connect_channel("CAN 1", "pcan", "PCAN_USBBUS1", 500000)

        # Bring the demo device up with nothing plugged in.
        # window.connect_channel("CAN 1", "virtual", "vcan0", 500000)

        # Load the database this product's messages are described by.
        # window.dbc.load(str(ctx.workspace_dir / "product.dbc"))

        # Stand up the rest of the bus: the devices this product expects to
        # be talking to, so it does not sit in a fault state on the bench.
        # window.nodes.start("canopen_device", "vcan0")

        # Open the panes this job wants, wherever they were left.
        # window.panes.show("canopen")
        # window.panes.show("custom:Battery limits")

        # Say something, so it is obvious the hook ran.
        # ctx.log("Startup hook: ready")
    """
    return None
