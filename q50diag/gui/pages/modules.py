"""Modules page: every known and discovered diagnostic module, grouped by
system, with identification, DTCs, live parameters, the identifier explorer
and a raw request console for the selected module."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from q50diag.core.types import Dtc, ModuleAddress, Reading
from q50diag.gui import theme
from q50diag.gui.context import AppContext
from q50diag.gui.pages.base import Page
from q50diag.gui.pages.module_explorer import ExplorerTab
from q50diag.gui.widgets.common import (
    Card,
    button,
    format_value,
    hbox,
    label,
    make_table,
    table_item,
)
from q50diag.session import ModuleReport

GROUP_ORDER = ("Powertrain", "Chassis", "Body", "Network", "Unknown")
PARAM_OWNER = "module"
STATE_TEXT = {True: ("responding", "ok"), False: ("no response", "fault"), None: ("not checked", "idle")}
TAB_IDENT, TAB_DTCS, TAB_PARAMS, TAB_EXPLORER, TAB_RAW = range(5)


def module_ids(module: ModuleAddress) -> str:
    return f"{module.tx_id:03X} / {module.rx_id:03X}"


def parse_hex_payload(text: str) -> bytes | None:
    cleaned = "".join(text.split()).upper().removeprefix("0X")
    if not cleaned or len(cleaned) % 2:
        return None
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return None


def grouped_modules(modules: list[ModuleAddress]) -> list[tuple[str, list[ModuleAddress]]]:
    groups: dict[str, list[ModuleAddress]] = {}
    for module in modules:
        group = module.group if module.group in GROUP_ORDER else "Unknown"
        groups.setdefault(group, []).append(module)
    return [(g, sorted(groups[g], key=lambda m: m.tx_id)) for g in GROUP_ORDER if g in groups]


class ModuleTree(QTreeWidget):
    """Modules grouped by system with responding state."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderLabels(["Module", "Req / Resp", "State"])
        self.setRootIsDecorated(True)
        self.setUniformRowHeights(True)
        self.setIndentation(14)
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)

    def populate(self, modules: list[ModuleAddress], responding: dict[str, bool]) -> None:
        selected = self.selected_module()
        self.blockSignals(True)
        self.clear()
        reselect = None
        for group, members in grouped_modules(modules):
            parent = QTreeWidgetItem([group.upper(), "", ""])
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            parent.setForeground(0, QColor(theme.TEXT_DIM))
            font = QFont(parent.font(0))
            font.setBold(True)
            font.setPointSizeF(8.5)
            parent.setFont(0, font)
            self.addTopLevelItem(parent)
            for module in members:
                item = self._module_item(module, responding.get(module.name))
                parent.addChild(item)
                if selected is not None and module.name == selected.name:
                    reselect = item
            parent.setExpanded(True)
        self.blockSignals(False)
        if reselect is not None:
            self.setCurrentItem(reselect)

    @staticmethod
    def _module_item(module: ModuleAddress, state: bool | None) -> QTreeWidgetItem:
        text, color_state = STATE_TEXT[state]
        item = QTreeWidgetItem([module.name, module_ids(module), text])
        item.setData(0, Qt.ItemDataRole.UserRole, module)
        item.setFont(1, theme.mono_font(9.5))
        item.setForeground(1, QColor(theme.TEXT_DIM))
        item.setForeground(2, QColor(theme.state_color(color_state)))
        item.setToolTip(0, module.notes or module.name)
        return item

    def selected_module(self) -> ModuleAddress | None:
        item = self.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def module_names(self) -> dict[str, str]:
        """Module name to displayed state text, for tests and summaries."""
        names = {}
        for index in range(self.topLevelItemCount()):
            group = self.topLevelItem(index)
            for child in range(group.childCount()):
                item = group.child(child)
                names[item.text(0)] = item.text(2)
        return names


