[&larr; Contents](manual.md)

# About, licences and updates

**Help > Documentation** opens this manual. It ships inside pycangui; *Back* and *Contents* get you around it.

**Help > Diagnostics...** writes a report of the versions, each channel's adapter, state, load and error count, and any panes out on their own, to the [Event Log](event-log.md) and copies it to the clipboard -- ready to paste into a bug report.

**Help > Check for updates...** asks GitHub whether there is a newer release. If there is one, it offers to open the releases page.

**Help > Licences...** shows pycangui's own Apache-2.0 licence, the NOTICE attributions and the full third-party licence text, all shipped with the application.

**Help > About pycangui** shows the version alongside the Python, PySide6, python-can and canopen versions and the platform, in a form you can copy.

## If pycangui will not start

Started from the source folder, `pycangui.cmd` and `pycangui.sh` install anything missing before they start pycangui, and a library that is still missing is reported in a window rather than as nothing happening. `python -m pycangui --selftest` imports everything a build is most likely to be missing and exits with 0 if it is all there, which is how the build checks itself.
