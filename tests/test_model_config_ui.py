"""Read-only model rows mirror actual fake-client requests, without secret dumps."""

import io
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
import wave

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLineEdit

from ai_runtime import AIConfig, AIRuntime
from model_config_ui import ModelConfigPanel


class Result(BaseModel):
    text: str


class ModelConfigPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["config-panel-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def config(self, **extra):
        values = dict(speech_model="speech-custom", vote_model="vote-custom", selector_model="selector-custom",
                      transcription_model="audio-custom", reasoning_effort="medium", timeout_seconds=31,
                      max_retries=2, max_output_tokens=1234)
        values.update(extra)
        return AIConfig(**values)

    def test_allowlisted_actual_config_is_read_only_and_does_not_inspect_secrets(self):
        config = self.config()
        object.__setattr__(config, "api_key", "private-config-sentinel")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "private-env-sentinel"}), patch("ai_runtime.OpenAI", side_effect=AssertionError("No client")):
            panel = ModelConfigPanel(config)
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel.values["speech_model"], "speech-custom")
        self.assertEqual(panel.values["transcription_model"], "audio-custom")
        self.assertEqual(panel.values["reasoning_effort"], "medium")
        self.assertEqual(panel.values["max_output_tokens"], "1234")
        self.assertEqual(panel.values["timeout_seconds"], "31 seconds")
        self.assertEqual(panel.values["max_retries"], "2 retries (3 attempts)")
        text = " ".join(label.text() for label in panel.value_labels.values())
        self.assertNotIn("private-config", text)
        self.assertNotIn("private-env", text)
        self.assertEqual(panel.findChildren(QLineEdit), [])
        self.assertTrue(all(label.textFormat() == Qt.PlainText for label in panel.value_labels.values()))
        with self.assertRaises(TypeError):
            panel.values["api_key"] = "not permitted"

    def test_refresh_keeps_plain_model_text_and_updates_only_known_rows(self):
        panel = ModelConfigPanel(self.config())
        self.addCleanup(panel.deleteLater)
        row_count = panel.form.rowCount()
        panel.refresh(self.config(speech_model="<b>literal model label</b>", max_retries=0))
        self.assertEqual(panel.value_labels["speech_model"].text(), "<b>literal model label</b>")
        self.assertEqual(panel.value_labels["speech_model"].textFormat(), Qt.PlainText)
        self.assertEqual(panel.values["max_retries"], "0 retries (1 attempt)")
        self.assertEqual(panel.form.rowCount(), row_count)

    def test_displayed_policy_matches_dialogue_and_transcription_request_arguments(self):
        config = self.config()
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(status="completed", output=[], output_parsed={"text": "A reply"})
        client.audio.transcriptions.create.return_value = SimpleNamespace(text="A draft")
        runtime = AIRuntime(config=config, client=client)
        runtime._perform_request(config.speech_model, "<role />", "<context />", Result, on_response=lambda _response: None)
        request = client.responses.parse.call_args.kwargs
        self.assertFalse(request["store"])
        self.assertNotIn("stream", request)
        self.assertNotIn("service_tier", request)
        self.assertNotIn("temperature", request)
        self.assertNotIn("top_p", request)
        self.assertEqual(request["reasoning"], {"effort": config.reasoning_effort})
        self.assertEqual(request["max_output_tokens"], config.max_output_tokens)
        clip = io.BytesIO()
        with wave.open(clip, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\0\0" * 32)
        self.assertEqual(runtime.transcribe(clip.getvalue()), "A draft")
        audio = client.audio.transcriptions.create.call_args.kwargs
        self.assertNotIn("store", audio)
        self.assertNotIn("reasoning", audio)
        self.assertNotIn("max_output_tokens", audio)
        panel = ModelConfigPanel(config)
        self.addCleanup(panel.deleteLater)
        self.assertEqual(panel.values["dialogue_store"], "false")
        self.assertIn("Off", panel.values["dialogue_streaming"])
        self.assertIn("Not set", panel.values["dialogue_service_tier"])
        self.assertIn("dialogue-only", panel.values["transcription_endpoint"])


if __name__ == "__main__":
    unittest.main()
