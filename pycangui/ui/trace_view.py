# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 davhodg
"""Trace dock: chronological list, or one row per id with the latest data.

Every frame gets a *kind* label (hook ``trace.frame_kind``, default CANopen
classifier) and a *group*. Three things narrow what is shown, and none of
them discard anything: the filter box matches text against the id, name,
channel and data; the Filter menu hides whole protocol groups or channels; and
Pause holds the display still while capture carries on. All of it is done
with proxy models and a pending queue, so nothing is lost and everything
reappears when the filter is relaxed.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, QTimer, Slot
from PySide6.QtGui import QAction, QFont, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pycangui.core.bus import Frame
from pycangui.core.classify import ERROR_GROUP, GROUPS, group_of
from pycangui.core.context import Context
from pycangui.core.hooks import Hooks
from pycangui.ui.latest_model import COLUMNS as LATEST_COLUMNS
from pycangui.ui.latest_model import (
    ROLE_CHANNEL,
    ROLE_GROUP,
    ROLE_SEARCH,
    STATISTICS,
    LatestModel,
)
from pycangui.ui.persist import remember, remember_columns
from pycangui.ui.trace_model import COLUMNS as TRACE_COLUMNS
from pycangui.ui.trace_model import TraceModel

MODES = ("Chronological", "Latest per ID")
MAX_PENDING = 200_000  # frames held while paused, oldest dropped beyond this


class _TraceFilter(QSortFilterProxyModel):
    """Hides rows by protocol group, by channel, and by a text search.

    The text is matched against the id, the kind, the channel and the data, so
    "185", "txpdo", "drive bus" and "de ad" all work. Several words all have
    to match, which makes "185 tx" mean what it looks like.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hidden_groups: set[str] = set()
        self.hidden_channels: set[str] = set()
        self.terms: list[str] = []
        self.setSortRole(Qt.UserRole)
        self.setDynamicSortFilter(True)

    def filterAcceptsRow(self, source_row: int, parent: QModelIndex) -> bool:
        if not (self.hidden_groups or self.hidden_channels or self.terms):
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, parent)
        if self.hidden_groups and model.data(index, ROLE_GROUP) in self.hidden_groups:
            return False
        if self.hidden_channels and model.data(index, ROLE_CHANNEL) in self.hidden_channels:
            return False
        if self.terms:
            haystack = model.data(index, ROLE_SEARCH) or ""
            return all(term in haystack for term in self.terms)
        return True

    def set_hidden(self, groups: set[str], channels: set[str]) -> None:
        self.hidden_groups = set(groups)
        self.hidden_channels = set(channels)
        self.invalidate()

    def set_text(self, text: str) -> None:
        self.terms = text.lower().split()
        self.invalidate()


#: The widest identifier there is: 29 bits, eight hex digits.
WIDEST_ID = "1FFFFFFF"


def _table(model, font: QFont, id_column: int) -> QTableView:
    table = QTableView()
    table.setModel(model)
    table.setFont(font)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(18)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setStretchLastSection(True)
    # Fixed at eight digits rather than fitted. Fitting measures a sample of
    # rows, and on a bus that starts with 11-bit ids -- CANopen before J1939
    # has claimed an address -- it settled three digits wide and elided every
    # 29-bit id after it to "18...".
    header.setSectionResizeMode(id_column, QHeaderView.Fixed)
    header.resizeSection(id_column, table.fontMetrics().horizontalAdvance(WIDEST_ID) + 16)
    return table


