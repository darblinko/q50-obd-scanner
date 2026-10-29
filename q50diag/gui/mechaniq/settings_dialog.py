"""MechanIQ settings dialog: auth mode, API key and model.

Works on any frozen settings dataclass with `auth_mode`, `api_key` and
`model` fields (`q50diag.mechaniq.settings.MechaniqSettings`). A packaged
build is API key only: the subscription option and its note are hidden and a
short explanation is shown instead."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from q50diag.gui import theme
from q50diag.gui.widgets.common import button, hbox, label
from q50diag.mechaniq.settings import (
    API_KEY,
    API_KEY_PAGE,
    SUBSCRIPTION,
    api_key_warning,
    clean_api_key,
    is_packaged,
)

AUTH_NOTE = (
    "Subscription uses the Claude login in ~/.claude and removes any ANTHROPIC_API_KEY from the "
    "agent's environment. API key mode bills the key below. There is no automatic fallback."
)
PACKAGED_NOTE = (
    "MechanIQ is an assistant that runs on Claude. It needs an API key from an Anthropic account, "
    "and its usage is billed to that account. The key is saved on this computer in your user folder."
)
KEY_HELP = (
    f'To get a key, sign in at <a href="{API_KEY_PAGE}" style="color: {theme.ACCENT};">'
    "platform.claude.com/settings/keys</a>, "
    "click Create key and copy the key. It starts with sk-ant- and is shown only once."
)
MISSING_KEY_SUBSCRIPTION = "Enter an API key or choose Subscription."


class MechaniqSettingsDialog(QDialog):
    def __init__(
        self, settings, models: Sequence[str], parent: QWidget | None = None, packaged: bool | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("MechanIQ settings")
        self.setMinimumWidth(460)
        self._settings = settings
        self.packaged = is_packaged() if packaged is None else packaged
        self.subscription_radio = QRadioButton("Subscription (Claude login)")
        self.api_radio = QRadioButton("Anthropic API key")
        group = QButtonGroup(self)
        group.addButton(self.subscription_radio)
        group.addButton(self.api_radio)
        self._build_key_field(settings.api_key)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(list(models))
        self.model_combo.setCurrentText(settings.model)
        self.warning_label = label("", state="warn")
        self.warning_label.setWordWrap(True)
        self.error_label = label("", state="fault")
        self._build_layout()
        api_mode = self.packaged or settings.auth_mode == API_KEY
        (self.api_radio if api_mode else self.subscription_radio).setChecked(True)
        self.api_radio.toggled.connect(self._sync)
        self.key_edit.textChanged.connect(self._sync)
        self._sync()

    def _build_key_field(self, api_key: str) -> None:
        self.key_edit = QLineEdit(api_key)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-ant-...")
        self.show_key_button = button("Show", "ghost", "Show or hide the key")
        self.show_key_button.setCheckable(True)
        self.show_key_button.toggled.connect(self._set_key_visible)

    def _build_layout(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Auth", self.subscription_radio)
        form.addRow("", self.api_radio)
        form.addRow("API key", hbox(self.key_edit, self.show_key_button, spacing=4))
        form.addRow("Model", self.model_combo)
        if self.packaged:
            form.setRowVisible(self.subscription_radio, False)
            form.setRowVisible(self.api_radio, False)
        layout.addLayout(form)
        self.note_label = label(PACKAGED_NOTE if self.packaged else AUTH_NOTE, "dim")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)
        self.key_help_label = label(KEY_HELP, "dim")
        self.key_help_label.setTextFormat(Qt.TextFormat.RichText)
        self.key_help_label.setOpenExternalLinks(True)
        self.key_help_label.setWordWrap(True)
        layout.addWidget(self.key_help_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.error_label)
        self.save_button = button("Save", "primary")
        self.save_button.clicked.connect(self.accept)
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        layout.addLayout(hbox(None, cancel, self.save_button))

    def _set_key_visible(self, visible: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_key_button.setText("Hide" if visible else "Show")

    def _api_mode(self) -> bool:
        return self.packaged or self.api_radio.isChecked()

    def _key_error(self) -> str:
        """Why the key cannot be saved, or "" when it can."""
        if not self._api_mode():
            return ""
        try:
            clean_api_key(self.key_edit.text())
        except ValueError as exc:
            return str(exc) if self.packaged else MISSING_KEY_SUBSCRIPTION
        return ""

    def _sync(self) -> None:
        api_mode = self._api_mode()
        self.key_edit.setEnabled(api_mode)
        self.show_key_button.setEnabled(api_mode)
        error = self._key_error()
        self.error_label.setText(error)
        self.warning_label.setText(api_key_warning(self.key_edit.text()) if api_mode and not error else "")
        self.save_button.setEnabled(not error)

    def accept(self) -> None:
        if self._key_error():
            self._sync()
            return
        super().accept()

    def result_settings(self):
        """The edited settings as a new object; the input settings are unchanged."""
        return replace(
            self._settings,
            auth_mode=API_KEY if self._api_mode() else SUBSCRIPTION,
            api_key=self.key_edit.text().strip(),
            model=self.model_combo.currentText().strip() or self._settings.model,
        )
