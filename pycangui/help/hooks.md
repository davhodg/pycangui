[&larr; Contents](manual.md)

# Hooks

A **hook** is a small Python function pycangui calls at a decision point where
the answer depends on your product rather than on any standard. Which EDS
belongs to this node. What those five manufacturer bytes in an emergency mean.
What the seed-to-key algorithm is. Nobody outside the company that built the
device can answer any of those.

Hooks are the smaller half of a pair. A hook answers a question pycangui
already knows to ask, from a fixed list of them; a [plugin](plugins.md) is the
other half -- code that adds something that was not there at all. If you find
yourself wanting a hook that does not exist, what you want is probably a plugin.

## Where they are

`workspaces\<name>\hooks\*.py`, beside the EDS files. They belong to the
[workspace](workspaces.md) because what a maker's objects mean is knowledge
about that product, and switching workspace should bring the right answers with
it.

On first run each file is copied there complete, with the default behaviour and
commented examples already in it, so there is never an empty file to start
from. **A file you have changed is designed to be left as it is.** One you have not
changed is still pycangui's, so when a later version ships a better one it is
simply replaced, and the Event Log says so. A changed one is left as it is,
and the log says once that a newer version exists. The same goes for the
[simulated nodes](virtual.md) in `nodes/`.

They are also yours in the other sense. The templates are released under
MIT-0, which claims no copyright and asks for no credit -- the first lines of
each file say so -- so a seed-key algorithm or a table of your own identifiers
written into one carries no conditions from pycangui. Keep it private, share
it, or ship it with your product.

*Tools > Open hooks folder* opens them, and *Tools > Reload hooks* reads the
files again, so an edit of your own takes effect without restarting. It changes nothing on disk.

*Tools > Reset > Restore supplied files...* is the way back from an edit that
has gone wrong, and the way to take a newer version over one you have changed:
it puts pycangui's own version of the hook and node files you tick back, and
renames yours to `canopen.py.bak` rather than deleting it, so an afternoon's
work is still there to copy out of. Files that already match what pycangui
ships are shown greyed, since there is nothing to restore and nothing to lose.

## Keeping up with a new pycangui

Your files were written against the pycangui you had. A later version can ask
questions yours have no answer for, and can change what it passes to a question
it already asked. Both are handled when that version first runs, because
keeping up with pycangui is pycangui's job rather than something to remember
after every update.

**A hook that is new** is appended to the end of the file that owns it, with
whatever it needs to work -- its imports, and the tables it reads -- under a
`# --- hooks pycangui added; yours to edit ---` line. It arrives as the
built-in default, so it behaves exactly as it did before it was there. Nothing
you wrote is touched, and a hook you deleted on purpose stays deleted: what
arrives is decided by what is *new*, not by what is missing. If appending
would somehow stop the file loading, the file is put back as it was and the
[Event Log](event-log.md) says so.

**A hook whose signature changed** is the awkward one, because your version is
still there and still has the right name. Every hook you have written is
checked against the one pycangui calls as the file loads, and one that cannot
be called is reported before anything has happened:

```
hooks/uds.py: security_key is not being used, because this version of
pycangui changed it -- it does not take ctx.
    yours: security_key(level, seed)
    pycangui: security_key(level, seed, *, ctx)
    The built-in default is running instead. See Hooks in the manual (Help > Documentation).
```

That is deliberately a line in the log at startup rather than a traceback
later: the alternative is finding out during a flash session that the default
seed-to-key has been running all along. Fix the signature, *Reload hooks*, and
it is used again.

*Tools > Add missing hooks* does the appending on demand, for every hook your
files lack rather than only the new ones. It is how to get back one you
deleted; you should not otherwise need it.

## What happens when one runs

For each decision, in order:

1. your function, if the file loaded and defines it;
2. if it raises, the traceback goes to the [Event Log](event-log.md) and pycangui carries on with the default;
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
| `canopen.py` | which EDS belongs to a node, what to call it, what its manufacturer emergency bytes mean, how an object should be shown -- name, unit, scaling, limits, named bits -- and how to log in to an access level |
| `uds.py` | the seed-to-key algorithm, the names and descriptions of data identifiers, routines and sessions, how a DID decodes and encodes, DTC descriptions, and what the erase and check routines are sent |
| `j1939.py` | PGN names, SPN names and failure-mode descriptions |
| `xcp.py` | the seed-to-key algorithm for CAL and the other resources |
| `transmit.py` | a checksum your device computes its own way, for a message sent from [CAN Transmit](transmit.md) |
| `trace.py` | what to call a frame the trace does not recognise |
| `startup.py` | what to do once the window is up -- see below |

The per-protocol pages say which hook does what in context:
[CANopen](canopen.md), [UDS](uds.md), [J1939](j1939.md) and [XCP](xcp.md).

## The startup hook

`startup.py` is the odd one out. Every other hook is *asked* something and
returns an answer; this one is simply told that the window is open, and does
whatever your setup needs doing -- connect the channel this product lives on,
load its database, open the panes the job wants. It is the thing somebody
would otherwise do by hand every morning, written down once.

It runs last: the channels, the protocol managers, the plugins and the saved
layout all exist by the time it is called, so a pane it opens is not put away
again by the layout arriving over the top of it. It is handed the main window,
and through it everything the [Python Console](console.md) has, under the same
names -- what works in the console works here.

**Connecting.** Use `window.connect_channel(name, interface, channel, bitrate)`
rather than reaching for the bus underneath it. That joins a bus exactly as the
Connect button does, which means a real interface still raises the question
about the bitrate, once a session, as it would if you had pressed the button
yourself. A [workspace](workspaces.md) is a folder that gets copied and shared,
and one that silently joined a live bus on somebody else's bench
because they opened it would be a bad thing to have built. A `virtual` channel
does not ask, because it is designed to stay inside pycangui.

**It is designed not to stop pycangui starting.** If it raises, the traceback
goes to the Event Log and the window opens anyway -- the tool you would need in
order to fix a broken startup hook is the one that would not have started.

## Hooks, scripts and plugins

Three ways to put your own code in, and they are not interchangeable:

- a **hook** answers one question pycangui already asks;
- a **script** in the [Python Console](console.md) does something once, or on
  demand, against the live objects;
- a [**plugin**](plugins.md) adds a pane, a menu entry or a toolbar button that
  was not there before, and stays until you remove it.
