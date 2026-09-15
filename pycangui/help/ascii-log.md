[&larr; Contents](manual.md)

# ASCII Log

The **ASCII Log** pane reads a CAN-ID as text. Some devices use an ID as a
console and print into the data bytes a few characters at a time. No protocol
settles which identifier that happens on -- CANopen has objects for a console,
but this window allows for any CAN-ID to be used.

Type the ID in hex and tick **29-bit** for an extended one. **Skip** ignores up
to 63 bytes at the start of each frame, and **Clear** throws away the text so
far while the ID goes on being read.

**One pane, one ID.** *View > Standard panes > Additional ASCII Log* opens
another, so two devices can be watched side by side; drop one onto another and
Qt tabs them if that is what you would rather have. Each pane remembers its
own ID, its own *Skip* -- for devices that put a length or a sequence number in
the first byte or two -- and its own text.

The dock says which ID it is showing, and the **name** box beside the ID adds
a name to that -- *ASCII 7A1 Bootloader*. *View > Rename pane* replaces the
whole title with one of your own, which it then keeps whatever you point it at.

NUL padding and carriage returns are dropped, newlines and tabs kept, and
anything else shown as a dot -- a stream of dots is how you find out the ID is
wrong.
