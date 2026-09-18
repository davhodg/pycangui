[&larr; Contents](manual.md)

# XCP

The **XCP** pane speaks XCP on CAN: set the command/response ids, Connect,
press **Load A2L...** and the MEASUREMENTs and CHARACTERISTICs appear.
Double-click to read one, edit a characteristic's value to write it (unlock CAL
first -- the seed-to-key algorithm is
[`hooks/xcp.py::compute_key`](hooks.md)), and tick *Plot* to poll a measurement into the
**Signals and Plot** pane. A real A2L runs to hundreds of parameters, so the
box above the list filters them: match on the name, the address in either hex
or decimal, the type, the unit or the description, with several words all
having to match, and *MEASUREMENT* or *CHARACTERISTIC* narrowing it to one
kind. **Plotted** shows only the ones ticked for the plot, which a filter
would otherwise hide while they went on being read. Which file the names came from is shown under the
bar, and **Remove A2L** forgets it -- including one that has moved since, which
is reported at startup and then sits there marked *(missing)*. The demo device
answers on 0x7A0/0x7A1 and matches `resources/demo.a2l`.

The A2L is loaded again next time. One kept outside the workspace can be copied
into it when you load it, so it travels with the workspace; see
[Workspaces](workspaces.md).
