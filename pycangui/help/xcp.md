[&larr; Contents](manual.md)

# XCP / CCP

The **XCP / CCP** pane reads and writes a controller's memory by address, in
either protocol. **Engine** chooses which: *xcp-builtin*, or *ccp-builtin* for the protocol
XCP replaced, which plenty of controllers in service still speak. An engine
is named for the protocol it speaks and whose code speaks it, so one calling
into a library of your own would sit beside these as, say, *xcp-rust*. Everything else on the pane is the same either way -- the A2L,
the list, the reading and writing, the plot -- because the two differ in how
the bytes travel and not in what is being asked for.

The identifier boxes start empty, because neither protocol standardises a
pair and a guess would be sent to whatever answered on it: they come from the
A2L or from the supplier, and *Connect* waits until both are filled in. They
are labelled **Tx ID** and **Rx ID** rather than by protocol, since XCP calls
them the command and response identifiers and CCP the CRO and the DTO; the
tooltip says so.

Whether the identifiers are 11-bit or 29-bit is in how they are written, as in
[CAN Transmit](transmit.md): anything above `7FF` is 29-bit, and so is one
written out in eight digits, so there is no box to tick for it.

**IDs from A2L** appears when the A2L loaded gives the identifiers for XCP on
CAN, which most do. Ticked, the two boxes show the A2L's and cannot be typed
into; unticked, they are yours again. An A2L with none to give shows no tick
box, and nor does a CCP engine, since the identifiers in question are XCP's.

**Station** appears for CCP only. CCP addresses a controller by a station
number as well as by identifiers, so several controllers can share one pair
and answer in turn; a station the slave does not recognise gets no answer at
all, which is how it should be. XCP has no such thing, so the box is hidden.

Connecting says what the slave offers and, from XCP's GET_STATUS, what of
it is still locked. A seed or a key longer than one frame holds goes in
pieces, as XCP has it, so a key of eight bytes or more works on classic CAN.
A slave that addresses its memory in two- or four-byte elements says so on
connecting, and reads, writes, programming and checksums are counted in its
elements; a write that is not a whole number of them is refused.

Connecting over XCP also asks the slave, with GET_ID, which A2L it goes
with, and says so. A name that is not the A2L loaded is said but not warned
about, since files are renamed far more often than they are wrong; the EPK,
below, is the real check. GET_ID is optional, and a slave without it is
connected to all the same.

### When the slave does not answer

XCP says what to do when an answer goes missing, and pycangui does it. The
command is sent again, up to twice, each time after a SYNCH, which puts the
slave back to waiting for a command. Where the command moves the slave's
memory pointer -- UPLOAD, DOWNLOAD, PROGRAM -- the pointer is put back first,
so a lost answer cannot make a read or a write skip bytes or repeat them. A
slave that answers ERR_CMD_BUSY is asked again a moment later. The log says
each time it happens; three silences in a row and the command is given up on.

The seed and key are not sent again piece by piece: an unlock that loses an
answer is started over, with Unlock. Nor is CONNECT, or PROGRAM_RESET, which
a slave that resets at once never answers.

