# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The every-call plugin's hooks: copied into the workspace's hooks folder."""


def greeting(name, *, ctx):
    """What to call somebody."""
    return f"Hello {name}"
