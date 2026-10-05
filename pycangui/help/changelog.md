[&larr; Contents](manual.md)

# Changes

What each version of pycangui added, changed and fixed, newest first. The format is [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/), and versions are numbered as [Semantic
Versioning](https://semver.org/spec/v2.0.0.html) describes. *Unreleased* is what has been done
since the last version, and comes out in the next one.

## [Unreleased]

### Changed

- CAN Transmit: no *Ext* column -- an id above `7FF`, or written in eight digits, is 29-bit;
  a DBC row's database file is in its tooltip, not the Unit column.
- The status bar says the signal history limit is per signal and the samples a total.

## [0.2.0] - 2026-10-04

### Added

- CANopen **Open DCF/EDS...**: edit a configuration file's values and PDOs with no node or bus.
- Open a DCF or EDS from the command line or by double-click, with *Open with* registered by
  the installer or *Tools > Settings*.
- CANopen **Read EDS from node** (object 0x1021).
- CANopen **TIME** producer and **SYNC counter**, set up in *Settings...*.
- A **CANopen log** tab: every SDO, NMT, SYNC, state change, emergency and LSS step.
- CANopen **Remove node**, and *Move up* / *Move down* for PDO mapped objects.
- UDS **functional requests**, and a **baud rate change** that follows the ECUs and comes back;
  the top row shows the rate in use meanwhile.
- Signals and Plot: optional **Min**, **Max**, **Count** and **Rate** columns.
- **Signal history** limit in *Tools > Settings*, up to five million samples per signal.
- Custom panes: **Write all**, and *Read all* asks before discarding unwritten changes.
- Command line `--workspace`, `--run` and `--skip-start-warning`; `wait()` for scripts.

### Changed

- The protocol panes open in a window of their own the first time.
- The Event Log keeps everything, with *Clear* on its right-click; a library line repeated over
  and over is shown once and then counted.
- The CiA 402 (1.6) and DCF compare (1.2) plugins are updated: *Update...* in *Plugins > Manage plugins...*.
- CANopen *Read all* and *Save DCF* include DOMAIN objects, and they and *Apply DCF* show
  progress, can be stopped, and report failures by abort code.
- CANopen buttons that cannot act say so in a box, not only in the Event Log.
- CANopen *Settings...* logs only what changed, and offers to restart a running SYNC.
- CANopen *Read PDO config* reads both directions; the NMT button says who it goes to.
- Named values read the same everywhere, name first: *Run (1)*.
- CAN Transmit: named-value pick-lists, counter and checksum bytes tinted, a typed period,
  the source DBC shown, and buttons greyed until a row is selected.
- A DBC message clashing with one already loaded is reported in the Event Log.
- Plot: one **Time** choice (*Follow*, *All*, *Manual*), an *Export...* button, and *Plot all* /
  *Unplot all* on right-click.
- CAN Trace: *Live refresh*, *Slow refresh* or *Paused* in one choice; Data column sized for
  eight bytes.
- ASCII Log: **Send enable** and **Send disable** buttons. CiA 402: *Poll* is a tick box.
- J1939: **Request stop broadcasts (DM13)** is a tick box; DM4 with no freeze frames shows what came;
  no answer and a refused request are warnings.
- *Tools > Settings* submenu; the installer's desktop shortcut is ticked by default.
- Demo nodes use vendor and manufacturer ID 0, assigned to nobody.
- Code signing policy, code of conduct and build attestations for releases.

### Fixed

- CANopen object dictionary: only the value column can be edited.
- NMT could not be sent to all nodes once a node was listed.
- A TPDO enabled after the first read was not decoded; one also in a DBC was decoded twice.
- Removing a DBC left its signals in Signals and Plot.
- A CRC in a DBC signal was computed over the wrong bytes.
- A library that is installed but fails to load is reported as such.
- About wrapped long paths.
- A PDO object mapped with fewer bits than its type was not decoded, and flooded the Event
  Log with library errors, as did NMT frames with no data. The object is decoded as mapped,
  and the mismatch with the EDS flagged; a frame nothing can handle is said once.
- Moving a plotted signal with data to the other Y axis failed, and the curve stopped drawing.
- DCF compare: a DOMAIN value made its column too wide to use. Long values are cut short and
  every column can be widened.

## [0.1.1] - 2026-09-29

### Added

- UDS **ECU control**: CommunicationControl and a baud rate change (LinkControl), beside reset.
- UDS **Read all DTC data**, with extended data records and snapshot DIDs sized and named by
  `hooks/uds.py`.
- J1939 **requests from a list** -- DM1 to DM5, DM11 and identification -- each answer, or none,
  reported in the Event Log, and a warning when a node answers against the standard.
- J1939 **Request address claims**, and **Stop broadcasts** (DM13), held until started.
- A recording shows the frames written and the file's size in the status bar, and says both
  in the Event Log when it stops. The status bar's frame count is now labelled *Total frames*.
- **Check for updates** asks PyPI when pycangui was installed with pip.
- **About** says where pycangui is running from: the Python, its environment, and where the
  package came from -- including a source folder's copy running in an environment that does
  not have pycangui installed.
- Badges, tags, classifiers and more links on the PyPI page.

### Changed

- The UDS pane is in tabs, and needs much less width.
- J1939 messages are sent from the tester's address, claiming it first if need be, not 0xFE.
- Recording and exporting signals suggest a file name with the date and time in it, such as
  `capture_2026-09-25_143012.blf`, rather than the same name every time.
- The Windows installer shows the third-party notices before installing, not after.
- CANopen *Add node*, LSS and SYNC with no channel connected say it is the channel that is
  not connected, and *Add node* says so before asking for a node id.

### Fixed

- UDS extended data (0x06) and snapshot (0x04) reports, which could not be read.
- J1939 DM2 answers were shown as active faults.
- Installing the MDF reader on first use froze the window for the whole download, which
  looked like a crash. pip runs in the background, says what it is doing, and can be cancelled.
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

[Unreleased]: https://github.com/davhodg/pycangui/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/davhodg/pycangui/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/davhodg/pycangui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/davhodg/pycangui/releases/tag/v0.1.0