How long to wait is the A2L's, from the `PROTOCOL_LAYER` in its XCP
`IF_DATA` (XCP on CAN's own, where it has one): T1 for most commands, T2 for
BUILD_CHECKSUM, T3 for PROGRAM_START, T4 for PROGRAM_CLEAR, T5 for PROGRAM
and PROGRAM_RESET, T6 for CONNECT, and T7 before a busy slave is asked again.
Without one, a second for most commands, thirty for erasing, summing and
programming, and a fifth of a second for T7. Loading an A2L says which it
gives. CCP has no SYNCH, and waits as it always has.

Two smaller differences are handled without asking. CCP is a Motorola-order
protocol, so values are read big-endian, where XCP uses the order the slave
declares on connecting. And every CCP command carries a counter which the
answer echoes; pycangui sends one and does not insist on it coming back
correct, because slaves in the field are careless with it and refusing a good
answer over a counter would waste an afternoon.

Set the identifiers, Connect,
choose **Load...** under the **A2L** button and the MEASUREMENTs and CHARACTERISTICs appear. A file
big enough to take more than a moment shows how far it has got, and *Cancel*
while it is being read leaves the A2L there was in use.
Double-click to read one, edit a characteristic's value to write it (unlock CAL
first -- the seed-to-key algorithm is
[`hooks/xcp.py::compute_key`](hooks.md), or the seed and key DLL named
with **Seed and key DLL...** under the arrow of the **Unlock CAL** button, which is the same one the [UDS](uds.md) pane
uses and is described there), and tick *Plot* to poll a measurement into the
**Signals and Plot** pane. A real A2L runs to hundreds of parameters, so the
box above the list filters them: match on the name, the address in either hex
or decimal, the type, the unit or the description, with several words all
having to match, and *MEASUREMENT* or *CHARACTERISTIC* narrowing it to one
kind. **Plotted** shows only the ones ticked for the plot, which a filter
would otherwise hide while they went on being read. Where the A2L sorts its
parameters into `GROUP`s and `FUNCTION`s, the **Group** box beside the filter
shows one of them at a time, with the groups inside it, each listed under the
one it is in; the filter then narrows that further. A file with none shows no
box. Which file the names came from is shown beside the
**A2L** button, and **Remove** under it forgets the file -- including one that has moved since, which
is reported at startup and then sits there marked *(missing)*. The demo device
answers XCP on 0x7A0/0x7A1 and CCP on 0x7B0/0x7B1 at station 1, and both match
`resources/demo.a2l`.

The parameters are on the **Parameters** tab. Flashing, reading memory out
and checksums are on the **Transfer** tab, described below.

## Transfer: flashing, uploading and checksums

The **Transfer** tab is the [UDS](uds.md) pane's firmware transfer for a slave
flashed over XCP or CCP. The connection above it is the one it uses, and its
log is the pane's. **Operation** chooses what it does:

- **Program (flash)** erases each area the file has bytes for, then writes
  them: PROGRAM_START, PROGRAM_CLEAR and PROGRAM over XCP; CLEAR_MEMORY and
  PROGRAM over CCP.
- **Download (RAM)** writes with DOWNLOAD and erases nothing, for memory that
  is written like RAM, a calibration page say.
- **Upload to file** reads the ticked areas into an Intel HEX, S-record or raw
  binary file. A raw binary has no addresses, so it holds one area only.
- **Checksum** has the slave sum each ticked area with BUILD_CHECKSUM. With a
  file chosen, it sums the file's bytes in each area instead, and compares.

The file is an Intel HEX, S-record or raw binary, as on the UDS tab; a raw
binary needs its start address typed in.

### Areas

Where UDS sends each segment of a file where the file says, here the A2L has a
say as well. The **Areas** table lists the stretches of memory that may be
programmed, and the order they are erased in and written in. They come from the
A2L's programming `SECTOR`s, in the `PGM` block of its XCP `IF_DATA`, which give
both orders. A file with no sectors gives its `FLASH` memory segments instead,
taken in address order. Only the file's bytes inside a ticked area are sent.
**In file** says how many bytes, in hex, that is for each area.

An area the file has nothing for is not erased, since erasing it would only take
away what the slave had there. The demo A2L's header sector is erased first and
written last, which is the usual reason for the two orders: a flash cut short
leaves no valid header pointing at half an application.

The A2L is not always right, and not always there, so the table can be changed:

- untick an area to leave it alone;
- edit an area's address, size or either order;
- **Add** an area the A2L does not list;
- **From file** makes the file's own segments the areas, to flash it as it is;
- **Write bytes in no area too** sends the file's bytes that no area covers,
  each run of them as an area of its own, after the rest. Without it they are
  left out, and the log says which.

An edited table is kept, and comes back next time, until **From A2L** puts the
file's areas back.

### How it is done

- **Erase first** clears each area, whole, before anything is written.
- **Unlock** does the seed and key first, for the PGM resource to program and
  CAL to download. The key comes from the same hook or DLL as *Unlock CAL*.
- **Verify** checks what was written. *Checksum* has the slave sum each run it
  was sent, and compares that with the file. *Read back* reads it all out
  again, which is slower but says where it first differs.
