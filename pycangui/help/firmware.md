[&larr; Contents](manual.md)

# CANopen firmware (CiA 302-3)

A [plugin](plugins.md) that ships with pycangui. *Plugins > Supplied with
pycangui* installs it, as does *Plugins > Manage plugins...*; until then it is
not there.

It downloads a program to a CANopen node: stop the
program (0x1F51), clear it, write the image as a domain (0x1F50), start it
again. Intel HEX, S-record and raw binary are all read; the image has to be
one contiguous block, because a program download *is* one block of bytes and
filling the gaps would put invented bytes into somebody's flash.

## Bootloader, transfer and version

**Enter bootloader** and **Exit bootloader** stop and start the program
(0x1F51), which is how a CiA 302-3 device goes into its loader and out again.
**Transfer** chooses how the image is written: *Segmented*, which every device
takes, or *Block*, which sends many frames to each acknowledgement and is much
faster on a device that supports it. A device that does not should refuse block
transfer at the start, before anything is written. **Read version** reads the
manufacturer software version (0x100A), and the software identification
(0x1F56) and flash status (0x1F57) where the device keeps them. It works
without an EDS, which is the usual case for a device sitting in its loader.
Enter bootloader and Download are designed to ask first, once a session for each node, because
stopping the program stops whatever it was controlling.

## Programming timeout

**Programming timeout** is how long the device may take to answer each request
while a download runs, 10 s unless changed. Clearing a program erases its flash,
and a device doing that answers only when it has finished. Everything else uses
the SDO timeout set under *Settings...* in the [CANopen](canopen.md) pane, and a
timeout set longer there is not shortened here.

## A device of your own

**Most devices do not do it that way.** Firmware download over CANopen is
usually a sequence of the maker's own writes to objects of their own choosing,
and no amount of standards reading will produce it. That is exactly why it is
a plugin: install it, then edit the `program.py` in your workspace to be what
the device actually wants. The copy you edit is the one that runs, and the
pane, the progress bar and the reporting go on working around it.

While a device is being programmed it answers very little and slowly, so
timeouts are the expected thing rather than a fault -- and pulling the power
part way through is how a controller is turned into a brick.
