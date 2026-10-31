[&larr; Contents](manual.md)

# About, licences and updates

**Help > Documentation** opens this manual. It ships inside pycangui; *Back* and *Contents* get you around it.

The report names the version, and -- when pycangui is being run from a source checkout rather than an installed build -- the branch or tag and the commit it is on, read from `.git` without running git. Between two releases every build calls itself the same version, so that line is what tells one pull from another. It does not say whether any file has been edited since: that needs git itself, and the launcher runs without a console for it to use.

**Help > Known CAN ids...** lists every identifier pycangui would put a name to -- the messages in the [databases](signals.md) loaded, the [CANopen](canopen.md) nodes it knows of, the [UDS](uds.md) addresses and the [XCP](xcp.md) identifiers -- with where each name comes from, and marks any id two sources both claim. **Copy** puts the whole list on the clipboard, tab separated, ready for a spreadsheet or an email. J1939 is not in the list: 29-bit frames are named from the PGN in the identifier rather than from a list.

**Help > Diagnostics...** opens a report in a window of its own, and copies it to the
clipboard as it opens -- ready to paste into a bug report. **Refresh** makes it again,
**Copy** copies it again and **Save...** writes it to a file. It gives:

- the versions, the Qt style and the theme, each channel's adapter, state, load and error
  count, and any panes out on their own;
- **what is being sent**: every cyclic message on each channel, with its identifier, bytes
  and period, whose it is -- a CAN Transmit row, CANopen's SYNC, or one started on the bus
  directly from the console or a script -- and whether pycangui or the adapter keeps its
  time; then how many frames were sent and received in the last five seconds, and the
  identifiers sent most. A frame nothing on screen accounts for is told apart from a
  device's here;
- whether pycangui's own traffic is [paused](pause.md), and who holds the pause; the panes
  polling and the simulated nodes running;
- CANopen's nodes and their states, its node ids, SDO timing, SYNC and TIME; UDS's
  identifiers, Tester Present and any transfer; J1939's address and DM13 hold; XCP or CCP's
  connection and polling;
- the DBCs, A2L and plugins loaded and the workspace; the last twenty warnings and errors
  from the [Event Log](event-log.md); and the threads running.

A part of it that cannot be read says so and the rest is still given. Names, files and
identifiers are listed; hook contents and passwords never are.

**Help > Check for updates...** asks the question that fits how pycangui was installed. The Windows installer's build asks GitHub whether there is a newer release, and offers to open the releases page. A pip installation asks PyPI for its newest version, and says the pip command that updates it. Run from a source folder, it asks about commits instead, since a release is behind everything pulled since: how many newer commits GitHub has on the branch this folder is on, and it offers to open the list of them. A branch GitHub does not have is compared with master.

**Help > Licences...** shows pycangui's own Apache-2.0 licence, the NOTICE attributions and the full third-party licence text, all shipped with the application.

**Help > About pycangui** shows the version alongside Python, every package pycangui runs on and the platform, in a form you can copy. It also says where it is running from: the Python it started with, that Python's environment, the folder the pycangui package was loaded from, and how it got there -- pip, an editable install of a source folder, or the Windows installer. On a computer with more than one Python that answers which one it is. Started from inside a source folder, Python loads pycangui from that folder even when the environment has no pycangui installed, or a different one; About says so.

## If pycangui will not start

Started from the source folder, `pycangui.cmd` and `pycangui.sh` install anything missing before they start pycangui, and a library that is still missing is reported in a window rather than as nothing happening. The entry made by *Tools > Settings > Add shortcut to Start menu* (*Add shortcut to applications menu* on Linux), or by *Add shortcut to desktop* on Windows, starts the launcher too, so starting from it after a pull does the same. Installed with pip, the same two make a shortcut that starts pycangui with the Python it is installed in. The first start says where they are in the Event Log; the launchers ask nothing. On Linux, a library Qt needs to draw the window is named, with the package it comes in, before Qt is started: Qt itself says only that it could not load the "xcb" plugin. `python -m pycangui --selftest` imports everything a build is most likely to be missing and exits with 0 if it is all there, which is how the build checks itself.

**If pycangui is slow to start**, the diagnostics report says where the seconds went: starting is measured on every run, step by step and package by package, so the breakdown is already there when somebody thinks to ask. It covers each step -- Qt, the libraries, the workspace's hooks and simulated nodes, the panes, the databases, the A2L, the plugins, the layout -- and the packages that took longest to import, since "the libraries" on its own says nothing about which one. The launchers stamp the time before they start Python, so there is also a line for the dependency check and the interpreter's own start, and the wait for the start-up notice to be answered is a line of its own rather than being charged to the step after it.

A start that takes longer than usual says so in the [Event Log](event-log.md) and points to *Help > Diagnostics*. The first start after an update is the usual one: Python compiles every changed file again, and that start says how many it compiled. To have the whole report in the Event Log after every start, and in `startup-timing.txt` in [pycangui's own folder](files.md), start pycangui with `--timing` -- `pycangui.cmd --timing`, or `pycangui.sh --timing` -- or set `PYCANGUI_TIMING=1`. The report then says it is there because of that option, so one left in a shortcut is easy to find.
