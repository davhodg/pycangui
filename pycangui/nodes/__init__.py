# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Simulated nodes shipped with pycangui, copied into a workspace on first run.

Every file here is a working example of one protocol, and is meant to be
edited: what arrives in ``<workspace>/nodes/`` is yours, and pycangui never
writes over it again. See ``pycangui/core/simnodes.py`` for the contract, or
the Virtual buses page of the manual for what these are for.

They are also the **demo device**. Connecting the demo channel starts the
four named in ``DEMO`` on it, which is how somebody with no hardware gets
something to look at -- and it means these examples cannot quietly rot,
because what everybody meets on their first run *is* the examples.
"""

#: The nodes the demo device is made of, in the order they are started.
#: Not the gateway: that wants two channels and the demo has one.
DEMO = ("canopen_device", "uds_server", "j1939_engine", "xcp_slave", "ccp_slave")

#: What the whole of it is called, on the channel and in the Event Log. Not
#: "CANopen demo device": it answers several protocols and always did, and a
#: name that mentions only one sends people looking for the others.
DEMO_NAME = "Demo device"
