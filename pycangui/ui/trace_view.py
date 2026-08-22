"""Trace dock: chronological list, or one row per id with the latest data.

Every frame gets a *kind* label (hook ``trace.frame_kind``, default CANopen
classifier) and a *group*; the Filter menu hides whole groups.  Filtering is
done with proxy models so hidden frames are still recorded and reappear when
the group is re-enabled.
"""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import Frame
from pycangui.core.classify import GROUPS, classify
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.latest_model import ROLE_GROUP, LatestModel
from pycangui.ui.trace_model import TraceModel

MODES = ("Chronological", "Latest per ID")


class _GroupFilter(QSortFilterProxyModel):
    """Hides rows whose group is in ``hidden``; sorts on the raw-value role."""

    def __init__(self) -> None:
        super().__init__()
        self.hidden: set[str] = set()
        self.setSortRole(Qt.UserRole)
        self.setDynamicSortFilter(True)

    def filterAcceptsRow(self, source_row: int, parent: QModelIndex) -> bool:
        if not self.hidden:
            return True
        index = self.sourceModel().index(source_row, 0, parent)
        return self.sourceModel().data(index, ROLE_GROUP) not in self.hidden

    def set_hidden(self, hidden: set[str]) -> None:
        self.hidden = set(hidden)
        self.invalidateRowsFilter()


def _table(model, font: QFont) -> QTableView:
    table = QTableView()
    table.setModel(model)
    table.setFont(font)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(18)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)
    return table


class TraceView(QWidget):
    def __init__(self, hooks: Hooks, ctx: Context) -> None:
        super().__init__()
        self.hooks = hooks
        self.ctx = ctx
        mono = QFont("Consolas", 9)
        self.model = TraceModel()
        self.latest = LatestModel()
        self._trace_proxy = _GroupFilter()
        self._trace_proxy.setSourceModel(self.model)
        self._latest_proxy = _GroupFilter()
        self._latest_proxy.setSourceModel(self.latest)

        self.table = _table(self._trace_proxy, mono)
        self.latest_table = _table(self._latest_proxy, mono)
        self.latest_table.setSortingEnabled(True)
        self.latest_table.sortByColumn(0, Qt.AscendingOrder)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.latest_table)

        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.autoscroll = QCheckBox("Autoscroll")
        self.autoscroll.setChecked(True)

        # Filter menu: one checkable action per group, all shown by default
        self.filter_button = QToolButton()
        self.filter_button.setText("Filter")
        self.filter_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.filter_button)
        self._group_actions: dict[str, QAction] = {}
        hidden = set(ctx.settings.get("trace.hidden_groups", []))
        for group in GROUPS:
            action = menu.addAction(group)
            action.setCheckable(True)
            action.setChecked(group not in hidden)
            action.toggled.connect(self._on_filter_changed)
            self._group_actions[group] = action
        menu.addSeparator()
        menu.addAction("Show all", self._show_all)
        self.filter_button.setMenu(menu)
        self._apply_filter(save=False)

        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("View:"))
        bar.addWidget(self.mode)
        bar.addWidget(self.autoscroll)
        bar.addWidget(self.filter_button)
        bar.addStretch()
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.stack)

        self._rate_timer = QTimer(self, interval=500, timeout=self.latest.refresh_rates)
        self._rate_timer.start()

    # --- filtering -----------------------------------------------------------------
    def hidden_groups(self) -> set[str]:
        return {g for g, a in self._group_actions.items() if not a.isChecked()}

    def _on_filter_changed(self, _checked: bool) -> None:
        self._apply_filter(save=True)

    def _apply_filter(self, save: bool) -> None:
        hidden = self.hidden_groups()
        self._trace_proxy.set_hidden(hidden)
        self._latest_proxy.set_hidden(hidden)
        self.filter_button.setText("Filter" if not hidden else f"Filter ({len(hidden)} hidden)")
        if save:
            self.ctx.settings.set("trace.hidden_groups", sorted(hidden))

    def _show_all(self) -> None:
        for action in self._group_actions.values():
            action.setChecked(True)

    # --- frames ----------------------------------------------------------------------
    def _classify(self, frames: list[Frame]) -> None:
        for f in frames:
            kind = self.hooks.call("trace", "frame_kind", f)
            f.kind = kind if kind is not None else classify(f.can_id, f.extended)[0]

    def _on_mode_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.autoscroll.setEnabled(index == 0)

    @Slot(list)
    def on_frames(self, frames: list[Frame]) -> None:
        self._classify(frames)
        self.model.append(frames)
        self.latest.append(frames)
        if self.autoscroll.isChecked() and self.stack.currentIndex() == 0:
            self.table.scrollToBottom()

    @Slot()
    def clear(self) -> None:
        self.model.clear()
        self.latest.clear()
