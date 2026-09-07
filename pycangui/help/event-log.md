[&larr; Contents](manual.md)

# Event Log

Everything pycangui says goes to the **Event Log** with a level: information,
warning or error.  A warning or an error opens the pane if it has been closed,
a plain note does not, so closing it means "stop chattering at me" rather than
"hide failures from me".  [Hooks](hooks.md) and [console](console.md) scripts get the same three:
`ctx.log(text)`, `ctx.warn(text)`, `ctx.error(text)`.
