"""Bounded, memory-only microphone capture. Transcription is a separate operation."""

import io
import math
from time import monotonic_ns
import wave

from PyQt5.QtCore import QBuffer, QIODevice, QObject, QTimer, pyqtSignal
from PyQt5.QtMultimedia import QAudio, QAudioDeviceInfo, QAudioFormat, QAudioInput


class DictationError(Exception):
    """The recording lifecycle was used incorrectly."""


class DictationRecorder(QObject):
    """Call on the owning Qt thread; connect ready to a background transcription job.

    Each start returns a generation. Cancel also invalidates completed clips, so
    consumers must check is_current before applying a late transcription result.
    No microphone is accessed until start. Audio is never written to disk.
    """

    started = pyqtSignal(int)
    ready = pyqtSignal(int, bytes)
    failed = pyqtSignal(int, str)
    cancelled = pyqtSignal(int)
    diagnostic = pyqtSignal(dict)

    def __init__(self, parent=None, *, max_duration_seconds=60, max_audio_bytes=12_000_000,
                 device_provider=None, audio_factory=None, device_name=""):
        super().__init__(parent)
        if (not isinstance(max_duration_seconds, (int, float))
                or not math.isfinite(max_duration_seconds)
                or not 0 < max_duration_seconds <= 120):
            raise ValueError("Recording duration must be between zero and 120 seconds")
        if type(max_audio_bytes) is not int or not 48 <= max_audio_bytes <= 24_000_000:
            raise ValueError("Recording size must be between 48 and 24000000 bytes")
        self._duration = max_duration_seconds
        self._max_bytes = max_audio_bytes
        self._device_provider = device_provider
        self._audio_factory = audio_factory
        if not isinstance(device_name, str):
            raise ValueError("Microphone device name must be a string")
        self._device_name = device_name
        self._generation = 0
        self._closed = False
        self._recording = False
        self._audio = self._source = self._buffer = self._timer = None
        self._format = None
        self._capacity = 0
        self._capture_started_ns = None
        self._captured_bytes = 0
        self._last_state = None
        self._ready_read_events = 0

    @property
    def generation(self):
        return self._generation

    @property
    def is_recording(self):
        return self._recording

    @staticmethod
    def input_devices():
        """Return current physical input names and labels without opening them."""
        names = dict.fromkeys(device.deviceName() for device in
                              QAudioDeviceInfo.availableDevices(QAudio.AudioInput)
                              if not device.isNull())
        return [(name, name) for name in names]

    @property
    def device_name(self):
        """An empty name follows the system default on each recording."""
        return self._device_name

    def set_device_name(self, name):
        if self._closed or self._recording:
            raise DictationError("Cannot change microphone while recording or closed")
        if not isinstance(name, str):
            raise ValueError("Microphone device name must be a string")
        if name and name not in dict(self.input_devices()):
            raise DictationError("The selected microphone is no longer available")
        self._device_name = name

    def _input_device(self):
        if self._device_name:
            return next((device for device in QAudioDeviceInfo.availableDevices(QAudio.AudioInput)
                         if device.deviceName() == self._device_name and not device.isNull()),
                        QAudioDeviceInfo())
        return (self._device_provider or QAudioDeviceInfo.defaultInputDevice)()

    def is_current(self, generation):
        return not self._closed and generation == self._generation

    def start(self):
        if self._closed or self._recording:
            raise DictationError("The recorder is closed or already recording")
        self._generation += 1
        generation = self._generation
        self._capture_started_ns = monotonic_ns()
        self._captured_bytes = 0
        self._last_state = None
        self._ready_read_events = 0
        self._format = None
        try:
            device = self._input_device()
            if device.isNull():
                self._fail(generation, reason="missing_device",
                           message="No microphone is available. Check your input device.")
                return generation
            audio_format = self._supported_format(device)
            if audio_format is None:
                self._fail(generation, reason="unsupported_format",
                           message="This microphone does not support a compatible recording format.")
                return generation
            frame_bytes = audio_format.channelCount() * 2
            capacity = min(int(self._duration * audio_format.sampleRate()) * frame_bytes,
                           self._max_bytes - 44)
            self._capacity = capacity - capacity % frame_bytes
            if self._capacity < frame_bytes:
                raise DictationError("The recording limits cannot hold a complete audio frame")
            self._format = audio_format
            self._buffer = QBuffer(self)
            self._buffer.open(QIODevice.ReadWrite)
            self._audio = (self._audio_factory or QAudioInput)(device, audio_format, self)
            audio = self._audio
            audio.stateChanged.connect(lambda state: self._state_changed(generation, audio, state))
            self._recording = True
            source = audio.start()
            if not self._recording or not self.is_current(generation):
                return generation
            if source is None or audio.error() != QAudio.NoError:
                self._fail(generation, reason="backend_start")
                return generation
            self._source = source
            source.readyRead.connect(lambda: self._ready_read(generation))
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(lambda: self._stop_generation(generation))
            self._timer.start(max(1, math.ceil(self._duration * 1000)))
            self.diagnostic.emit(self._diagnostic_payload("capture_started", "recording"))
            self.started.emit(generation)
            self._drain(generation)
        except Exception:
            # Device/backend failures can include machine-specific details.
            self._fail(generation, reason="backend_failure")
        return generation

    @staticmethod
    def _supported_format(device):
        for channels in (1, 2):
            for rate in (16000, 24000, 44100, 48000):
                audio_format = QAudioFormat()
                audio_format.setCodec("audio/pcm")
                audio_format.setSampleRate(rate)
                audio_format.setChannelCount(channels)
                audio_format.setSampleSize(16)
                audio_format.setSampleType(QAudioFormat.SignedInt)
                audio_format.setByteOrder(QAudioFormat.LittleEndian)
                if device.isFormatSupported(audio_format):
                    return audio_format
        return None

    def _state_changed(self, generation, audio, state):
        if self._recording and self.is_current(generation) and audio is self._audio:
            self._last_state = int(state)
            self.diagnostic.emit(self._diagnostic_payload("backend_state", "state_changed"))
            if audio.error() != QAudio.NoError or state == QAudio.StoppedState:
                self._fail(generation, reason="backend_state")

    def _ready_read(self, generation):
        if self._recording and self.is_current(generation):
            self._ready_read_events += 1
            self._drain(generation)

    def _drain(self, generation, *, stop_at_limit=True):
        if not self._recording or not self.is_current(generation) or self._source is None:
            return
        try:
            remaining = self._capacity - self._buffer.size()
            # The audio backend owns the pending PCM. Its pull QIODevice may
            # report zero bytesAvailable even while readyRead signals PCM.
            available = self._audio.bytesReady()
            if remaining and available:
                chunk = self._source.read(min(available, remaining))
                if self._buffer.write(chunk) != len(chunk):
                    self._fail(generation, reason="buffer_write")
                    return
                self._captured_bytes += len(chunk)
            if stop_at_limit and self._buffer.size() >= self._capacity:
                self._stop("size_limit")
        except Exception:
            self._fail(generation, reason="backend_read")

    def _stop_generation(self, generation):
        if self.is_current(generation):
            self._stop("duration_limit")

    def stop(self):
        self._stop("user_stop")

    def _stop(self, reason):
        if not self._recording:
            return
        generation = self._generation
        self._drain(generation, stop_at_limit=False)
        if not self._recording:
            return
        pcm = bytes(self._buffer.data())
        audio_format = self._format
        diagnostic = self._diagnostic_payload("capture_stopped", reason)
        self._cleanup()
        frame_bytes = audio_format.channelCount() * 2
        pcm = pcm[:len(pcm) - len(pcm) % frame_bytes]
        if not pcm:
            diagnostic.update(event="capture_failed", reason="empty_capture")
            self.diagnostic.emit(diagnostic)
            self.failed.emit(generation, "No audio was recorded. Check your microphone and try again.")
            return
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(audio_format.channelCount())
            wav.setsampwidth(2)
            wav.setframerate(audio_format.sampleRate())
            wav.writeframes(pcm)
        self.diagnostic.emit(diagnostic)
        self.ready.emit(generation, output.getvalue())

    def cancel(self):
        self._cancel("cancelled")

    def _cancel(self, reason):
        generation = self._generation
        diagnostic = (self._diagnostic_payload("capture_cancelled", reason)
                      if self._recording else None)
        self._generation += 1
        self._cleanup()
        if diagnostic is not None:
            self.diagnostic.emit(diagnostic)
        self.cancelled.emit(generation)

    def close(self):
        if not self._closed:
            self._cancel("closed")
            self._closed = True

    def _fail(self, generation, *, reason="backend_failure",
              message="Could not record audio. Check microphone access and try again."):
        if self.is_current(generation):
            diagnostic = self._diagnostic_payload("capture_failed", reason)
            self._cleanup()
            self.diagnostic.emit(diagnostic)
            self.failed.emit(generation, message)

    def _diagnostic_payload(self, event, reason):
        """Content-free capture metadata; audio and unsubmitted text stay private."""
        audio_format = self._format
        try:
            error = int(self._audio.error()) if self._audio is not None else None
        except Exception:
            error = None
        elapsed = ((monotonic_ns() - self._capture_started_ns) // 1_000_000
                   if self._capture_started_ns is not None else 0)
        source_metadata = {}
        for name, target, method in (("source_open", self._source, "isOpen"),
                                     ("source_readable", self._source, "isReadable"),
                                     ("source_bytes_available", self._source, "bytesAvailable"),
                                     ("backend_bytes_ready", self._audio, "bytesReady")):
            try:
                source_metadata[name] = getattr(target, method)() if target is not None else None
            except Exception:
                source_metadata[name] = None
        return {**source_metadata, "ready_read_events": self._ready_read_events,
                "event": event, "reason": reason, "generation": self._generation,
                "duration_ms": elapsed, "captured_bytes": self._captured_bytes,
                "sample_rate": audio_format.sampleRate() if audio_format else None,
                "channels": audio_format.channelCount() if audio_format else None,
                "sample_bits": audio_format.sampleSize() if audio_format else None,
                "state": self._last_state, "error": error}

    def _cleanup(self):
        self._recording = False
        audio, buffer, timer = self._audio, self._buffer, self._timer
        self._audio = self._source = self._buffer = self._timer = None
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        if audio is not None:
            audio.stop()
            audio.deleteLater()
        if buffer is not None:
            buffer.close()
            buffer.deleteLater()
