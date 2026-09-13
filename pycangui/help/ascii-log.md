[&larr; Contents](manual.md)

# ASCII Log

The **ASCII Log** pane reads a CAN id as text. Some devices use an id as a
console and print into the data bytes a few characters at a time. No protocol
settles which identifier that happens on -- CANopen has objects for a console,
but an object is not an identifier -- so you give it the id.

**One pane, one id.** *View > Standard panes > Additional ASCII Log* opens
another, so two devices can be watched side by side; drop one onto another and
Qt tabs them if that is what you would rather have. Each pane remembers its
own id, its own *Skip* -- for devices that put a length or a sequence number in
the first byte or two -- and its own text.

The dock says which id it is showing until you give it a name of your own with
*View > Rename pane*, after which it keeps that name whatever you point it at.

NUL padding and carriage returns are dropped, newlines and tabs kept, and
anything else shown as a dot -- a stream of dots is how you find out the id is
wrong.
