# Security policy

## Reporting a vulnerability

Please report it privately rather than in a public issue:
**[report a vulnerability](https://github.com/davhodg/pycangui/security/advisories/new)**,
which is also under the repository's *Security* tab.

Say what you found, how to make it happen, and which version or commit you
were running -- *Help > About pycangui* has a box to copy that from.

pycangui is looked after by one person in their own time, so there is no
promised response time.  Every report is read and answered, the fix and the
advisory are published together, and you are credited in the advisory unless
you would rather not be.

## Versions that get fixes

The latest release and `master`.  Older releases are not patched: upgrading
is the fix.

## What is and is not a vulnerability

pycangui runs Python you give it, on purpose.  Hook files, simulated nodes,
plugins, the Python console and *Run script* all execute code.  The hooks in a
workspace run as soon as it is opened, and its simulated nodes when they are
started -- which for the demo nodes means connecting the demo channel.  A
workspace or a plugin from somebody else is code from somebody else, to be
trusted as such.  That is the design, not a flaw in it.

What **is** worth reporting:

- a way to run code, or to write outside the folder you chose, from a file
  that is meant to be data -- a DBC, EDS, DCF, A2L, firmware image, log or
  measurement file;
- pycangui going online when you did not ask it to.  It does so only for
  *Help > Check for updates*, for a GitHub page you chose to open, and to
  install the optional MDF reader from PyPI after you agreed to it;
- transmitting onto a real bus without asking first -- unless you told it to
  stop asking -- or after you declined.

What is **not**, here:

- CAN having no authentication.  Anything on a bus can send anything, and
  attacks on CAN, CANopen, UDS, J1939 or XCP themselves, or on the devices
  that speak them, belong with those devices' makers;
- problems in the libraries pycangui is built on -- Qt, python-can, canopen,
  cantools, udsoncan and the rest.  Report those upstream; do tell us if
  pycangui uses one unsafely.
