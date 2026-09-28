[&larr; Contents](manual.md)

# Changes

What each version of pycangui added, changed and fixed, newest first. The format is [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/), and versions are numbered as [Semantic
Versioning](https://semver.org/spec/v2.0.0.html) describes. *Unreleased* is what has been done
since the last version, and comes out in the next one.

## [Unreleased]

### Fixed

- Installing the MDF reader on first use froze the window for the whole download, which
  looked like a crash. pip runs in the background, says what it is doing, and can be cancelled.

## [0.1.1] - 2026-09-27

### Added

- UDS **ECU control**: CommunicationControl and a baud rate change (LinkControl), beside reset.
- UDS **Read all DTC data**, with extended data records and snapshot DIDs sized and named by
  `hooks/uds.py`.
- J1939 **requests from a list** -- DM1 to DM5, DM11 and identification -- each answer, or none,
  reported in the Event Log, and a warning when a node answers against the standard.
- J1939 **Request address claims**, and **Stop broadcasts** (DM13), held until started.
- **Check for updates** asks PyPI when pycangui was installed with pip.
- Badges, tags, classifiers and more links on the PyPI page.

### Changed

- The UDS pane is in tabs, and needs much less width.
- J1939 messages are sent from the tester's address, claiming it first if need be, not 0xFE.
- The Windows installer shows the third-party notices before installing, not after.

### Fixed

- UDS extended data (0x06) and snapshot (0x04) reports, which could not be read.
- J1939 DM2 answers were shown as active faults.
- A mistake typed in the Python Console was reported as a bug in pycangui.
- A development build from TestPyPI was taken as newer than the release it leads to.

## [0.1.0] - 2026-09-27

The first release, on PyPI (`pip install pycangui`) and as a Windows installer.

### Added

- **Live trace** of every connected channel on one clock, CAN FD included, with filtering that
  hides rather than discards, recording to six log formats, and replay of a log onto a bus.
- **Transmit** of raw frames, DBC messages edited by signal, or a CANopen RPDO, with counters
  and checksums.
- **CANopen**: node list, object dictionary, PDO configuration, EMCY and stored faults, LSS,
  SYNC, and DCF save and apply.
- **Custom panes**: the CANopen objects a job needs, laid out as a form with labels and units,
  built from the object dictionary with no code, and polled into Signals and Plot.
- **UDS** over ISO-TP: sessions, security access, DIDs, DTCs, routines, and firmware transfer in
  either direction, with the ECU's timing kept to, or relaxed for a slow bootloader.
- **J1939**: nodes from address claims, DM1 faults with lamp status and the failure mode in
  words, multi-packet messages, requesting and sending PGNs, and SPNs decoded from a J1939 DBC.
- **XCP and CCP on CAN**: connect, seed and key, and reading and writing A2L measurements and
  characteristics by polling.
- **Seed and key** from a hook or from a seed and key DLL, 32-bit included, for UDS, XCP and
  CCP.
- **ASCII Log**: any CAN identifier read as text.
- **Signals and Plot** from DBC decode, CANopen TPDOs or XCP and CCP polling, and out to CSV.
- **Python**: hooks with hot reload, replaceable components, a live console and *Run script*.
- **Plugins** that add a pane of their own. Three come with pycangui: CANopen firmware download
  (CiA 302-3), DCF compare, and CiA 402 motor control.
- **Simulated nodes** on a virtual bus or a real adapter, and gateways between channels.
- **Workspaces**, one per product, exported and imported as one zip.

[Unreleased]: https://github.com/davhodg/pycangui/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/davhodg/pycangui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/davhodg/pycangui/releases/tag/v0.1.0