- **Checksum** chooses the algorithm to compare with. Left as it is, that is
  the one an XCP slave says it used, or else the one the A2L names: the
  `CHECKSUM` block in XCP's `IF_DATA`, or `CHECKSUM_PARAM` in CCP's `TP_BLOB`,
  whose procedure numbers are XCP's checksum types. A CCP answer does not say
  which algorithm made it, so for CCP the A2L is what makes a comparison
  possible. A procedure number above XCP's is the maker's own: it is shown,
  and nothing is compared unless one is chosen here. The A2L's largest block
  (`MAX_BLOCK_SIZE`, or the second number of `CHECKSUM_PARAM`) is kept to:
  a longer stretch is summed a block at a time.
- **Reset after** sends PROGRAM_RESET, so the slave starts what it was given.
  It is XCP only, since CCP has no reset, and the pane is disconnected
  afterwards.

Programming asks first, once a session, and says what will be erased and
written, in order. *Cancel* stops after the piece being sent now. Polling waits
while a transfer runs. The demo device's XCP and CCP slaves have flash from
0x4000: erased to FF, and programming can only turn ones into zeros, as real
flash does. Their checksums are CRC_32 over XCP and ADD_12 over CCP, as the
demo A2L says, in blocks of up to 0x100 bytes.

## What is read from an A2L

Single values, and rows of them:

- **Measurements**, and **characteristics** of type `VALUE`. A characteristic's
  data type comes from the record layout it names, as the standard has it.
- **Conversions**: `LINEAR`, `IDENTICAL` and `RAT_FUNC`, including one over a
  straight line (a period shown as a frequency) and one with a square in it,
  whose two answers are chosen between by the parameter's limits. A
  `COMPU_TAB` of numbers is looked up, interpolated between its rows for
  `TAB_INTP` and not for `TAB_NOINTP`, which uses the table's default for a
  raw value it does not list. A `FORM` formula is worked out, with `X1` the
  raw value; it can be written only where the file gives `FORMULA_INV` as
  well. A formula is checked when the file is read: arithmetic, comparisons,
  bit operations and the usual functions (`sqrt`, `exp`, `log`, `sin` ...)
  only, so nothing in an A2L is ever run as code. Anything else -- a formula
  with more than that in it, a conversion of another kind -- and the value is
  shown **raw**, with *raw* as its unit: a true raw number rather than a wrong
  converted one.
- **Named values** from a `COMPU_VTAB` or `COMPU_VTAB_RANGE`: shown as *Run (1)*,
  and written as the name, the number, or both.
- **How a value is shown**: to the places after the point the `FORMAT` asks
  for -- the parameter's own, then its conversion's, so `%6.1` shows 13.2 --
  and with the parameter's own `PHYS_UNIT` where it gives one, in place of its
  conversion's. A value shown raw is shown as it is, whatever either says.
- **Text**: an `ASCII` characteristic, as many bytes as its `NUMBER` or
  `MATRIX_DIM` says, is read as text up to its first NUL and written as text,
  with NULs after it to fill the space; text longer than the space is not
  written.
- **Groups and functions**: `GROUP` (with `REF_CHARACTERISTIC`,
  `REF_MEASUREMENT` and `SUB_GROUP`), `FUNCTION` (with what it defines, uses and
  puts out, and `SUB_FUNCTION`), and the `FUNCTION_LIST` an older file puts in a
  parameter, for the **Group** box.
- **Bit masks.** A masked value is read as its own bits. Writing one reads the
  stored value first and puts the other bits back as they were.
- **Byte order**: the parameter's own where it states one, then the file's, and
  only then what the slave said when it connected.
- **Address extensions**: a parameter's `ECU_ADDRESS_EXTENSION` goes out with
  its address, for a slave with more than one address space.
- **Older files**, from before `ECU_ADDRESS`: a measurement's address is taken
  from the `KP_BLOB` in its `IF_DATA`, for the CCP, ETK and KWP2000 layouts.
- **Included files** (`/include`), beside the file that names them.
- **The EPK**: `EPK` and `ADDR_EPK` in `MOD_PAR` name the software build the
  file was made for and where the slave keeps its own. On connecting, or on
  loading the file while connected, the slave's is read and compared, and a
  different one is a warning in the Event Log: an A2L for another build has
  addresses that may be wrong, and reads and writes there go to the wrong
  place without anything else saying so.
- **`MAX_DLC_REQUIRED`** in `XCP_ON_CAN`: every command is sent as a full
  eight-byte frame, for a slave that ignores a shorter one.
