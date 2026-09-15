[&larr; Contents](manual.md)

# XCP

The **XCP** pane speaks XCP on CAN: set the command/response ids, Connect,
press **Load A2L...** and the MEASUREMENTs and CHARACTERISTICs appear.
Double-click to read one, edit a characteristic's value to write it (unlock CAL
first -- the seed-to-key algorithm is
[`hooks/xcp.py::compute_key`](hooks.md)), and tick *Plot* to poll a measurement into the
**Signals and Plot** pane. The demo device answers on 0x7A0/0x7A1 and matches
`resources/demo.a2l`.

The A2L is loaded again next time. One kept outside the workspace can be copied
into it when you load it, so it travels with the workspace; see
[Workspaces](workspaces.md).
