[&larr; Contents](manual.md)

# Hooks

A **hook** is a small Python function pycangui calls at a decision point where
the answer depends on your product rather than on any standard.  Which EDS
belongs to this node.  What those five manufacturer bytes in an emergency mean.
What the seed-to-key algorithm is.  Nobody outside the company that built the
device can answer any of those, so pycangui asks rather than guesses.

Hooks are the smaller half of a pair.  A hook answers a question pycangui
already knows to ask, from a fixed list of them; a [plugin](plugins.md) is the
other half -- code that adds something that was not there at all.  If you find
yourself wanting a hook that does not exist, what you want is probably a plugin.

## Where they are

`workspaces\<name>\hooks\*.py`, beside the EDS files.  They belong to the
[workspace](workspaces.md) because what a maker's objects mean is knowledge
about that product, and switching workspace should bring the right answers with
it.

On first run each file is copied there complete, with the default behaviour and
commented examples already in it, so there is never an empty file to start
from.  **Your copies are never overwritten.**  *Tools > Update hook files* adds
anything new that a later version of pycangui introduced, leaving what you have
written alone; *Tools > Reload hooks* applies an edit without a restart.

## What happens when one runs

For each decision, in order:

1. your function, if the file loaded and defines it;
2. if it raises, the traceback goes to the [Event Log](event-log.md) and
   pycangui carries on with the default;
3. if it returns `None`, that means "do the normal thing" and the default runs;
4. otherwise your answer is used.

So a hook file with a mistake in it costs you that answer, not the
application -- and returning `None` for the cases you do not care about is how
you handle one node without having to handle all of them.

Every hook is handed `ctx`, which carries `ctx.log(text)`, `ctx.warn(text)` and
`ctx.error(text)`: the same three levels the rest of the tool reports at, so
anything a hook wants to say arrives where everything else does.

## The files, and what each one answers

| File | Answers |
|---|---|
| `canopen.py` | which EDS belongs to a node, what to call it, what its manufacturer emergency bytes mean, and how an object should be shown -- name, unit, scaling, limits, named bits |
| `uds.py` | the seed-to-key algorithm, the names and descriptions of data identifiers, routines and sessions, how a DID decodes and encodes, DTC descriptions, and what the erase and check routines are sent |
| `j1939.py` | PGN names, SPN names and failure-mode descriptions |
| `xcp.py` | the seed-to-key algorithm for CAL and the other resources |
| `trace.py` | what to call a frame the trace does not recognise |

The tables in `j1939.py` are filled in rather than hidden inside pycangui:
the PGN names and the failure modes are there to read, and adding a proprietary
PGN is a line in a dictionary.  What is deliberately *not* shipped is anything
copyrighted -- the several thousand SPN names from SAE J1939-71, and UDS DTC
descriptions, which no standard defines at all.

The per-protocol pages say which hook does what in context:
[CANopen](canopen.md), [UDS](uds.md), [J1939](j1939.md) and [XCP](xcp.md).

## Hooks, scripts and plugins

Three ways to put your own code in, and they are not interchangeable:

- a **hook** answers one question pycangui already asks;
- a **script** in the [Python Console](console.md) does something once, or on
  demand, against the live objects;
- a [**plugin**](plugins.md) adds a pane, a menu entry or a toolbar button that
  was not there before, and stays until you remove it.
