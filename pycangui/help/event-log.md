[&larr; Contents](manual.md)

# Event Log

Everything pycangui says goes to the **Event Log** with a level: information,
warning or error. A warning or an error opens the pane if it has been closed,
a plain note does not, so closing it means "stop chattering at me" rather than
"hide failures from me". [Hooks](hooks.md) and [console](console.md) scripts get the same three:
`ctx.log(text)`, `ctx.warn(text)`, `ctx.error(text)`.

python-can and the protocol libraries -- canopen, can-j1939, udsoncan and
can-isotp -- report through Python's logging, and a backend often says nothing
about a bus error any other way. Their warnings and errors come here as well.
*Tools > Verbose CAN logging* adds their information messages too, which is
where the detail is when something is actually wrong, and more than you want
the rest of the time.

An error inside pycangui itself -- a bug -- is reported here with its
traceback, rather than disappearing with only a button that did nothing to show
for it. Please send it with a bug report.

*Help > Diagnostics...* writes a report of the versions and each channel's
adapter, state, load and error count here, and copies it to the clipboard; see
[About, licences and updates](about.md).
