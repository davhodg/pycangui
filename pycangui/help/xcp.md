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

Two smaller differences are handled without asking. CCP is a Motorola-order
protocol, so values are read big-endian, where XCP uses the order the slave
declares on connecting. And every CCP command carries a counter which the
answer echoes; pycangui sends one and does not insist on it coming back
correct, because slaves in the field are careless with it and refusing a good
answer over a counter would waste an afternoon.

Set the identifiers, Connect,
press **Load A2L...** and the MEASUREMENTs and CHARACTERISTICs appear. A file
big enough to take more than a moment shows how far it has got, and *Cancel*
while it is being read leaves the A2L there was in use.
Double-click to read one, edit a characteristic's value to write it (unlock CAL
first -- the seed-to-key algorithm is
[`hooks/xcp.py::compute_key`](hooks.md), or the seed and key DLL named
with **Seed and key DLL...**, which is the same one the [UDS](uds.md) pane
uses and is described there), and tick *Plot* to poll a measurement into the
**Signals and Plot** pane. A real A2L runs to hundreds of parameters, so the
box above the list filters them: match on the name, the address in either hex
or decimal, the type, the unit or the description, with several words all
having to match, and *MEASUREMENT* or *CHARACTERISTIC* narrowing it to one
kind. **Plotted** shows only the ones ticked for the plot, which a filter
would otherwise hide while they went on being read. Which file the names came from is shown under the
bar, and **Remove A2L** forgets it -- including one that has moved since, which
is reported at startup and then sits there marked *(missing)*. The demo device
answers XCP on 0x7A0/0x7A1 and CCP on 0x7B0/0x7B1 at station 1, and both match
`resources/demo.a2l`.

## What is read from an A2L

Single values, and rows of them:

- **Measurements**, and **characteristics** of type `VALUE`. A characteristic's
  data type comes from the record layout it names, as the standard has it.
- **Conversions** that are a straight line -- `LINEAR`, `IDENTICAL`, and a
  `RAT_FUNC` whose coefficients make one -- or one over a straight line, which
  is how a period is stored for something shown as a frequency. Any other -- a
  formula, a table to interpolate -- and the value is shown **raw**, with *raw*
  as its unit: a true raw number rather than a wrong converted one.
- **Named values** from a `COMPU_VTAB` or `COMPU_VTAB_RANGE`: shown as *Run (1)*,
  and written as the name, the number, or both.
- **Bit masks.** A masked value is read as its own bits. Writing one reads the
  stored value first and puts the other bits back as they were.
- **Byte order**: the parameter's own where it states one, then the file's, and
  only then what the slave said when it connected.
- **Address extensions**: a parameter's `ECU_ADDRESS_EXTENSION` goes out with
  its address, for a slave with more than one address space.
- **Older files**, from before `ECU_ADDRESS`: a measurement's address is taken
  from the `KP_BLOB` in its `IF_DATA`, for the CCP, ETK and KWP2000 layouts.
- **Included files** (`/include`), beside the file that names them.
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

Listed, in grey, and not read: curves, maps, text, arrays of more than 4,096
values, a measurement the file gives no address,
and a characteristic whose record layout is missing. They are listed so that
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
