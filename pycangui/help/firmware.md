[&larr; Contents](manual.md)

# CANopen firmware (CiA 302-3)

The **Firmware** tab of the [CANopen](canopen.md) pane downloads a program to the
node selected in the list above it. By default it does what CiA 302-3 says: stop
the program (0x1F51), clear it, write the image as a domain (0x1F50), start it
again. Intel HEX, S-record and raw binary are all read. For CiA 302-3 the image
has to be one contiguous block, because a program download *is* one block of
bytes and filling the gaps would put invented bytes into somebody's flash; an
image in pieces is refused before anything is written.

## Bootloader, transfer and version

**Enter bootloader** and **Exit bootloader** stop and start the program
(0x1F51), which is how a CiA 302-3 device goes into its loader and out again.
**Program** is which program on the device, counted from 1: a controller with
two processors takes two images. **Transfer** chooses how the image is written:
*Segmented*, which every device takes, or *Block*, which sends many frames to
each acknowledgement and is much faster on a device that supports it. A device
that does not should refuse block transfer at the start, before anything is
written. **Read version** reads the manufacturer software version (0x100A), and
the software identification (0x1F56) and flash status (0x1F57) where the device
keeps them. It works without an EDS, which is the usual case for a device
sitting in its loader. After a download the version is read again and said in
the Event Log.

Enter bootloader and Download are designed to ask first, once a session for each
node, because stopping the program stops whatever it was controlling. While a
program goes in, the rest of pycangui's own traffic is [paused](pause.md), so
the node is not identified or its PDOs read as it reboots into its loader.

## Programming timeout

**Programming timeout** is how long the device may take to answer each request
while a download runs, 10 s unless changed. Clearing a program erases its flash,
and a device doing that answers only when it has finished. Everything else uses
the SDO timeout set under *Settings...* in the CANopen pane, and a timeout set
longer there is not shortened here.

## A device of your own

**Most devices do not do it that way.** Firmware download over CANopen is
usually a sequence of the maker's own writes to objects of their own choosing,
and no amount of standards reading will produce it. Each part of the sequence is
a [hook](hooks.md) in `hooks/canopen.py`, given the node's identity -- vendor,
product and revision -- so one workspace can hold the sequence for each product
it talks to, and CiA 302-3 for the rest:

| Hook | What it does | Left as it is |
|---|---|---|
| `firmware_check` | refuses an image that does not belong to the node | nothing refused |
| `enter_bootloader` | puts the node in its loader | stop the program |
| `exit_bootloader` | takes it out again | start the program |
| `firmware_steps` | the whole download, as steps, each said in the Event Log as it starts | stop, clear, write, start |
| `firmware_version` | what the node says it runs | 0x100A |
| `flash_status` | how the last download went | 0x1F56 and 0x1F57 |

Each is given `device`, which reads and writes the node: `device.read(index,
sub)`, `device.write(index, sub, value)`, `device.write_domain(index, sub, data,
progress)` for a block of bytes with the progress bar moving, and `with
device.patient(seconds):` for an answer that takes longer. The examples in the
file show a maker's own sequence for one product. The sequence is worked out
before its first step runs, so a refusal leaves the node untouched; a step that
fails stops the download there and says which step it was.

*Tools > Open hooks folder* opens the file, and *Tools > Reload hooks* picks up
an edit without a restart.

## It used to be a plugin

Until this version it was a plugin, installed into the workspace and edited
there. A workspace with it installed and unchanged has it removed the first time
this version opens it, and the Event Log says so. One that was changed is left
as it was and goes on loading as a pane of its own; the Event Log says, once,
to copy what was changed in it into the hooks above and then remove the plugin.

While a device is being programmed it answers very little and slowly, so
timeouts are the expected thing rather than a fault -- and pulling the power
part way through is how a controller is turned into a brick.
