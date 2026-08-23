"""Trace dock: chronological list, or one row per id with the latest data.

Every frame gets a *kind* label (hook ``trace.frame_kind``, default CANopen
classifier) and a *group*; the Filter menu hides whole groups.  Filtering is
done with proxy models so hidden frames are still recorded and reappear when
the group is re-enabled.
"""

from __future__ import annotations

from collections.abc import Callable

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
from pycangui.core.classify import GROUPS, classify, group_of
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
        # Extra labellers tried after the hook, before the CANopen classifier (DBC names)
        self.classifiers: list[Callable[[Frame], str | None]] = []
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
        """Label precedence: the user hook first, then what the CAN id says on
        its own (CANopen's predefined connection set), then the other labellers
        -- DBC message names, J1939 PGNs, UDS and XCP.

        The id-based label wins over a database name on purpose: "TxPDO1 n5"
        says which node sent it, which a DBC message name cannot.  The database
        still names the signals in the Signals and Plot panes.

        The filter group always comes from the id, so a PDO stays under PDO
        whatever it ends up being called.
        """
        for f in frames:
            co_kind, co_group = classify(f.can_id, f.extended)
            kind = self.hooks.call("trace", "frame_kind", f)
            if kind is None and not co_kind:
                for fn in self.classifiers:
                    if kind := fn(f):
                        break
            f.kind = kind or co_kind
            f.group = co_group if co_group != "Other" else group_of(f.kind)

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
