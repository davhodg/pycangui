# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""The CiA 402 plugin: driving a motor controller through its state machine.

Split in two so the half that matters can be tested without a bus -- drive.py
is the state machine and the profile's objects and knows nothing about Qt,
plugin.py is the pane.
"""
