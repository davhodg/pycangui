# Security policy

## Reporting a vulnerability

Please report it privately rather than in a public issue:
**[report a vulnerability](https://github.com/davhodg/pycangui/security/advisories/new)**,
which is also under the repository's *Security* tab.

Say what you found, how to make it happen, and which version or commit you
were running -- *Help > About pycangui* has a box to copy that from.

Every report is read and answered as soon as possible. The fix and the
advisory are published together, and you are credited in the advisory unless
you would rather not be.

## Versions that get fixes

The latest release and `master`. Older releases are not patched: upgrading
is the fix.

## What to report

A way for someone else to run code, read or write files, or send onto a network or a bus
through pycangui without your say: from a file meant to be data (a DBC, EDS, A2L, log or
firmware image), from another user on the same computer, or by a plugin installed although it
does not match its list's checksum.

Workspaces, hook files, scripts and plugins are code, and run as code: that is by design.
