[&larr; Contents](manual.md)

# Changes

What each version of pycangui added, changed and fixed, newest first. The format is [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/), and versions are numbered as [Semantic
Versioning](https://semver.org/spec/v2.0.0.html) describes. *Unreleased* is what has been done
since the last version, and comes out in the next one.

## [Unreleased]

### Added

- **ECU control** in the UDS pane: CommunicationControl (0x28), to quieten an ECU on the bus
  or give it back, and a baud rate change (LinkControl, 0x87), verified before it is made.
  Both ask first. ECU reset is there too.
- **Read all** in the UDS DTC tab: every DTC matching the status mask with its extended data
  and severity, every snapshot, the first and most recent failed and confirmed DTCs, the fault
  detection counters and the permanent DTCs, as one report. Supported DTCs on request.
- **J1939 requests from a list**: DM1, DM2, DM3, DM4, DM5, DM11 and ECU, software and
  component identification, or any PGN typed, to every node or one seen. Every request says
  in the Event Log what came of it: each node's answer decoded, including no faults, a
  refusal, or no answer at all after 1.25 s. DM2 also goes into the fault table as previously
  active. DM3 and DM11 ask first.
- **Request address claims** in the J1939 pane, and **Stop broadcasts** (DM13), held until
  started again.
- The demo engine answers every request in that list.
- `hooks/uds.py::extended_data_record` and `EXTENDED_DATA_RECORDS`, for the size and name of
  each DTC extended data record, and `did_size` and `DID_SIZES`, for the length of a DID in a
  snapshot where the ECU will not read it to say.
- **Check for updates** asks PyPI for a pip installation, and gives the pip command that
  updates it and the environment to run it in. The Windows build still asks for the latest
  release, and a source folder for newer commits.
- The PyPI page has the README's badges, tags, classifiers, and links to the manual, the
  discussions, the releases and these notes.

### Changed

- The UDS pane is in tabs -- DIDs, routines and raw; DTCs; Transfer -- below the ECU config,
  the session and ECU control, which stay in view. It needs a good deal less width.
- The Windows installer shows the third-party notices on the page after the licence, before
  anything is installed, rather than after installing.
- On the PyPI page, the link to the Windows installer is under *More*.

### Fixed

- J1939 requests, Send PGN and DM13 went out from the null address 0xFE until an address was
  claimed, whatever the tester's address was set to. The address is claimed first, and they
  are sent from it.
- A J1939 node answering a request with a transfer to 0xFE, which J1939-21 does not allow, was
  reported as no answer. It is now a warning saying it answered and is not decoded.
- A UDS DTC with no status bits set was printed with two spaces before its description.
- A J1939 DM2 answer was shown in the active faults table as though its faults were active.
- The UDS reports for a DTC's extended data (0x06) and snapshots (0x04) are read and split by
  pycangui, where udsoncan refused them without every record's size given first. A snapshot's
  DIDs are named and decoded like any other DID.
- A development build, such as `0.1.0.dev5` from TestPyPI, is no longer taken as newer than the
  release it leads to.

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

[Unreleased]: https://github.com/davhodg/pycangui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/davhodg/pycangui/releases/tag/v0.1.0
