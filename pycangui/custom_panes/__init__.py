# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""User-composed custom_panes: a named group of objects laid out as a form.

The object dictionary shows one object per row sorted by index, which is the
right way to *find* an object and the wrong way to *use* one. Reading six
related parameters means six double-clicks in three parts of a tree of fifteen
hundred rows, and doing it again tomorrow means finding them again.

A pane is that group, named and kept: a title, and a list of objects each with
a label and a way of being shown. Building one takes no code -- pick the
objects and say what they are -- and it opens as a dock like any other pane, so
two custom_panes can sit side by side comparing two nodes.

The custom_panes are readable JSON in the workspace, beside the hooks that give their
objects meaning, so one can be edited by hand and sent to someone else along with
the EDS it belongs to.
"""

from pycangui.custom_panes.model import (
    KINDS,
    CustomPane,
    Field,
    delete,
    directory,
    load,
    names,
    path_for,
    problems,
    save,
    why_not,
)
from pycangui.custom_panes.source import FileSource, NodeSource, Source

__all__ = [
    "KINDS",
    "CustomPane",
    "Field",
    "FileSource",
    "NodeSource",
    "Source",
    "delete",
    "directory",
    "load",
    "names",
    "path_for",
    "problems",
    "save",
    "why_not",
]
