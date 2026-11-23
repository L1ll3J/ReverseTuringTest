"""Read-only, allowlisted model configuration with no client or environment access."""

from types import MappingProxyType

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFormLayout, QLabel, QScrollArea, QVBoxLayout, QWidget


def model_config_values(config):
    """Mirror current AIRuntime request policy, with flags scoped to their endpoint."""
    return MappingProxyType({
        "speech_model": str(config.speech_model),
        "vote_model": str(config.vote_model),
        "selector_model": str(config.selector_model),
        "transcription_model": str(config.transcription_model),
        "reasoning_effort": str(config.reasoning_effort),
        "max_output_tokens": str(config.max_output_tokens),
        "timeout_seconds": f"{config.timeout_seconds:g} seconds",
        "max_retries": (f"{config.max_retries} {'retry' if config.max_retries == 1 else 'retries'} "
                        f"({config.max_retries + 1} {'attempt' if config.max_retries == 0 else 'attempts'})"),
        "dialogue_store": "false",
        "dialogue_streaming": "Off (non-streamed structured Responses)",
        "dialogue_service_tier": "Not set; provider/account default",
        "dialogue_temperature": "Not set; provider/model default",
        "dialogue_top_p": "Not set; provider/model default",
        "transcription_endpoint": "Audio transcription; reasoning/token/store flags above are dialogue-only",
    })


class ModelConfigPanel(QWidget):
    def __init__(self, config, *, body_font=None, heading_font=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        heading = QLabel("Model configuration")
        heading.setTextFormat(Qt.PlainText)
        self._font(heading, heading_font)
        layout.addWidget(heading)
        self.form = QFormLayout()
        content = QWidget()
        content.setLayout(self.form)
        self.value_labels = {}
        self.values = MappingProxyType({})
        labels = {
            "speech_model": "NPC speech", "vote_model": "NPC votes", "selector_model": "Reply selection",
            "transcription_model": "Voice transcription", "reasoning_effort": "Dialogue reasoning effort",
            "max_output_tokens": "Dialogue output token limit", "timeout_seconds": "Request timeout",
            "max_retries": "SDK retry limit", "dialogue_store": "Dialogue response store flag",
            "dialogue_streaming": "Dialogue streaming", "dialogue_service_tier": "Dialogue service tier",
            "dialogue_temperature": "Dialogue temperature", "dialogue_top_p": "Dialogue top-p",
            "transcription_endpoint": "Transcription request scope",
        }
        for key, title in labels.items():
            label = QLabel(title)
            label.setTextFormat(Qt.PlainText)
            self._font(label, body_font)
            value = QLabel()
            value.setTextFormat(Qt.PlainText)
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            value.setAccessibleName(title)
            self._font(value, body_font)
            self.form.addRow(label, value)
            self.value_labels[key] = value
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(content)
        layout.addWidget(self.scroll, 1)
        self.refresh(config)

    @staticmethod
    def _font(widget, font):
        if font is not None:
            widget.setFont(font)
            family = font.family().replace("\\", "\\\\").replace('"', '\\"')
            size = f"{font.pixelSize()}px" if font.pixelSize() > 0 else f"{font.pointSizeF():g}pt"
            widget.setStyleSheet(f'font-family: "{family}"; font-size: {size};')

    def refresh(self, config):
        values = model_config_values(config)
        for key, value in values.items():
            self.value_labels[key].setText(value)
        self.values = values
