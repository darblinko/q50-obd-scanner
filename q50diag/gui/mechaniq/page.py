"""MechanIQ page: one column per agent (lead first, then each helper), the
change log, and the same message input as the docked panel. The page is
constructed with the AppContext like every page; `build_mechaniq` attaches
the controller afterwards."""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QTabWidget, QVBoxLayout, QWidget

from q50diag.gui import theme
from q50diag.gui.context import AppContext
from q50diag.gui.mechaniq.model import LEAD, AgentInfo, MechaniqModel
from q50diag.gui.mechaniq.views import (
    AGENT_STATE_LABELS,
    ChangesView,
    ChatInput,
    KeyPrompt,
    TranscriptView,
    header_info,
)
from q50diag.gui.pages.base import Page
from q50diag.gui.widgets.common import button, hbox, label

COLUMN_WIDTH = 420
STATE_STYLES = {"running": "accent", "failed": "fault", "done": "idle", "idle": "idle"}


class AgentColumn(QFrame):
    """Title, state and transcript of one agent."""

    def __init__(self, model: MechaniqModel, agent: AgentInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.agent_id = agent.agent_id
        self.setMinimumWidth(COLUMN_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        self.title_label = label(agent.title.upper(), "section")
        self.state_label = label("")
        self.type_label = label(agent.subagent_type, "faint")
        layout.addLayout(hbox(self.title_label, self.type_label, None, self.state_label))
        speaker = "MechanIQ" if agent.agent_id == LEAD else agent.title
        self.transcript = TranscriptView(model, agent.agent_id, speaker)
        layout.addWidget(self.transcript, 1)
        self.update_agent(agent)

    def update_agent(self, agent: AgentInfo) -> None:
        self.state_label.setText(AGENT_STATE_LABELS.get(agent.state, agent.state))
        theme.set_state(self.state_label, STATE_STYLES.get(agent.state, "idle"))


class MechaniqPage(Page):
    title = "MechanIQ"
    subtitle = "Messages go to the lead agent, which hands work to helper agents. One column per agent."

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(ctx, parent)
        self.controller = None
        self.model: MechaniqModel | None = None
        self.columns: dict[str, AgentColumn] = {}
        self.info_label = label("", "dim")
        self.new_button = button("New conversation", "ghost")
        self.settings_button = button("Settings", "ghost", "Auth mode and model")
        for widget in (self.info_label, self.new_button, self.settings_button):
            self.header.add_action(widget)
        self.key_prompt = KeyPrompt()
        self.key_prompt.hide()
        self.root.addWidget(self.key_prompt)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self.placeholder = label("MechanIQ is not available in this window.", "dim")
        self.tabs.addTab(self.placeholder, "Agents")
        self.input = ChatInput()
        self.send_button = button("Send", "primary")
        self.stop_button = button("Stop", "danger", "Interrupt the current turn")
        self.stop_button.setEnabled(False)
        self.root.addLayout(hbox(self.input, self._button_column()))

    def _button_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addWidget(self.send_button)
        column.addWidget(self.stop_button)
        column.addStretch(1)
        return column

    def attach(self, controller) -> None:
        """Bind to the shared MechanIQ controller (same session as the panel)."""
        self.controller = controller
        self.model = controller.model
        self.tabs.clear()
        self.placeholder.hide()
        self.placeholder.deleteLater()
        self.tabs.addTab(self._build_board(), "Agents")
        self.changes = ChangesView(self.model)
        self.changes.undo_requested.connect(controller.undo)
        self.tabs.addTab(self.changes, "Changes")
        self.input.submitted.connect(controller.send)
        self.send_button.clicked.connect(self.input.submit)
        self.stop_button.clicked.connect(controller.interrupt)
        self.new_button.clicked.connect(controller.reset)
        self.settings_button.clicked.connect(lambda: controller.open_settings(self.window()))
        self.key_prompt.add_key_requested.connect(lambda: controller.open_settings(self.window()))
        self.model.agents_changed.connect(self._sync_columns)
        self.model.info_changed.connect(self._update_info)
        self.model.busy_changed.connect(self.stop_button.setEnabled)
        controller.needs_key_changed.connect(self._on_needs_key)
        self._sync_columns()
        self._update_info()
        self.stop_button.setEnabled(self.model.busy)
        self._on_needs_key(controller.needs_api_key)

    def _build_board(self) -> QScrollArea:
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        board = QWidget()
        self.board_layout = QHBoxLayout(board)
        self.board_layout.setContentsMargins(0, 8, 0, 0)
        self.board_layout.setSpacing(12)
        self.scroll.setWidget(board)
        return self.scroll

    def _sync_columns(self) -> None:
        agents = {agent.agent_id: agent for agent in self.model.agents()}
        for agent_id in [a for a in self.columns if a not in agents]:
            column = self.columns.pop(agent_id)
            self.board_layout.removeWidget(column)
            column.hide()
            column.deleteLater()
        for agent_id, agent in agents.items():
            column = self.columns.get(agent_id)
            if column is None:
                column = AgentColumn(self.model, agent)
                self.columns = {**self.columns, agent_id: column}
                self.board_layout.addWidget(column, 1)
            column.update_agent(agent)

    def _on_needs_key(self, needs_key: bool) -> None:
        self.key_prompt.setVisible(needs_key)
        self.input.setEnabled(not needs_key)
        self.send_button.setEnabled(not needs_key)

    def _update_info(self) -> None:
        self.info_label.setText(header_info(self.model))

    def on_shown(self) -> None:
        self.input.setFocus()
