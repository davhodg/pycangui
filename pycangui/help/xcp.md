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
press **Load A2L...** and the MEASUREMENTs and CHARACTERISTICs appear.
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

The A2L is loaded again next time. One kept outside the workspace can be copied
into it when you load it, so it travels with the workspace; see
[Workspaces](workspaces.md).
