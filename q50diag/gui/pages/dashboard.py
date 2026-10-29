"""Dashboard page: an instrument panel of analog gauges arranged by the active
named layout. Click a gauge to choose its sensor; the settings dialog picks the
layout preset, range overrides and named layouts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPoint, Qt, QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import QComboBox, QDialog, QGridLayout, QMenu, QWidget

from q50diag.gui.context import AppContext
from q50diag.gui.dashboard_layouts import (
    PRESETS,
    Preferences,
    SlotAssignment,
    active_layout,
    visible_slots,
    with_active,
    with_favorite,
    with_layout,
    with_slot,
    with_vehicle_choice,
    without_favorite,
)
from q50diag.gui.dialogs.dashboard_settings import DashboardSettingsDialog
from q50diag.gui.dialogs.sensor_picker import SensorPickerDialog
from q50diag.gui.favorites import adopt_vehicle_layout, load_favorites, save_favorites
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.analog_gauge import AnalogGauge
from q50diag.gui.widgets.common import button

OWNER = "dashboard"
REFRESH_MS = 200
ANIMATION_MS = 33  # about 30 Hz needle smoothing
MAX_FRAME_S = 0.1
GRID_SPAN = 4  # largest row or column count of any preset


class DashboardPage(Page):
    title = "Dashboard"
    subtitle = "Analog gauges for the sensors in the active layout. Click a gauge to change its sensor."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None, favorites_path: Path | None = None) -> None:
        super().__init__(ctx, parent)
        self._favorites_path = favorites_path
        self._prefs = load_favorites(favorites_path)
        self._on_disk = self._prefs
        self._subscribed: list[str] = []
        self._save_warned = False
        self._pool: list[AnalogGauge] = []
        self._pool_configs: list[tuple | None] = []
        self.gauges: list[AnalogGauge] = []
        self._shown_preset = ""
        self._adopted_vehicle = ""
        self._build_header()
        self.grid = QGridLayout()
        self.grid.setSpacing(14)
        self.root.addLayout(self.grid, 1)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(REFRESH_MS)
        self._refresh_timer.timeout.connect(self._refresh)
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(ANIMATION_MS)
        self.animation_timer.timeout.connect(self._animate)
        self._frame_clock = QElapsedTimer()
        ctx.catalog_changed.connect(self._on_catalog_changed)
        ctx.session.activity_changed.connect(self._sync_poll_button)
        ctx.session.connection_changed.connect(self._sync_poll_button)
        self._apply(self._prefs)
        self._adopt_vehicle_layout()
        self._refresh_timer.start()

    def _build_header(self) -> None:
        self.layout_combo = QComboBox()
        self.layout_combo.setMinimumWidth(180)
        self.layout_combo.setToolTip("Saved layout")
        self.layout_combo.currentIndexChanged.connect(self._on_layout_combo)
        self.settings_button = button("Instrument panel settings", tooltip="Layout preset, slot ranges and saved layouts")
        self.settings_button.clicked.connect(self.open_settings)
        self.poll_button = button("Start polling", "primary")
        self.poll_button.setCheckable(True)
        self.poll_button.toggled.connect(self._on_poll_toggled)
        for widget in (self.layout_combo, self.settings_button, self.poll_button):
            self.header.add_action(widget)

    # Preferences --------------------------------------------------------
    def preferences(self) -> Preferences:
        return self._prefs

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802; Qt override
        super().showEvent(event)
        self.reload_preferences()
        self._resubscribe()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802; Qt override
        super().hideEvent(event)
        self.animation_timer.stop()
        # Leaving the page pauses its poll so the next page gets the adapter's full
        # request rate. Minimizing (a spontaneous hide) and recording keep it running.
        if not event.spontaneous() and not self.ctx.recorder.is_recording:
            self._pause_polling()

    def _pause_polling(self) -> None:
        if self._subscribed:
            self._subscribed = []
            self.ctx.session.stop_polling(OWNER)

    def reload_preferences(self) -> None:
        """Reread the file (the Live Data page edits favorites) and apply it if it
        changed since this page last read or wrote it."""
        prefs = load_favorites(self._favorites_path)
        if prefs != self._on_disk:
            self._on_disk = prefs
            self._apply(prefs)

    def _commit(self, prefs: Preferences) -> None:
        self._apply(prefs)
        try:
            saved = save_favorites(prefs, self._favorites_path)
        except OSError as exc:
            self.ctx.notify(f"Dashboard: could not save layouts: {exc}", "error")
            return
        if saved is not None:
            self._on_disk = prefs
        if saved is None and not self._save_warned:
            self._save_warned = True
            self.ctx.notify("Dashboard: favorites file is unreadable or from a newer version; "
                            "changes are kept for this session only", "warn")

    def _apply(self, prefs: Preferences) -> None:
        self._prefs = prefs
        self._fill_layout_combo()
        layout = active_layout(prefs)
        if layout.preset != self._shown_preset:
            self._arrange(layout.preset)
        self._configure_gauges()
        self._resubscribe()

    def _fill_layout_combo(self) -> None:
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        for layout in self._prefs.layouts:
            self.layout_combo.addItem(layout.name, layout.id)
        self.layout_combo.setCurrentIndex(max(self.layout_combo.findData(active_layout(self._prefs).id), 0))
        self.layout_combo.blockSignals(False)

    def _on_layout_combo(self, index: int) -> None:
        layout_id = self.layout_combo.itemData(index)
        if layout_id and layout_id != active_layout(self._prefs).id:
            self._commit_choice(with_active(self._prefs, layout_id))

    def select_layout(self, layout_id: str) -> None:
        self.layout_combo.setCurrentIndex(self.layout_combo.findData(layout_id))

    # Grid ---------------------------------------------------------------
    def _gauge(self, index: int) -> AnalogGauge:
        while len(self._pool) <= index:
            slot = len(self._pool)
            gauge = AnalogGauge(self)
            gauge.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            gauge.clicked.connect(lambda i=slot: self.choose_sensor(i))
            gauge.customContextMenuRequested.connect(lambda pos, i=slot: self._show_menu(i, pos))
            gauge.hide()
            self._pool.append(gauge)
            self._pool_configs.append(None)
        return self._pool[index]

    def _arrange(self, preset_name: str) -> None:
        preset = PRESETS[preset_name]
        for gauge in self.gauges:
            self.grid.removeWidget(gauge)
            gauge.hide()
        for line in range(GRID_SPAN):
            self.grid.setRowStretch(line, 1 if line < preset.rows else 0)
            self.grid.setColumnStretch(line, 1 if line < preset.columns else 0)
        self.gauges = [self._gauge(index) for index in range(preset.slot_count)]
        for gauge, (row, column, row_span, column_span) in zip(self.gauges, preset.cells):
            self.grid.addWidget(gauge, row, column, row_span, column_span)
            gauge.show()
        self._shown_preset = preset_name

    def slots(self) -> tuple[SlotAssignment | None, ...]:
        return visible_slots(active_layout(self._prefs))

    def _gauge_config(self, slot: SlotAssignment | None) -> tuple:
        entry = self.ctx.entry(slot.key) if slot else None
        if slot is None:
            return ("empty",)
        if entry is None:
            return ("unavailable", slot.key)
        low, high = slot.range or (entry.param.min_value, entry.param.max_value)
        return ("parameter", slot.key, entry.param.name, entry.param.unit, low, high)

    def _configure_gauges(self) -> None:
        """Reconfigure only gauges whose sensor or range changed, so needles
        and readings of unchanged gauges are kept."""
        for index, (gauge, slot) in enumerate(zip(self.gauges, self.slots())):
            config = self._gauge_config(slot)
            if config == self._pool_configs[index]:
                continue
            self._pool_configs[index] = config
            if config[0] == "empty":
                gauge.set_empty()
            elif config[0] == "unavailable":
                gauge.set_unavailable(slot.key)
            else:
                gauge.set_parameter(*config[2:])
                self._show(index)

    def _on_catalog_changed(self) -> None:
        self._configure_gauges()
        self._resubscribe()
        self._adopt_vehicle_layout()

    def _adopt_vehicle_layout(self) -> None:
        """Pick the layout for the selected vehicle once per vehicle change, so
        catalog rebuilds for the same vehicle never override the user."""
        vehicle = self.ctx.vehicle
        if not self.ctx.catalog or vehicle.key == self._adopted_vehicle:
            return
        self._adopted_vehicle = vehicle.key
        prefs = adopt_vehicle_layout(self._prefs, vehicle.key, vehicle.name, vehicle.default_favorites,
                                     lambda key: self.ctx.entry(key) is not None)
        if prefs != self._prefs:
            self._commit(prefs)

    def _commit_choice(self, prefs: Preferences) -> None:
        """Commit a layout the user activated and remember it for the vehicle."""
        self._commit(with_vehicle_choice(prefs, self.ctx.vehicle.key, active_layout(prefs).id))

    # Sensor assignment --------------------------------------------------
    def assign_slot(self, index: int, key: str) -> None:
        """Show `key` in gauge `index` of the active layout; "" empties it."""
        slot = self.slots()[index]
        if (slot.key if slot else "") == key:
            return
        layout = with_slot(active_layout(self._prefs), index, SlotAssignment(key) if key else None)
        self._commit(with_layout(self._prefs, layout))

    def set_favorite(self, key: str, favorite: bool) -> None:
        prefs = with_favorite(self._prefs, key) if favorite else without_favorite(self._prefs, key)
        if prefs != self._prefs:
            self._commit(prefs)

    def _favorites_changed(self, favorites: tuple[str, ...]) -> None:
        if favorites != self._prefs.favorites:
            self._commit(replace(self._prefs, favorites=favorites))

    def choose_sensor(self, index: int) -> None:
        slot = self.slots()[index]
        picker = SensorPickerDialog(self.ctx.catalog, self._prefs.favorites, slot.key if slot else "", self)
        accepted = picker.exec() == QDialog.DialogCode.Accepted
        self._favorites_changed(picker.favorites())
        if accepted:
            self.assign_slot(index, picker.selected_key())

    def _show_menu(self, index: int, position: QPoint) -> None:
        slot = self.slots()[index]
        menu = QMenu(self)
        menu.addAction("Change sensor...", lambda: self.choose_sensor(index))
        if slot is not None:
            menu.addAction("Clear", lambda: self.assign_slot(index, ""))
            favorite = slot.key in self._prefs.favorites
            menu.addAction("Remove from favorites" if favorite else "Add to favorites",
                           lambda: self.set_favorite(slot.key, not favorite))
        menu.addSeparator()
        menu.addAction("Instrument panel settings...", self.open_settings)
        menu.exec(self.gauges[index].mapToGlobal(position))

    def open_settings(self) -> None:
        dialog = DashboardSettingsDialog(self._prefs, self.ctx.catalog, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._commit_choice(dialog.result_preferences())

    # Polling ------------------------------------------------------------
    def assigned_keys(self) -> list[str]:
        """Unique keys shown by the visible gauges that exist in the catalog, in slot order."""
        keys = (slot.key for slot in self.slots() if slot is not None)
        return [key for key in dict.fromkeys(keys) if self.ctx.entry(key) is not None]

    def _resubscribe(self) -> None:
        keys = self.assigned_keys()
        if not self.poll_button.isChecked() or keys == self._subscribed:
            return
        self._subscribed = keys
        if keys:
            self.ctx.session.start_polling(OWNER, [self.ctx.entry(key).subscription() for key in keys])
        else:
            self.ctx.session.stop_polling(OWNER)

    def _on_poll_toggled(self, running: bool) -> None:
        self.poll_button.setText("Stop polling" if running else "Start polling")
        self._subscribed = []
        if not running:
            self.ctx.session.stop_polling(OWNER)
            return
        if not self.ctx.session.connected:
            self.ctx.notify("Dashboard: connect to an adapter or the simulator first", "warn")
            self.poll_button.setChecked(False)
            return
        if not self.assigned_keys():
            self.ctx.notify("Dashboard: choose a sensor for at least one gauge to poll", "warn")
            self.poll_button.setChecked(False)
            return
        self._resubscribe()

    def start_polling(self) -> None:
        self.poll_button.setChecked(True)

    def stop_polling(self) -> None:
        self.poll_button.setChecked(False)

    def _sync_poll_button(self, *_args) -> None:
        running = OWNER in self.ctx.session.polling_owners()
        if self.poll_button.isChecked() and not running and not self.ctx.session.connected:
            self.poll_button.blockSignals(True)
            self.poll_button.setChecked(False)
            self.poll_button.setText("Start polling")
            self.poll_button.blockSignals(False)
            self._subscribed = []

    # Values -------------------------------------------------------------
    def _refresh(self) -> None:
        if self.isVisible():
            self.refresh_values()

    def refresh_values(self) -> None:
        for index, slot in enumerate(self.slots()):
            if slot is not None and self.ctx.entry(slot.key) is not None:
                self._show(index)
        if self.isVisible() and not self.animation_timer.isActive():
            if not all(gauge.is_settled() for gauge in self.gauges):
                self._frame_clock.start()
                self.animation_timer.start()

    def _show(self, index: int) -> None:
        key = self.slots()[index].key
        latest = self.ctx.store.latest(key)
        if latest is None:
            return
        stats = self.ctx.store.stats(key)
        minimum = stats.minimum if stats else None
        maximum = stats.maximum if stats else None
        if latest.error and (stats is None or stats.count == 0):
            self.gauges[index].set_reading(None, minimum, maximum, error=latest.error)
        else:
            self.gauges[index].set_reading(latest.value, minimum, maximum)

    def _animate(self) -> None:
        dt = min(self._frame_clock.restart() / 1000.0, MAX_FRAME_S)
        moving = [gauge.step(dt) for gauge in self.gauges]
        if not any(moving) or not self.isVisible():
            self.animation_timer.stop()
