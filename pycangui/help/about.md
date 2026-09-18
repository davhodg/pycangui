[&larr; Contents](manual.md)

# About, licences and updates

**Help > Documentation** opens this manual. It ships inside pycangui; *Back* and *Contents* get you around it.

**Help > Diagnostics...** writes a report of the versions, each channel's adapter, state, load and error count, and any panes out on their own, to the [Event Log](event-log.md) and copies it to the clipboard -- ready to paste into a bug report.

**Help > Check for updates...** asks GitHub whether there is a newer release. If there is one, it offers to open the releases page.

**Help > Licences...** shows pycangui's own Apache-2.0 licence, the NOTICE attributions and the full third-party licence text, all shipped with the application.

**Help > About pycangui** shows the version alongside the Python, PySide6, python-can and canopen versions and the platform, in a form you can copy.

## If pycangui will not start

Started from the source folder, `pycangui.cmd` and `pycangui.sh` install anything missing before they start pycangui, and a library that is still missing is reported in a window rather than as nothing happening. `python -m pycangui --selftest` imports everything a build is most likely to be missing and exits with 0 if it is all there, which is how the build checks itself.

**If pycangui is slow to start**, `--timing` says where the seconds went: `pycangui.cmd --timing` (or `pycangui.sh --timing`, or `PYCANGUI_TIMING=1` to leave it on). It reports each step -- Qt, the libraries, the workspace's hooks and simulated nodes, the panes, the databases, the A2L, the plugins, the layout -- into the [Event Log](event-log.md) and into `startup-timing.txt` in [pycangui's own folder](files.md), and it names the packages that took longest to import, since "the libraries" on its own says nothing about which one. The clock starts inside Python, so what the launcher does before that, and the interpreter's own start, are not in the total.
