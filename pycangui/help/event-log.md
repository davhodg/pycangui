[&larr; Contents](manual.md)

# Event Log

Everything pycangui says goes to the **Event Log** with a level, and the level
is what the line looks like:

- an **error** in red and bold -- something went wrong that nobody asked for,
  an emergency from a node, a library reporting a bus fault;
- a **warning** in amber -- what you asked for did not happen, or not as
  asked: a node that will not answer, an EDS that is missing;
- **good news** in green -- something that was wrong is right again: a
  heartbeat back, an emergency cleared. It is the one thing a log of failures
  otherwise never carries, and reading back through red to find out whether a
  fault ever cleared is a poor way to spend a morning;
- anything else in the ordinary text colour, because a colour on every line is
  a colour that says nothing.

The colours follow the theme: one set reads against a pale background, another
against a dark one.

A warning or an error opens the pane if it has been closed, a plain note does
not, and neither does good news -- a fault clearing is worth seeing and is not
worth a pane springing open over. So closing the pane means "stop chattering
at me" rather than "hide failures from me". [Hooks](hooks.md) and
[console](console.md) scripts get the same levels: `ctx.log(text)`,
`ctx.warn(text)`, `ctx.error(text)` and `ctx.good(text)`.

python-can and the protocol libraries -- canopen, can-j1939, udsoncan and
can-isotp -- report through Python's logging, and a backend often says nothing
about a bus error any other way. Their warnings and errors come here as well.

A CANopen line carrying an **SDO abort code** gets its meaning put beside it:
the library logs `Transfer aborted by client with code 0x05040000` and stops
there, which is a number and a shrug, so it arrives as *...0x05040000 (Timeout
of transfer communication detected)*. The code is what goes to the device's
maker; the meaning is what tells you whether the node refused or never
answered at all. *Aborted by client* is pycangui's own end giving up -- an
SDO timeout -- rather than the device saying no.
*Tools > Verbose CAN logging* adds their information messages too, which is
where the detail is when something is actually wrong, and more than you want
the rest of the time.

An error inside pycangui itself -- a bug -- is reported here with its
traceback, rather than disappearing with only a button that did nothing to show
for it. Please send it with a bug report.

*Help > Diagnostics...* writes a report of the versions and each channel's
adapter, state, load and error count here, and copies it to the clipboard; see
[About, licences and updates](about.md).