class ModulesPage(Page):
    title = "Modules"
    subtitle = "Known and discovered control units. Select one to read identification, codes and data."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self.module: ModuleAddress | None = None
        self._param_values: dict[str, Reading] = {}
        self.discover_button = button("Discover modules", "primary", "Probe CAN ids 0x700 to 0x7FF")
        self.discover_button.clicked.connect(ctx.session.discover_modules)
        self.header.add_action(self.discover_button)
        self.tree = ModuleTree()
        self.tree.setMinimumWidth(300)
        self.tree.currentItemChanged.connect(self._on_tree_selection)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self._build_detail())
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 900])
        self.root.addWidget(splitter, 1)
        self._param_timer = QTimer(self)
        self._param_timer.setInterval(200)
        self._param_timer.timeout.connect(self._refresh_param_values)
        self._wire()
        self.refresh_tree()
        self._show_module(None)

    def _wire(self) -> None:
        self.ctx.module_state_changed.connect(self.refresh_tree)
        self.ctx.session.module_read.connect(self._on_module_read)
        self.ctx.session.dtcs_ready.connect(lambda _result: self._fill_dtcs())
        self.ctx.session.raw_response.connect(self._on_raw_response)
        self.ctx.session.connection_changed.connect(self._on_connection)
        self.ctx.vehicle_changed.connect(lambda _vehicle: self._show_module(None))

    # Layout --------------------------------------------------------------
    def _build_detail(self) -> QWidget:
        detail = QWidget()
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(12)
        self.info_card = Card()
        self.name_label = label("No module selected", "value")
        self.ids_label = label("", "mono")
        self.state_label = label("", "dim")
        self.notes_label = label("", "dim")
        self.notes_label.setWordWrap(True)
        self.read_button = button("Read module", None, "Read identification and trouble codes")
        self.read_button.clicked.connect(self.read_selected)
        self.info_card.body.addLayout(hbox(self.name_label, self.ids_label, None, self.state_label, self.read_button))
        self.info_card.body.addWidget(self.notes_label)
        layout.addWidget(self.info_card)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_ident_tab(), "Identification")
        self.tabs.addTab(self._build_dtc_tab(), "DTCs")
        self.tabs.addTab(self._build_params_tab(), "Parameters")
        self.explorer = ExplorerTab(self.ctx)
        self.tabs.addTab(self.explorer, "Explorer")
        self.tabs.addTab(self._build_raw_tab(), "Raw")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)
        return detail

    @staticmethod
    def _tab_container() -> tuple[QWidget, QVBoxLayout]:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        return widget, layout

    def _build_ident_tab(self) -> QWidget:
        widget, layout = self._tab_container()
        self.ident_table = make_table(["Field", "Value"], stretch_column=1)
        self.ident_table.setColumnWidth(0, 220)
        self.ident_errors = label("", "faint")
        self.ident_errors.setWordWrap(True)
        layout.addWidget(self.ident_table, 1)
        layout.addWidget(self.ident_errors)
        return widget

    def _build_dtc_tab(self) -> QWidget:
        widget, layout = self._tab_container()
        self.dtc_table = make_table(["Code", "Description", "Kind", "Status"], stretch_column=1)
        self.dtc_table.setColumnWidth(0, 90)
        self.dtc_source = label("", "faint")
        layout.addWidget(self.dtc_table, 1)
        layout.addWidget(self.dtc_source)
        return widget

    def _build_params_tab(self) -> QWidget:
        widget, layout = self._tab_container()
        self.live_button = button("Live", None, "Poll this module's parameters while this tab is open")
        self.live_button.setCheckable(True)
        self.live_button.toggled.connect(self._on_live_toggled)
        self.read_params_button = button("Read once", "ghost", "Read every parameter one time")
        self.read_params_button.clicked.connect(self._read_params_once)
        self.params_note = label("", "faint")
        layout.addLayout(hbox(self.live_button, self.read_params_button, None, self.params_note))
        self.param_table = make_table(["Parameter", "Value", "Unit", "Category", "Key"], stretch_column=0)
        for column, width in ((1, 140), (2, 70), (3, 110), (4, 240)):
            self.param_table.setColumnWidth(column, width)
        layout.addWidget(self.param_table, 1)
        return widget

    def _build_raw_tab(self) -> QWidget:
        widget, layout = self._tab_container()
        note = label(
            "Hex request sent to this module's request id. The read-only safety gate refuses "
            "writes, resets, security access, routines and DTC clears from here.",
            "dim",
        )
        note.setWordWrap(True)
        self.raw_edit = QLineEdit()
        self.raw_edit.setProperty("role", "mono")
        self.raw_edit.setPlaceholderText("e.g. 21 01  or  22 12 08")
        self.raw_edit.returnPressed.connect(self.send_raw)
        send = button("Send", "primary")
        send.clicked.connect(self.send_raw)
        self.raw_log = QPlainTextEdit()
        self.raw_log.setReadOnly(True)
        self.raw_log.setProperty("role", "mono")
        self.raw_log.setFont(theme.mono_font(9.5))
        self.raw_log.setMaximumBlockCount(500)
        layout.addWidget(note)
        layout.addLayout(hbox(self.raw_edit, send))
        layout.addWidget(self.raw_log, 1)
        return widget

    # Tree and selection --------------------------------------------------
    def refresh_tree(self) -> None:
        self.tree.populate(self.ctx.all_modules(), self.ctx.module_responding)
        if self.module is not None:
            self._show_state(self.module)

    def _on_tree_selection(self, current: QTreeWidgetItem | None, _previous) -> None:
        module = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        if module is None or module == self.module:
            return
        self._show_module(module)

    def select_module(self, name: str) -> bool:
        for index in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(index)
            for child in range(group.childCount()):
                item = group.child(child)
                if item.text(0) == name:
                    self.tree.setCurrentItem(item)
                    return True
        return False

    def _show_module(self, module: ModuleAddress | None) -> None:
        self._set_live(False)
        self.module = module
        self.explorer.set_module(module)
        self.read_button.setEnabled(module is not None)
        self.tabs.setEnabled(module is not None)
        self.raw_log.clear()
        self._param_values = {}
        if module is None:
            self.name_label.setText("No module selected")
            self.ids_label.setText("")
            self.state_label.setText("")
            self.notes_label.setText("Select a module in the list, or run discovery to find responders.")
            return
        self.name_label.setText(module.name)
        bus = "" if module.bus == "hs" else f"   {module.bus.upper()} bus"
        self.ids_label.setText(f"{module_ids(module)}   {module.protocol}   {module.group}{bus}")
        self.notes_label.setText(module.notes)
        self._show_state(module)
        self._fill_from_report(self.ctx.module_reports.get(module.name))
        self._fill_params_table()

    def _show_state(self, module: ModuleAddress) -> None:
        text, state = STATE_TEXT[self.ctx.module_responding.get(module.name)]
        self.state_label.setText(text.upper())
        theme.set_state(self.state_label, state)

    # Identification and DTCs ---------------------------------------------
    def read_selected(self) -> None:
        if self.module is None:
            return
        if not self.ctx.session.connected:
            self.ctx.notify("Connect to the adapter first", "warn")
            return
        self.ctx.session.read_module(self.module, include_parameters=False)

    def _on_module_read(self, report: ModuleReport) -> None:
        if self.module is not None and report.module.name == self.module.name:
            self._fill_from_report(report)
            for reading in report.parameters:
                self._param_values[reading.key] = reading
            self._refresh_param_values()

    def _fill_from_report(self, report: ModuleReport | None) -> None:
        self.ident_table.setRowCount(0)
        if report is None:
            self.ident_errors.setText("Not read yet. Press Read module.")
            self._fill_dtcs()
            return
        for field_name, value in report.identification.items():
            row = self.ident_table.rowCount()
            self.ident_table.insertRow(row)
            self.ident_table.setItem(row, 0, table_item(field_name))
            self.ident_table.setItem(row, 1, table_item(value, mono=True))
        errors = "; ".join(report.errors)
        self.ident_errors.setText(errors if errors else "")
        self._fill_dtcs(report)

    def _module_dtcs(self, report: ModuleReport | None) -> tuple[list[Dtc], str]:
        if report is not None and report.dtcs is not None:
            return report.dtcs, "From the last module read"
        if self.module is not None and self.ctx.dtc_result is not None:
            return self.ctx.dtcs_for(self.module.name), "From the last DTC scan"
        return [], "Not read yet"

    def _fill_dtcs(self, report: ModuleReport | None = None) -> None:
        if self.module is None:
            return
        report = report or self.ctx.module_reports.get(self.module.name)
        dtcs, source = self._module_dtcs(report)
        self.dtc_table.setRowCount(0)
        for dtc in dtcs:
            row = self.dtc_table.rowCount()
            self.dtc_table.insertRow(row)
            description = dtc.description or self.ctx.backend.describe(dtc.code) or "No description"
            color = theme.FAULT if dtc.kind in ("current", "stored", "permanent") else theme.WARN
            status = "" if dtc.status is None else f"0x{dtc.status:02X}"
            self.dtc_table.setItem(row, 0, table_item(dtc.code, mono=True, color=color))
            self.dtc_table.setItem(row, 1, table_item(description))
            self.dtc_table.setItem(row, 2, table_item(dtc.kind))
            self.dtc_table.setItem(row, 3, table_item(status, mono=True))
        self.dtc_source.setText(f"{source}: {len(dtcs)} codes")

    # Parameters ----------------------------------------------------------
    def _module_params(self):
        if self.module is None:
            return []
        try:
            return list(self.ctx.backend.module_parameters(self.module))
        except Exception as exc:  # noqa: BLE001; backend gaps are shown, not fatal
            self.ctx.notify(f"No parameter list for {self.module.name}: {exc}", "warn")
            return []

    def _fill_params_table(self) -> None:
        params = self._module_params()
        self.param_table.setRowCount(0)
        for param in params:
            row = self.param_table.rowCount()
            self.param_table.insertRow(row)
            name = table_item(param.name)
            name.setData(Qt.ItemDataRole.UserRole, param.key)
            name.setToolTip(param.description or param.key)
            self.param_table.setItem(row, 0, name)
            self.param_table.setItem(row, 1, table_item("--", mono=True, align_right=True))
            self.param_table.setItem(row, 2, table_item(param.unit, color=theme.TEXT_DIM))
            self.param_table.setItem(row, 3, table_item(param.category, color=theme.TEXT_DIM))
            self.param_table.setItem(row, 4, table_item(param.key, mono=True, color=theme.TEXT_FAINT))
        has_params = bool(params)
        self.live_button.setEnabled(has_params)
        self.read_params_button.setEnabled(has_params)
        self.params_note.setText(
            f"{len(params)} parameters" if has_params
            else "No known parameters for this module. Use the Explorer tab to find identifiers."
        )

    def _on_live_toggled(self, checked: bool) -> None:
        if checked and self.module is not None:
            subs = [(self.module, p) for p in self._module_params()]
            if not self.ctx.session.connected:
                self.ctx.notify("Connect to the adapter first", "warn")
                self._set_live(False)
                return
            self.ctx.session.start_polling(PARAM_OWNER, subs)
            self._param_timer.start()
        else:
            self.ctx.session.stop_polling(PARAM_OWNER)
            self._param_timer.stop()
        self.live_button.setText("Live (polling)" if checked else "Live")

    def _set_live(self, on: bool) -> None:
        if self.live_button.isChecked() != on:
            self.live_button.setChecked(on)
        elif not on:
            self.ctx.session.stop_polling(PARAM_OWNER)
            self._param_timer.stop()

    def _read_params_once(self) -> None:
        if self.module is not None and self.ctx.session.connected:
            self.ctx.session.read_module(self.module, include_parameters=True)
        elif not self.ctx.session.connected:
            self.ctx.notify("Connect to the adapter first", "warn")

    def _refresh_param_values(self) -> None:
        for row in range(self.param_table.rowCount()):
            key = self.param_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            reading = self._newest(key)
            if reading is None:
                continue
            cell = self.param_table.item(row, 1)
            if reading.error:
                cell.setText("error")
                cell.setForeground(QColor(theme.FAULT))
                cell.setToolTip(reading.error)
            else:
                cell.setText(format_value(reading.value))
                cell.setForeground(QColor(theme.TEXT))
                cell.setToolTip("")

    def _newest(self, key: str) -> Reading | None:
        candidates = [r for r in (self._param_values.get(key), self.ctx.store.latest(key)) if r]
        return max(candidates, key=lambda r: r.timestamp) if candidates else None

    def _on_tab_changed(self, index: int) -> None:
        if index != TAB_PARAMS:
            self._set_live(False)
        if index != TAB_EXPLORER:
            self.explorer.stop_watch()

    # Raw -----------------------------------------------------------------
    def send_raw(self) -> None:
        if self.module is None:
            return
        payload = parse_hex_payload(self.raw_edit.text())
        if payload is None:
            self.ctx.notify("Enter hex bytes, e.g. 21 01 or 2201 08", "warn")
            return
        if not self.ctx.session.connected:
            self.ctx.notify("Connect to the adapter first", "warn")
            return
        self.ctx.session.raw_request(self.module, payload)

    def _on_raw_response(self, request: str, response: str) -> None:
        if self.module is None or not request.startswith(f"{self.module.tx_id:03X} "):
            return
        self.raw_log.appendPlainText(f">  {request}\n<  {self.module.rx_id:03X} {response}\n")

    # Lifecycle -----------------------------------------------------------
    def _on_connection(self, connected: bool, _info) -> None:
        if not connected:
            self._set_live(False)

    def hideEvent(self, event) -> None:  # noqa: N802; Qt override
        self._set_live(False)
        self.explorer.stop_watch()
        super().hideEvent(event)
