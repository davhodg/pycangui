[&larr; Contents](manual.md)

# About, licences and updates

**Help > Documentation** opens this manual. It ships inside pycangui; *Back* and *Contents* get you around it.

The report names the version, and -- when pycangui is being run from a source checkout rather than an installed build -- the branch or tag and the commit it is on, read from `.git` without running git. Between two releases every build calls itself the same version, so that line is what tells one pull from another. It does not say whether any file has been edited since: that needs git itself, and the launcher runs without a console for it to use.

**Help > Known CAN ids...** lists every identifier pycangui would put a name to -- the messages in the [databases](signals.md) loaded, the [CANopen](canopen.md) nodes it knows of, the [UDS](uds.md) addresses and the [XCP](xcp.md) identifiers -- with where each name comes from, and marks any id two sources both claim. **Copy** puts the whole list on the clipboard, tab separated, ready for a spreadsheet or an email. J1939 is not in the list: 29-bit frames are named from the PGN in the identifier rather than from a list.

**Help > Diagnostics...** writes a report of the versions, each channel's adapter, state, load and error count, and any panes out on their own, to the [Event Log](event-log.md) and copies it to the clipboard -- ready to paste into a bug report.

**Help > Check for updates...** asks GitHub whether there is a newer release. If there is one, it offers to open the releases page.

**Help > Licences...** shows pycangui's own Apache-2.0 licence, the NOTICE attributions and the full third-party licence text, all shipped with the application.

**Help > About pycangui** shows the version alongside the Python, PySide6, python-can and canopen versions and the platform, in a form you can copy.

## If pycangui will not start

Started from the source folder, `pycangui.cmd` and `pycangui.sh` install anything missing before they start pycangui, and a library that is still missing is reported in a window rather than as nothing happening. `python -m pycangui --selftest` imports everything a build is most likely to be missing and exits with 0 if it is all there, which is how the build checks itself.

**If pycangui is slow to start**, the diagnostics report says where the seconds went: starting is measured on every run, step by step and package by package, so the breakdown is already there when somebody thinks to ask. It covers each step -- Qt, the libraries, the workspace's hooks and simulated nodes, the panes, the databases, the A2L, the plugins, the layout -- and the packages that took longest to import, since "the libraries" on its own says nothing about which one. The launchers stamp the time before they start Python, so there is also a line for the dependency check and the interpreter's own start, and the wait for the start-up notice to be answered is a line of its own rather than being charged to the step after it.

A start that takes longer than usual says so in the [Event Log](event-log.md) and points to *Help > Diagnostics*. The first start after an update is the usual one: Python compiles every changed file again, so that start says how many and that the next will be quicker. To have the whole report in the Event Log after every start, and in `startup-timing.txt` in [pycangui's own folder](files.md), start pycangui with `--timing` -- `pycangui.cmd --timing`, or `pycangui.sh --timing` -- or set `PYCANGUI_TIMING=1`. The report then says it is there because of that option, so one left in a shortcut is easy to find.