- **Arrays**: a measurement with `MATRIX_DIM` or `ARRAY_SIZE`, and a `VAL_BLK`
  characteristic. The row shows the type and how many, `UWORD[16]`.
  Double-click it to read every value, which opens it into a row for each,
  `name[0]` onwards; or expand it and read, edit or tick *Plot* on one value.
  The output gives the whole row on one line. An array is written one value at
  a time, not as a whole. Up to 1,024 values are read in one go; a bigger array,
  up to 4,096 values, is still opened, and its values read one at a time.
- **Arrays of two and three dimensions**, shown as `UBYTE[2][24]`. The values
  are read in the order they are stored and named the way C names them, so the
  names count up with the last index changing fastest: `name[0][0]` to
  `name[0][23]`, then `name[1][0]`. An A2L gives the sizes as `MATRIX_DIM 24 2`
  and stores the values row by row unless it says column by column; row by row,
  the first size is the one that changes fastest, so that is two rows of
  twenty-four. Generators do not all agree on this, so the array's tooltip says
  what the file wrote and how it was taken. If the sizes look the wrong way
  round for your controller, the values are still each at the right address,
  in order: only the split into rows is in question.

## Curves and maps

A **CURVE** is values over one axis, a **MAP** values over two, and an
**AXIS_PTS** is axis points kept apart, which several curves and maps share.
Double-click one to open it as a table, with its axes along the top and down
the side, and read it. Each cell is a value of its own: type into it to write
it, after unlocking CAL, as for any characteristic. Expand the row in the list
to read, write or plot one value from it. A map's values are named `[x][y]` --
`IgnitionMap[2][3]` is the third speed and the fourth load -- whichever way
round they are stored, and a curve's own axis points are `IdleCurve:x[0]`
onwards, which are written the same way.

Where everything is comes from the **record layout**, which lists the parts in
order: the number of axis points, the points, the values, and anything else a
generator keeps there. Each part starts after the one before it, moved on to
its type's alignment (`ALIGNMENT_*` in the layout or `MOD_COMMON`, and
otherwise the type's own size). Where the number of points is kept in the
slave (`NO_AXIS_PTS_X`), it is read first, and the table laid out from it: a
curve of six points takes less room than one of eight, unless the layout says
`STATIC_RECORD_LAYOUT`. Until then the list shows the type as `UWORD[?]`.

The axes read are those whose points are somewhere to be read: kept with the
curve (`STD_AXIS`), in an `AXIS_PTS` (`COM_AXIS`, through `AXIS_PTS_REF`), or
worked out from the file (`FIX_AXIS`, by `FIX_AXIS_PAR`, `FIX_AXIS_PAR_DIST` or
`FIX_AXIS_PAR_LIST`), which are shown and cannot be typed into because the
slave keeps none. The demo device has one of each: `IdleCurve`, whose number of
points is in the slave, and `IgnitionMap`, with a shared speed axis and a fixed
load axis.

## What is not read

Listed, in grey, and not read: three-dimensional maps, an axis that is
a `RES_AXIS` or a `CURVE_AXIS` or is stored as differences, a part of a
record reached through a pointer, arrays of more than 4,096 values, a
measurement the file gives no address, and a characteristic whose record
layout is missing. They are listed so that
the file is seen whole; hover over
one for why. A characteristic marked `READ_ONLY` is read and cannot be edited.
Hovering over any parameter gives its description, address, type and limits.

Where the A2L gives the XCP on CAN identifiers, loading it says so in the pane's
output, and **IDs from A2L** puts them in the boxes.

## Limits

An A2L gives each characteristic a lower and an upper limit: whoever wrote it
saying what the controller is meant to be given. They are in the parameter's
tooltip. A value typed outside them is asked about before it is sent, every
time, with the limits shown; *Cancel* is the default and puts the cell back as
it was. Limits of 0 and 0 are a generator with nothing to say, and stop
nothing. From the [console](console.md), `xcp.write(name, value)` refuses a
value outside the limits, and `xcp.write(name, value, beyond_limits=True)` is
how a script says it is meant.

The A2L is loaded again next time. One kept outside the workspace can be copied
into it when you load it, so it travels with the workspace; see
[Workspaces](workspaces.md).
