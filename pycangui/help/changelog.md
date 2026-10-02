[&larr; Contents](manual.md)

# Changes

What each version of pycangui added, changed and fixed, newest first. The format is [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/), and versions are numbered as [Semantic
Versioning](https://semver.org/spec/v2.0.0.html) describes. *Unreleased* is what has been done
since the last version, and comes out in the next one.

## [Unreleased]

### Added

- CANopen **SYNC counter**, in *Settings...* and *none* until set: the SYNC producer sends the
  one-byte counter devices with 0x1019 set expect, counting 1 to the overflow value.
- CANopen **Read EDS from node**: reads the EDS a device keeps in itself (object 0x1021), saves
  it in the workspace and uses it for the node -- for a node with no EDS to hand. The demo
  device keeps one.
- CANopen **Open DCF/EDS...**: a configuration file as a row of the node list, its object
  dictionary in the tree with no node or bus, values changed there and saved as a DCF. The
  PDO configuration tab works for the file too: its PDOs read from its objects, remapped, and
  put back with **Put in file**.
- **Command line**: `pycangui drive.dcf` opens a DCF or EDS, handed to the pycangui already
  open if there is one. A tick box in the installer, and *Tools > Settings > Open .dcf and .eds files
  with pycangui* for a source folder or pip, put pycangui in *Open with* for those files.
  `--workspace NAME` opens a workspace for that run only. `--run script.py` runs a script once
  the window is up and exits with its code, and `--skip-start-warning` skips the start-up
  notice for such a run.
- Console and scripts: `wait(seconds)`, a sleep that lets the window and the buses run.
- Custom panes: **Write all** writes every change held on the pane, and **Read all** reads
  again, asking first before it throws unwritten changes away. Dropdowns, ticks and map cells
  now hold a change until it is written, as typed boxes did.
- UDS **functional requests**: a *Functional* choice in ECU config sends each ticked service --
  session, reset, clear DTCs, CommunicationControl, tester present, DTC setting, baud rate, a
  one-frame raw request -- to every ECU at once, and says which ECUs objected.
- The UDS **baud rate change follows the ECUs**: every ECU is asked, then told, and the channel
  reopens at the new rate with the session and tester present; *Back to ...* returns to the
  channel's own rate. Behind a confirmation, and not on a channel whose rate pycangui cannot set.
- A **CANopen log** tab in the CANopen pane: every SDO read and write pycangui makes, whatever
  asked for it, with the data, how long it took, and an abort code and its meaning if it failed;
  NMT sent and SYNC started and stopped; node state changes, boot-up and heartbeats lost; each
  emergency; each LSS step. A tick per kind chooses what is shown.

### Changed

- Loading a DBC that has a message name or identifier already in a database loaded before it
  says so in the Event Log, and which file is the one used.
- CAN Transmit shows which database a DBC row came from, in its Unit column, and still names
  the file (*not loaded: drive.dbc*) once that database is removed. With several loaded, *Add >
  From DBC...* says which file each message is in. *Send selected*, *Counter / checksum...* and
  *Remove selected* are greyed until a message is selected.
- CAN Transmit tints the bytes a counter (blue) and a checksum (green) will overwrite, on the
  message's own data, and has a second J1850: *CRC-8 / SAE J1850, start 0x00*, with no start
  value and no final XOR.
- In CAN Transmit a signal with named values (a `VAL_` table) is picked from a list, and
  shown as name and number together -- *Run (1)* -- however it was entered.
- The **Period** of a DBC or RPDO row in CAN Transmit can be typed, where it was fixed at
  100 ms, and a row added from a database starts at the database's cycle time for the message.
- The demo CANopen device reports vendor ID 0x00000000 and the J1939 engine node manufacturer
  code 0, which are assigned to nobody, and no longer numbers that are somebody's.
- The CAN trace's Data column starts wide enough for eight bytes rather than for its heading.
- The plot's top row: *Window*, *Follow* and *Fit* are one **Time** choice -- *Follow* (with
  its seconds), *All*, which keeps everything in view as more arrives, and *Manual*, which
  dragging or zooming chooses by itself. **Export...** sits beside *Clear history*. *Unplot
  all* is on the signal list's right-click, with *Plot all* and the same two for one message.
- Signals and Plot has optional **Min**, **Max**, **Count** and **Rate** columns, from the
  list's right-click, where **Unit** can be hidden too, and the plot's top row says how many
  signals and samples are held.
- **Tools > Settings** holds what is set and left -- Theme, Verbose CAN logging, Strict DBC
  checks, and the two that set this installation up -- so that Tools itself is what is done.
  *Add to Start menu* is **Add shortcut to Start menu** there, and the installer's desktop
  shortcut is ticked to begin with.
- CANopen *Read RPDO config* is **Read PDO config**, and reads both directions: TPDOs for
  Signals and Plot as well as RPDOs for CAN Transmit.
- The CAN Trace's top row is shorter: Pause and Slow refresh are one choice -- Live
  refresh, Slow refresh or Paused -- and Columns is on the table's right-click, as it was on the header's.
  The plot has the same choice in place of its two ticks.
- The NMT send button says who it goes to -- *Send NMT to node 5* or *to all nodes*.
- The README and PyPI page list every adapter python-can supports.

### Fixed

- A UDS baud rate change reopened the channel the instant the request to change had been
  handed to the adapter, which could close it before the frame was on the wire. It now waits a
  tenth of a second first, and the same before following the ECUs back.
- A CRC placed in a database **signal** was computed over the frame with a zero where the CRC
  goes, not over the other bytes, so it did not match what a receiver computes (a sum or an XOR
  was unaffected). A checksum signal that is whole bytes is now left out, as one at a byte
  position always was.
- Plot **Fit** shows everything held for the plotted signals the first time: it had measured
  only what was already in view, so each press showed a little more. It also works while the
  display is paused.
- A library that is installed but will not load -- as a release of one of its own dependencies
  can make it -- is reported as that, with what failed, at start-up and by *Import signals*,
  rather than as a library that is missing or not reported at all.
- About wrapped its paths under the column of names. It is as wide as its longest line, up to
  most of the screen, and scrolls sideways past that.
- Removing a DBC left its signals in Signals and Plot, as though still decoded. They go with it.
- A CANopen TPDO that a loaded DBC also describes was in Signals and Plot twice, the DBC's
  scaled values beside the PDO's raw ones. The DBC's decode is used, and the PDO's left out.
- Once any CANopen node was listed, NMT could not be sent to all nodes: a node stayed selected.
  Clicking an empty part of the node list, or Esc, now selects none.
- A TPDO enabled after its node was first read was never decoded into Signals and Plot.

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

[Unreleased]: https://github.com/davhodg/pycangui/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/davhodg/pycangui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/davhodg/pycangui/releases/tag/v0.1.0
