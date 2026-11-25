# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The every-call plugin's helper: says a port, then waits for its stdin to close."""

import sys

print(4712, flush=True)
sys.stdin.read()
