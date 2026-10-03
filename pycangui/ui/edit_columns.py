# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Editing a tree's rows in some columns and not the rest.

A QTreeWidgetItem is editable or not as a whole row: mark it editable for its
value and its index, name and type can be typed over as well. Nothing is
written when they are -- the change is ignored -- which is worse than refusing,
since the cell keeps the typing and looks changed. This refuses.
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import QStyledItemDelegate, QWidget


class EditColumns(QStyledItemDelegate):
    """Open an editor in these columns only; the rest stay as they are drawn."""

    def __init__(self, columns: Iterable[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = frozenset(columns)

    def createEditor(self, parent, option, index):
        if index.column() not in self.columns:
            return None
        return super().createEditor(parent, option, index)
