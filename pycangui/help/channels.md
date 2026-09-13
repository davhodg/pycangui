[&larr; Contents](manual.md)

# CAN adapters and channels

## Channels

pycangui talks to several CAN buses at once -- a second port on a multi-channel
adapter, or a second adapter entirely.  Use **+** on the toolbar to add a
channel, then give it its own interface, bitrate and connection; each channel's
settings are remembered by name.

Pick the interface and press **Detect**: pycangui asks it which adapters are
attached and lists them, so the channel is chosen rather than guessed (it is
`can0` on socketcan, `PCAN_USBBUS1` on a PEAK, and plain `0` on an IXXAT).
Detection also runs by itself when you change interface.  It only enumerates
adapters -- nothing is transmitted and no bitrate is applied.

Channel numbers belong to an adapter, so **two identical dongles both offer
channels 0 and 1**.  The list shows each one's hardware id or serial number to
tell them apart, and the whole configuration is used to open the bus -- picking
the second dongle really does connect to the second dongle, not to whichever
the driver enumerated first.  Which one you chose is saved with the channel and
shown in its description.

Where an interface cannot enumerate, opening the list still offers something
rather than nothing: the conventional names for that interface, the default
the backend declares in its own signature (PCAN says `PCAN_USBBUS1`, NI-XNET
says `CAN1`), and for the serial adapters the serial ports actually present.
The box stays editable, so anything typed in is kept.  An interface with no
channel at all shows it empty and greyed.

The status bar shows each channel's **bus load** -- an estimate from the
frames seen and the configured bitrate, including nominal bit stuffing --
beside a coloured dot for how its controller is doing (see *Controller state
and bus off* below).  The trace's *Channel* column says which bus a frame
came from.

The trace, the recorder and the decoders always see **every** connected
channel, on one shared clock, so an ECU forwarding messages between two buses
can be watched from both sides with comparable timestamps.  The channel picked
in the toolbar is the one the protocol panes ([CANopen](canopen.md),
[UDS](uds.md), [J1939](j1939.md), [XCP](xcp.md)) work with; switching channel looks to them like a disconnect and a reconnect.

## Bitrates and CAN FD

Bitrates run from 50 kbit/s to 1 Mbit/s -- 50 and 100 are ordinary on
machinery and marine buses, where a long backbone costs more than speed.
Ticking **FD** adds a **Data** rate beside it, since the arbitration phase
still runs at the bitrate on the left.  python-can has no way to ask an
adapter which rates it supports, and no common way to set an FD data rate
either: only the IXXAT and Vector backends take a `data_bitrate`, socketcan
takes `fd=True` and gets its data rate from `ip link`, and PCAN, Kvaser and
slcan take neither -- FD is a `can.BitTimingFd` to them, which needs the
controller's clock frequency and cannot be worked out from a bitrate.  Where
the choice cannot be sent, the Event Log says so rather than letting the
channel open as classic CAN with FD ticked on screen.

On an FD channel the [**UDS**](uds.md) pane offers **CAN-DL** beside the transport:
how many bytes go in one ISO-TP frame.  Eight is all a classic bus can
carry, and the FD lengths -- 12, 16, 20, 24, 32, 48, 64 -- are what makes
running UDS over FD worth the trouble, since at 64 there are eight times
fewer flow control rounds.  **BRS** beside it switches the data phase to
the faster rate; without it an FD frame runs end to end at the arbitration
bitrate and the data rate never gets used.  Both follow the channel rather
than the box that was ticked: a channel that opened classic offers 8 and
nothing else.

## Controller state and bus off

A CAN controller counts the errors it is involved in, and the count decides
how far it is still allowed to take part.  Each channel in the status bar has
a dot for where it stands:

| Dot | State | What it means |
|-----|-------|---------------|
| Grey | down | Not connected. |
| Green | error active | Taking part in the bus normally. |
| Amber | warning, error passive | Errors are being counted.  The controller still works, but something is wrong -- usually the bitrate, the wiring or the termination.  Error passive is the last step before bus off. |
| Red | bus off | The controller has stopped taking part altogether, and hears nothing until it is restarted. |

The text beside the dot names the state whenever it is not green, and
hovering over a channel says how the state is known -- because python-can has
no common way to ask, and only some adapters tell.  **socketcan** reports it
in its error frames, **PCAN** answers a status query, and **IXXAT** reports
bus off.  Everything else is judged on error frames alone: amber while they
arrive, but a controller that has gone bus off quietly looks exactly like an
idle bus.  On those adapters, a channel that falls silent after a burst of
errors is worth suspecting.

**Recover from bus off** is on the menu that opens when you click a channel.
It restarts the controller: PCAN, Vector and NI adapters through their own
reset; socketcan with `ip link set <channel> type can restart`, which needs
the right to configure the interface (if pycangui does not have it, the Event
Log gives the command, and a `restart-ms` set when the interface is brought
up makes the kernel restart it by itself); and anything else by closing the
channel and opening it again, which the protocol panes see as a disconnect and
a reconnect.

pycangui never restarts a controller by itself.  One that goes bus off again
straight away is saying the cause is still there, and restarting it onto the
wrong bitrate only puts more error frames on a bus that has working nodes on
it.  Fix the cause, then recover.

## Before it disturbs equipment

Every time pycangui starts it shows a notice saying what it is capable of,
which you click through.  It is not a question about anything in particular --
it is the sentence worth having read before your first connection rather than
after your first mistake.

**It is the one dialog here you cannot switch off.**  A notice dismissed for
good on the first afternoon is one that the colleague who picks the machine up
in March never sees, and it costs a keypress a session.  It also costs no time
at all: the libraries pycangui needs load behind it while you read, so what it
replaced was a second and a half of nothing on screen rather than a second and
a half of anything.

After that, pycangui asks before it can disturb equipment that is not its own,
once a session for each: joining a **real bus** (naming the bitrate, because a
controller at the wrong one cannot read a frame and signals an error on every
one it sees, which can drive the working nodes off the bus), **transmitting**
onto one, and **replaying** a log onto one.  A `virtual` channel never asks --
nothing leaves pycangui.  Change the bitrate and the connect question comes
back, since getting it wrong is what the question is for.

Each of those questions carries a **Do not ask me this again on this machine**
tick box.  What "remembered" means is worth knowing: an answer is kept for
*you*, on *this computer*, and never inside a workspace.  A workspace is a
folder made to be copied and handed to a colleague, and an agreement that
travelled inside one would mean somebody else's window, on somebody else's
bench, quietly not asking.  Another account on the same machine is asked for
itself, and so is the same account on another machine.

*Tools > Ask about everything again* brings the whole lot back.  A setting that can be turned on and not off is one you would
be right to distrust, and this one turns off the questions asked before
pycangui can disturb equipment.

## Recording and replay

**Record** and **Replay** sit together on the toolbar.  Record writes every
connected channel to a log file -- `.blf` (Vector binary), `.asc` (Vector
ASCII), `.trc` (PEAK), `.log` (candump), `.csv` or `.db` (SQLite); the format
follows the file extension, and each frame is tagged with the channel it
arrived on.  A recording is not tied to the channel you have selected, so
switching channel, or a channel dropping, leaves it running.

Replay plays a log back onto the selected channel with its original timing.
The arrow beside the button holds the speed (0.1x to 20x), *Loop*, and the
last few files replayed.  There is no separate offline mode: replaying onto a
**virtual** channel feeds the trace, the decoders, the signal hub and the plot
without touching any hardware, which is how a colleague's recording is
examined with nothing attached -- and if nothing is connected when you press
Replay, pycangui offers to create that virtual channel for you.  Replaying
onto a real bus is real traffic, so it asks first.