class TraceView(QWidget):
    def __init__(self, hooks: Hooks, ctx: Context, key: str = "trace") -> None:
        super().__init__()
        self.hooks = hooks
        self.ctx = ctx
        #: Where this trace's settled choices are kept. There can be more than
        #: one trace, and the reason to open a second is that it should show
        #: something the first does not -- so the filter, the mode and the
        #: autoscroll belong to the instance rather than to traces in general.
        self.key = key
        self._pending: list[Frame] = []  # frames captured while paused
        # Extra labellers tried after the hook, before the CANopen classifier (DBC names)
        self.classifiers: list[Callable[[Frame], str | None]] = []
        mono = QFont("Consolas", 9)
        self.model = TraceModel()
        self.latest = LatestModel()
        self._trace_proxy = _TraceFilter()
        self._trace_proxy.setSourceModel(self.model)
        self._latest_proxy = _TraceFilter()
        self._latest_proxy.setSourceModel(self.latest)

        self.table = _table(self._trace_proxy, mono, TRACE_COLUMNS.index("ID"))
        self.latest_table = _table(self._latest_proxy, mono, LATEST_COLUMNS.index("ID"))
        self.latest_table.setSortingEnabled(True)
        self.latest_table.sortByColumn(0, Qt.AscendingOrder)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.latest_table)

        self.mode = QComboBox()
        self.mode.setToolTip(
            "Chronological lists every frame as it arrives.\n"
            "Latest per ID keeps one row per identifier, showing its newest\n"
            "data with how often it has been seen and how fast."
        )
        self.mode.addItems(MODES)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        self.autoscroll = QCheckBox("Autoscroll")
        self.autoscroll.setChecked(True)
        self.pause = QCheckBox("Pause")
        self.pause.setToolTip(
            "Hold the display still. Frames carry on being captured and recorded,\n"
            "and appear when you unpause."
        )
        self.pause.toggled.connect(self._on_pause)

        self.search = QLineEdit()
        self.search.setPlaceholderText("filter: id, name, channel or data...")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(self._on_search)

        self.count_label = QLabel("")
        self.count_label.setToolTip("Rows shown of rows captured")

        # Filter menu: one checkable action per group, all shown by default
        self.filter_button = QToolButton()
        self.filter_button.setText("Filter")
        self.filter_button.setToolTip(
            "Hide whole protocol groups or channels. Nothing is discarded:\n"
            "the count reads shown of captured, and unhiding brings it back."
        )
        self.filter_button.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.filter_button)
        self._group_actions: dict[str, QAction] = {}
        hidden = set(ctx.settings.get(f"{key}.hidden_groups", []))
        for group in GROUPS:
            action = menu.addAction(group)
            action.setCheckable(True)
            action.setChecked(group not in hidden)
            action.toggled.connect(self._on_filter_changed)
            self._group_actions[group] = action
        menu.addSeparator()
        self._channel_menu = menu.addMenu("Channels")
        self._channel_actions: dict[str, QAction] = {}
        menu.addSeparator()
        menu.addAction("Show all", self._show_all)
        self.filter_button.setMenu(menu)
        self._apply_filter(save=False)

        # Which columns, per mode: the two tables answer different questions
        # and have nothing in common but the word column. On a button as
        # well as on the header, because right-clicking a header is a
        # convention rather than something anybody can see.
        self._column_menus = {
            0: remember_columns(ctx, f"{key}.trace_columns", self.table),
            1: remember_columns(ctx, f"{key}.latest_columns", self.latest_table, STATISTICS),
        }
        self.columns_button = QToolButton()
        self.columns_button.setText("Columns")
        self.columns_button.setToolTip(
            "Which columns this view shows. The timing statistics -- first\n"
            "seen, shortest, average and longest gap, and jitter -- start\n"
            "hidden, because fifteen columns at once is a table nobody reads."
        )
        self.columns_button.setPopupMode(QToolButton.InstantPopup)
        self.columns_button.setMenu(self._column_menus[self.mode.currentIndex()])

        # Settled choices about how to read the trace, not what is in it.
        # Last of the three, and it has to stay last: restoring a saved mode
        # changes the combo box, which fires currentIndexChanged from inside
        # this constructor, so everything _on_mode_changed reaches for must
        # already exist. It did not, and a workspace left in Latest per ID
        # threw on startup.
        remember(ctx, f"{key}.mode", self.mode)
        remember(ctx, f"{key}.autoscroll", self.autoscroll)

        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("View:"))
        bar.addWidget(self.mode)
        bar.addWidget(self.autoscroll)
        bar.addWidget(self.pause)
        bar.addWidget(self.search)
        bar.addWidget(self.filter_button)
        bar.addWidget(self.columns_button)
        bar.addStretch()
        bar.addWidget(self.count_label)
        bar.addWidget(clear)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addLayout(bar)
        layout.addWidget(self.stack)

        self._rate_timer = QTimer(self, interval=500, timeout=self.latest.refresh_rates)
        self._rate_timer.start()

        for table in (self.table, self.latest_table):
            table.setSelectionBehavior(QAbstractItemView.SelectRows)
            copy = QAction("Copy", table)
            copy.setShortcut(QKeySequence.Copy)
            copy.setShortcutContext(Qt.WidgetShortcut)
            copy.triggered.connect(self.copy_selection)
            table.addAction(copy)
            table.setContextMenuPolicy(Qt.ActionsContextMenu)

    # --- filtering -----------------------------------------------------------------
    def hidden_groups(self) -> set[str]:
        return {g for g, a in self._group_actions.items() if not a.isChecked()}

    def hidden_channels(self) -> set[str]:
        return {c for c, a in self._channel_actions.items() if not a.isChecked()}

    def _note_channel(self, name: str) -> None:
        """Channels appear in the filter menu as soon as one is seen on them."""
        if name in self._channel_actions:
            return
        action = self._channel_menu.addAction(name)
        action.setCheckable(True)
        hidden = set(self.ctx.settings.get(f"{self.key}.hidden_channels", []))
        action.setChecked(name not in hidden)
        action.toggled.connect(self._on_filter_changed)
        self._channel_actions[name] = action

    def _on_filter_changed(self, _checked: bool) -> None:
        self._apply_filter(save=True)

    def _apply_filter(self, save: bool) -> None:
        groups = self.hidden_groups()
        channels = self.hidden_channels()
        self._trace_proxy.set_hidden(groups, channels)
        self._latest_proxy.set_hidden(groups, channels)
        total = len(groups) + len(channels)
        self.filter_button.setText("Filter" if not total else f"Filter ({total} hidden)")
        if save:
            self.ctx.settings.set(f"{self.key}.hidden_groups", sorted(groups))
            self.ctx.settings.set(f"{self.key}.hidden_channels", sorted(channels))
        self._update_count()

    def _show_all(self) -> None:
        for action in (*self._group_actions.values(), *self._channel_actions.values()):
            action.setChecked(True)
        self.search.clear()

    @Slot(str)
    def _on_search(self, text: str) -> None:
        self._trace_proxy.set_text(text)
        self._latest_proxy.set_text(text)
        self._update_count()

    @Slot(bool)
    def _on_pause(self, paused: bool) -> None:
        if not paused and self._pending:
            self._append(self._pending)
            self._pending = []
        self._update_count()

    def _update_count(self) -> None:
        shown = self.stack.currentWidget().model().rowCount()
        total = self.model.rowCount() if self.stack.currentIndex() == 0 else self.latest.rowCount()
        held = f" (+{len(self._pending)} held)" if self._pending else ""
        self.count_label.setText(f"{shown} of {total}{held}")

    # --- frames ----------------------------------------------------------------------
    def _classify(self, frames: list[Frame]) -> None:
        """Name a frame, in order of who has the best claim to know.

        The hook first, because it is this workspace's own answer about this
        product. Then the databases somebody loaded, which name what they
        were written to name. Then each protocol, and only where it has been
        told what it is looking at: the XCP identifiers somebody typed, the
        UDS addresses in the pane, the CANopen ids of nodes that are
        actually there.

        This order used to be the other way about, on the grounds that
        "TxPDO1 n5" says which node sent a frame where a database name
        cannot. True on a CANopen bus. On a bus with no CANopen on it, the
        predefined connection set claims 0x180 to 0x67F and the names in the
        database somebody deliberately loaded never got a look in.

        The group follows the name, so a frame nobody can account for sits
        under Other rather than being filed as a PDO.
        """
        for f in frames:
            if f.error:
                # An error frame is the controller reporting a fault, not a
                # message: its id carries error flags rather than an
                # identifier, so none of the decoders apply to it. Its own
                # group means the Filter menu can hide them, which matters
                # because a bus in trouble produces them faster than anything
                # else on the wire.
                f.kind, f.group = "Bus error", ERROR_GROUP
                continue
            kind = self.hooks.call("trace", "frame_kind", f)
            if kind is None:
                for fn in self.classifiers:
                    if kind := fn(f):
                        break
            f.kind = kind or ""
            f.group = group_of(f.kind) if f.kind else ("J1939" if f.extended else "Other")

    def _on_mode_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.autoscroll.setEnabled(index == 0)
        self.columns_button.setMenu(self._column_menus[index])
        self._update_count()

    @Slot(list)
    def on_frames(self, frames: list[Frame]) -> None:
        self._classify(frames)
        if self.pause.isChecked():
            self._pending.extend(frames)  # captured, just not shown yet
            del self._pending[:-MAX_PENDING]
            self._update_count()
            return
        self._append(frames)

    def _append(self, frames: list[Frame]) -> None:
        for f in frames:
            self._note_channel(f.channel)
        self.model.append(frames)
        self.latest.append(frames)
        if self.autoscroll.isChecked() and self.stack.currentIndex() == 0:
            self.table.scrollToBottom()
        self._update_count()

    @Slot()
    def clear(self) -> None:
        self.model.clear()
        self.latest.clear()
        self._pending = []
        self._update_count()

    # --- copying ---------------------------------------------------------------------
    def copy_selection(self) -> None:
        """Selected rows as tab separated text, ready to paste into a report."""
        view = self.stack.currentWidget()
        model = view.model()
        rows = sorted({i.row() for i in view.selectionModel().selectedIndexes()})
        if not rows:
            return
        columns = range(model.columnCount())
        header = "\t".join(str(model.headerData(c, Qt.Horizontal) or "") for c in columns)
        lines = [header]
        for row in rows:
            lines.append("\t".join(str(model.index(row, c).data() or "").strip() for c in columns))
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.ctx.log(f"Trace: {len(rows)} row(s) copied")
