"""The firmware plugin: program download over CANopen.

Split in two so the half that matters can be tested without a bus -- program.py
is the sequence and knows nothing about Qt, plugin.py is the pane.
"""
