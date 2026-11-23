"""Real Qt lifecycle tests with fake audio input; never opens a microphone."""

import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import wave

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtMultimedia import QAudio, QAudioFormat
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from dictation import DictationError, DictationRecorder


class FakeSource(QObject):
    readyRead = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.pending = bytearray()
        self.read_sizes = []

    def feed(self, data):
        self.pending.extend(data)
        self.readyRead.emit()

    def bytesAvailable(self):
        return len(self.pending)

    def read(self, size):
        self.read_sizes.append(size)
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data


class FakeAudio(QObject):
    stateChanged = pyqtSignal(int)

    def __init__(self, device, audio_format, parent):
        super().__init__(parent)
        self.source = FakeSource()
        self.audio_format = audio_format
        self.audio_error = QAudio.NoError
        self.stops = 0

    def start(self):
        return self.source

    def stop(self):
        self.stops += 1
        self.stateChanged.emit(QAudio.StoppedState)

    def bytesReady(self):
        return len(self.source.pending)

    def error(self):
        return self.audio_error


class DictationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["dictation-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.device = Mock()
        self.device.isNull.return_value = False
        self.device.isFormatSupported.return_value = True
        self.audio = []
        self.provider = Mock(return_value=self.device)
        self.recorders = []
        # Fail loudly if an implementation accidentally bypasses the fakes.
        self.hardware_patch = patch("dictation.QAudioInput", side_effect=AssertionError("Real microphone access"))
        self.hardware_patch.start()
        self.addCleanup(self.hardware_patch.stop)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for recorder in self.recorders:
            recorder.close()

    def recorder(self, **kwargs):
        def factory(*args):
            audio = FakeAudio(*args)
            self.audio.append(audio)
            return audio
        recorder = DictationRecorder(device_provider=self.provider, audio_factory=factory, **kwargs)
        self.recorders.append(recorder)
        recorder.ready.connect(lambda generation, data: self.ready.append((generation, data)))
        recorder.failed.connect(lambda generation, message: self.failed.append((generation, message)))
        self.ready, self.failed = [], []
        return recorder

    def test_lazy_start_stop_produces_exact_pcm_wav_and_releases_resources(self):
        recorder = self.recorder()
        self.provider.assert_not_called()
        generation = recorder.start()
        self.assertTrue(recorder.is_recording)
        audio = self.audio[-1]
        pcm = b"\x01\x00\x02\x00" * 80
        audio.source.feed(pcm)
        recorder.stop()
        self.assertFalse(recorder.is_recording)
        self.assertEqual(audio.stops, 1)
        self.assertEqual(self.failed, [])
        token, data = self.ready.pop()
        self.assertEqual(token, generation)
        self.assertTrue(recorder.is_current(token))
        with wave.open(io.BytesIO(data), "rb") as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 16000))
            self.assertEqual(wav.readframes(wav.getnframes()), pcm)
        self.assertIsNone(recorder._buffer)
        self.assertIsNone(recorder._audio)
        recorder.stop()
        self.assertEqual(audio.stops, 1)

    def test_device_selection_enumerates_without_opening_and_resolves_at_start(self):
        self.device.deviceName.return_value = "USB microphone"
        with patch("dictation.QAudioDeviceInfo.availableDevices", return_value=[self.device, self.device]):
            recorder = self.recorder()
            self.assertEqual(recorder.input_devices(), [("USB microphone", "USB microphone")])
            self.assertEqual(recorder.device_name, "")
            recorder.set_device_name("USB microphone")
            self.assertEqual(recorder.device_name, "USB microphone")
            self.provider.assert_not_called()
            recorder.start()
            self.provider.assert_not_called()
            with self.assertRaises(DictationError):
                recorder.set_device_name("")
            recorder.cancel()
            recorder.set_device_name("")
            recorder.start()
            self.provider.assert_called_once()

    def test_unavailable_selection_never_silently_uses_another_device(self):
        recorder = self.recorder(device_name="unplugged microphone")
        with patch("dictation.QAudioDeviceInfo.availableDevices", return_value=[]):
            with self.assertRaises(DictationError):
                recorder.set_device_name("unknown microphone")
            recorder.start()
        self.assertEqual(len(self.failed), 1)
        self.assertEqual(self.audio, [])
        self.provider.assert_not_called()
        self.assertEqual(recorder.device_name, "unplugged microphone")

    def test_backend_pending_bytes_are_drained_when_pull_source_reports_zero(self):
        recorder = self.recorder(max_audio_bytes=52)
        diagnostics = []
        recorder.diagnostic.connect(diagnostics.append)
        recorder.start()
        source = self.audio[-1].source
        source.bytesAvailable = Mock(return_value=0)
        source.isOpen = Mock(return_value=True)
        source.isReadable = Mock(return_value=True)
        source.feed(b"\x01\x00" * 20)
        self.assertEqual(source.read_sizes, [8])
        self.assertEqual(len(source.pending), 32)
        self.assertFalse(recorder.is_recording)
        self.assertEqual(self.failed, [])
        with wave.open(io.BytesIO(self.ready[0][1]), "rb") as wav:
            self.assertEqual(wav.readframes(4), b"\x01\x00" * 4)
        stopped = diagnostics[-1]
        self.assertEqual(stopped["source_bytes_available"], 0)
        self.assertEqual(stopped["backend_bytes_ready"], 32)
        self.assertEqual(stopped["ready_read_events"], 1)
        self.assertTrue(stopped["source_open"])
        self.assertTrue(stopped["source_readable"])

    def test_stop_flushes_backend_pcm_without_a_ready_read_signal(self):
        recorder = self.recorder()
        recorder.start()
        source = self.audio[-1].source
        source.bytesAvailable = Mock(return_value=0)
        pcm = b"\x01\x00" * 8
        source.pending.extend(pcm)
        recorder.stop()
        self.assertEqual(self.failed, [])
        with wave.open(io.BytesIO(self.ready[0][1]), "rb") as wav:
            self.assertEqual(wav.readframes(8), pcm)

    def test_missing_and_incompatible_device_fail_without_constructing_audio(self):
        for missing in (True, False):
            self.device.isNull.return_value = missing
            self.device.isFormatSupported.return_value = False
            recorder = self.recorder()
            generation = recorder.start()
            self.assertEqual(self.failed[0][0], generation)
            self.assertFalse(recorder.is_recording)
            self.assertEqual(self.audio, [])
            self.assertEqual(self.ready, [])

    def test_format_fallback_uses_supported_signed_little_endian_pcm(self):
        self.device.isFormatSupported.side_effect = lambda fmt: fmt.channelCount() == 2 and fmt.sampleRate() == 48000
        recorder = self.recorder()
        recorder.start()
        fmt = self.audio[-1].audio_format
        self.assertEqual((fmt.channelCount(), fmt.sampleRate(), fmt.sampleSize()), (2, 48000, 16))
        self.assertEqual(fmt.sampleType(), QAudioFormat.SignedInt)
        self.assertEqual(fmt.byteOrder(), QAudioFormat.LittleEndian)

    def test_size_limit_prevents_overread_and_auto_stops(self):
        recorder = self.recorder(max_audio_bytes=52)
        recorder.start()
        audio = self.audio[-1]
        audio.source.feed(b"\x00" * 1000)
        self.assertEqual(audio.source.read_sizes, [8])
        self.assertFalse(recorder.is_recording)
        self.assertEqual(len(self.ready[0][1]), 52)
        self.assertEqual(len(audio.source.pending), 992)

    def test_duration_limit_is_enforced_by_real_qt_timer(self):
        recorder = self.recorder(max_duration_seconds=0.015)
        recorder.start()
        self.audio[-1].source.feed(b"\x00\x00" * 10)
        QTest.qWait(50)
        self.assertFalse(recorder.is_recording)
        self.assertEqual(len(self.ready), 1)

    def test_sample_count_also_bounds_duration_when_input_arrives_quickly(self):
        recorder = self.recorder(max_duration_seconds=0.01)
        recorder.start()
        self.audio[-1].source.feed(b"\x00" * 10000)
        with wave.open(io.BytesIO(self.ready[0][1]), "rb") as wav:
            self.assertEqual(wav.getnframes(), 160)

    def test_cancel_invalidates_recording_and_completed_clip_and_stale_callbacks(self):
        recorder = self.recorder()
        first = recorder.start()
        old_audio = self.audio[-1]
        recorder.cancel()
        second = recorder.start()
        old_audio.source.feed(b"\x00\x00")
        old_audio.stateChanged.emit(QAudio.StoppedState)
        recorder._stop_generation(first)
        self.assertTrue(recorder.is_recording)
        self.assertFalse(recorder.is_current(first))
        self.audio[-1].source.feed(b"\x00\x00")
        recorder.stop()
        self.assertEqual([entry[0] for entry in self.ready], [second])
        recorder.cancel()
        self.assertFalse(recorder.is_current(second))
        self.assertEqual(self.failed, [])

    def test_empty_audio_and_device_failure_never_produce_ready(self):
        recorder = self.recorder()
        recorder.start()
        recorder.stop()
        self.assertIn("No audio", self.failed[-1][1])
        recorder.start()
        self.audio[-1].audio_error = QAudio.IOError
        self.audio[-1].stateChanged.emit(QAudio.StoppedState)
        self.assertFalse(recorder.is_recording)
        self.assertEqual(self.ready, [])
        self.assertEqual(len(self.failed), 2)

    def test_start_failure_is_sanitized_and_can_retry(self):
        recorder = self.recorder()
        self.provider.side_effect = RuntimeError("private machine detail")
        recorder.start()
        self.assertNotIn("private", self.failed[0][1])
        self.provider.side_effect = None
        recorder.start()
        self.assertTrue(recorder.is_recording)

    def test_synchronous_backend_failure_during_start_cannot_resurrect_capture(self):
        recorder = self.recorder()

        def factory(*args):
            audio = FakeAudio(*args)
            self.audio.append(audio)

            def start():
                audio.audio_error = QAudio.OpenError
                audio.stateChanged.emit(QAudio.StoppedState)
                return audio.source

            audio.start = start
            return audio

        recorder._audio_factory = factory
        started = []
        recorder.started.connect(started.append)
        recorder.start()
        self.assertFalse(recorder.is_recording)
        self.assertEqual(len(self.failed), 1)
        self.assertEqual(self.ready, [])
        self.assertEqual(started, [])
        self.assertIsNone(recorder._timer)
        self.assertEqual(self.audio[-1].stops, 1)

    def test_read_failure_releases_resources_and_does_not_publish_partial_audio(self):
        recorder = self.recorder()
        recorder.start()
        audio = self.audio[-1]
        audio.source.read = Mock(side_effect=RuntimeError("private capture details"))
        audio.source.feed(b"\x00\x00")
        self.assertFalse(recorder.is_recording)
        self.assertEqual(audio.stops, 1)
        self.assertEqual(self.ready, [])
        self.assertEqual(len(self.failed), 1)
        self.assertNotIn("private", self.failed[0][1])

    def test_closed_and_already_recording_lifecycle(self):
        recorder = self.recorder()
        recorder.start()
        with self.assertRaises(DictationError):
            recorder.start()
        recorder.close()
        recorder.close()
        self.assertFalse(recorder.is_recording)
        self.assertFalse(recorder.is_current(recorder.generation))
        with self.assertRaises(DictationError):
            recorder.start()

    def test_partial_frame_is_dropped_and_invalid_limits_are_rejected(self):
        recorder = self.recorder()
        recorder.start()
        self.audio[-1].source.feed(b"\x01\x00\xff")
        recorder.stop()
        with wave.open(io.BytesIO(self.ready[0][1]), "rb") as wav:
            self.assertEqual(wav.readframes(2), b"\x01\x00")
        for limits in ({"max_duration_seconds": 121}, {"max_duration_seconds": float("nan")},
                       {"max_duration_seconds": 0}, {"max_audio_bytes": 47}, {"max_audio_bytes": 25_000_000}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.recorder(**limits)

    def test_diagnostics_report_capture_counts_and_format_without_content(self):
        recorder = self.recorder()
        diagnostics = []
        recorder.diagnostic.connect(diagnostics.append)
        generation = recorder.start()
        pcm = b"private audio sentinel!"
        self.audio[-1].source.feed(pcm)
        recorder.stop()
        self.assertEqual([event["event"] for event in diagnostics], ["capture_started", "capture_stopped"])
        stopped = diagnostics[-1]
        self.assertEqual(stopped["generation"], generation)
        self.assertEqual(stopped["reason"], "user_stop")
        self.assertEqual(stopped["captured_bytes"], len(pcm))
        self.assertEqual((stopped["sample_rate"], stopped["channels"], stopped["sample_bits"]), (16000, 1, 16))
        self.assertGreaterEqual(stopped["duration_ms"], 0)
        self.assertNotIn("private", repr(diagnostics))
        self.assertTrue(all(value is None or isinstance(value, (str, int))
                            for event in diagnostics for value in event.values()))

    def test_empty_capture_and_backend_failures_have_content_free_reasons(self):
        recorder = self.recorder()
        diagnostics = []
        recorder.diagnostic.connect(diagnostics.append)
        recorder.start()
        recorder.stop()
        self.assertEqual(diagnostics[-1]["reason"], "empty_capture")
        self.assertEqual(diagnostics[-1]["captured_bytes"], 0)
        recorder.start()
        self.audio[-1].audio_error = QAudio.IOError
        self.audio[-1].stateChanged.emit(QAudio.StoppedState)
        self.assertEqual(diagnostics[-1]["reason"], "backend_state")
        self.assertEqual(diagnostics[-1]["state"], int(QAudio.StoppedState))
        self.assertEqual(diagnostics[-1]["error"], int(QAudio.IOError))
        self.assertEqual(len(self.ready), 0)

    def test_diagnostics_distinguish_limits_cancellation_and_device_failures(self):
        for reason, kwargs in (("size_limit", {"max_audio_bytes": 52}),
                               ("duration_limit", {"max_duration_seconds": 0.015}),
                               ("cancelled", {}), ("closed", {}), ("missing_device", {})):
            with self.subTest(reason=reason):
                recorder = self.recorder(**kwargs)
                diagnostics = []
                recorder.diagnostic.connect(diagnostics.append)
                self.device.isNull.return_value = reason == "missing_device"
                recorder.start()
                if reason == "size_limit":
                    self.audio[-1].source.feed(b"\x00" * 20)
                elif reason == "duration_limit":
                    self.audio[-1].source.feed(b"\x00\x00")
                    QTest.qWait(50)
                elif reason == "cancelled":
                    recorder.cancel()
                elif reason == "closed":
                    recorder.close()
                self.assertEqual(diagnostics[-1]["reason"], reason)
                count = len(diagnostics)
                recorder.cancel()
                self.assertEqual(len(diagnostics), count)


if __name__ == "__main__":
    unittest.main()
