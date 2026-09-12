# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The CANopen DCF compare plugin.

Split in two so the half that matters can be tested without a bus or a window --
compare.py is the comparison and knows about neither, plugin.py is the pane.
"""
