# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""How wide a list's columns are, and who decides: pycangui or the user.

Qt offers a column that fits its contents and cannot be dragged, or one that
can be dragged and never fits itself. Neither is enough on its own: fitted
columns let one long name push every other column off the pane, and dragged
ones start at widths nobody chose. So there are two ways, picked in *Tools >
Settings > Column widths*:

* **Automatic** -- every column fits what is in it, as it always did.
* **Manual** -- a column fits what is in it up to a limit, until it is dragged;
  from then on it is as wide as it was dragged to, and that is remembered.
  Double-clicking a column's edge gives it back to fitting.

One of these per list. The setting is the workspace's, and changing it
changes every list that is open.
"""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTreeView

#: ``"auto"`` or ``"manual"``.
MODE_KEY = "ui.column_widths"
AUTO, MANUAL = "auto", "manual"
MODES = (AUTO, MANUAL)

#: The widest a column makes itself in Manual. Past this it takes dragging:
#: the point of the mode is that one long name does not have the pane.
WIDEST_FITTED = 260

#: How long after the last drag the widths are written down. A drag is many
#: resizes, and the settings file is written whole each time.
SAVE_AFTER_MS = 400

_open: weakref.WeakSet[ColumnWidths] = weakref.WeakSet()


def mode(settings) -> str:
    said = settings.get(MODE_KEY, AUTO) if settings is not None else AUTO
    return said if said in MODES else AUTO


def set_mode(settings, wanted: str) -> None:
    """Choose, and have every open list follow."""
    settings.set(MODE_KEY, wanted if wanted in MODES else AUTO)
    for widths in list(_open):
        widths.apply()


class ColumnWidths(QObject):
    """The widths of the columns of one tree, or of one table.

    ``key`` names the list in the settings, so that what was dragged is
    remembered; lists of the same kind share it. ``stretch`` is a column that
    takes the room left over in Automatic. The last column always runs to the
    edge when ``last_stretches``, and so is never one that is dragged.

    ``fixed`` is column -> width, for a column whose contents are not to be
    trusted to say how wide it should be: that wide in Automatic whatever is
    in it, and at least that wide in Manual until it is dragged.

    ``refit_ms`` is how long Manual waits before fitting to new rows, for a
    list that gains them many times a second: fitting is a measuring of
    every row in view, and a trace would otherwise spend its time on it.
    """

    def __init__(
        self,
        view: QAbstractItemView,
        settings,
        key: str,
        stretch: int | None = None,
        last_stretches: bool = True,
        fixed: dict[int, int] | None = None,
        refit_ms: int = 0,
    ) -> None:
        super().__init__(view)
        self._view = view
        self._settings = settings
        self._key = f"columns.{key}"
        self._stretch = stretch
        self._last_stretches = last_stretches
        self._fixed = dict(fixed or {})
        self._fitting = False
        #: Column -> width, for the ones somebody has dragged.
        self._dragged: dict[int, int] = self._saved()
        self._fit_soon = QTimer(self, singleShot=True, interval=refit_ms, timeout=self.fit)
        self._save_soon = QTimer(self, singleShot=True, interval=SAVE_AFTER_MS, timeout=self._save)

        header = self._header()
        header.sectionResized.connect(self._on_resized)
        header.sectionHandleDoubleClicked.connect(self._on_handle_double_clicked)
        model = view.model()
        # Not dataChanged: a list of live values changes ten times a second,
        # and columns that followed it would never keep still.
        for changed in (model.rowsInserted, model.modelReset):
            changed.connect(self._contents_changed)
        if isinstance(view, QTreeView):
            view.expanded.connect(self._contents_changed)
        _open.add(self)
        self.apply()

    def _header(self) -> QHeaderView:
        view = self._view
        return view.header() if isinstance(view, QTreeView) else view.horizontalHeader()

    # --- which way --------------------------------------------------------------------
    @property
    def manual(self) -> bool:
        return mode(self._settings) == MANUAL

    def apply(self) -> None:
        """Set the columns up for the mode chosen."""
        header = self._header()
        self._fitting = True
        try:
            if self.manual:
                header.setSectionResizeMode(QHeaderView.Interactive)
                header.setStretchLastSection(True)
            else:
                header.setSectionResizeMode(QHeaderView.ResizeToContents)
                if self._stretch is not None:
                    header.setSectionResizeMode(self._stretch, QHeaderView.Stretch)
                for column, width in self._fixed.items():
                    header.setSectionResizeMode(column, QHeaderView.Fixed)
                    header.resizeSection(column, width)
                header.setStretchLastSection(self._last_stretches)
        finally:
            self._fitting = False
        if self.manual:
            self.fit()

    # --- manual: fitted until dragged ---------------------------------------------------
    def fit(self) -> None:
        """Give each column its dragged width, or fit it, up to the limit."""
        if not self.manual:
            return
        header = self._header()
        self._fitting = True
        try:
            last = self._last()
            for column in range(header.count()):
                if column == last or header.isSectionHidden(column):
                    continue  # the last runs to the edge
                if column in self._dragged:
                    header.resizeSection(column, self._dragged[column])
                    continue
                # The limit is on what the rows ask for. What the heading asks
                # for is short, or is a width somebody gave it on purpose.
                wanted = min(self._view.sizeHintForColumn(column), WIDEST_FITTED)
                wanted = max(wanted, header.sectionSizeHint(column), self._fixed.get(column, 0))
                header.resizeSection(column, wanted)
        finally:
            self._fitting = False

    def _last(self) -> int:
        """The column showing furthest right: columns can be moved, and hidden."""
        header = self._header()
        for place in range(header.count() - 1, -1, -1):
            column = header.logicalIndex(place)
            if not header.isSectionHidden(column):
                return column
        return -1

    def _contents_changed(self, *_what) -> None:
        # Not started again while it is running: rows that keep arriving
        # would put the fitting off for as long as they did.
        if self.manual and not self._fit_soon.isActive():
            self._fit_soon.start()

    def _on_resized(self, column: int, old: int, new: int) -> None:
        if self._fitting or not self.manual or column == self._last():
            return
        if old <= 0 or new <= 0:
            # Hidden, or shown again: neither is a drag. A column that comes
            # back is fitted like any other that nobody has set.
            if new > 0:
                self._fit_soon.start()
            return
        self._dragged[column] = new
        self._save_soon.start()

    def _on_handle_double_clicked(self, column: int) -> None:
        """Back to fitting: the tree has just fitted it, and that counted as a drag."""
        if self._dragged.pop(column, None) is not None:
            self._save_soon.start()
        self._fit_soon.start()

    # --- remembered ---------------------------------------------------------------------
    def _saved(self) -> dict[int, int]:
        said = self._settings.get(self._key, {}) if self._settings is not None else {}
        try:
            return {int(column): int(width) for column, width in dict(said).items()}
        except (TypeError, ValueError):
            return {}  # a hand-edited settings.json

    def _save(self) -> None:
        if self._settings is None:
            return
        if self._dragged:
            self._settings.set(self._key, {str(c): w for c, w in sorted(self._dragged.items())})
        else:
            self._settings.remove(self._key)
