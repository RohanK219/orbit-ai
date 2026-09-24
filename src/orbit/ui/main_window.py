"""Setup window: the first thing you see, and where all configuration lives.

Keeping configuration here is what lets the overlay stay down to four controls.
You set this up once before a meeting, press Start, and this window gets out of
the way.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import load_api_key
from ..core.audiotest import AudioTestWorker
from ..settings import Settings, delete_api_key, has_api_key, save_api_key
from . import theme
from .widgets import LevelMeter

LLM_MODELS = ["gpt-4o-mini", "gpt-4o"]
STT_MODELS = ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"]
LANGUAGES = [
    ("English", "en"),
    ("Auto-detect", "auto"),
    ("Hindi", "hi"),
    ("Marathi", "mr"),
    ("Spanish", "es"),
    ("German", "de"),
    ("French", "fr"),
]


class MainWindow(QWidget):
    """Configuration and session launcher."""

    start_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._test_thread: QThread | None = None
        self._test_worker: AudioTestWorker | None = None

        self.setWindowTitle("orbit-ai — setup")
        self.setMinimumWidth(560)

        self._build_ui()
        self._load_from_settings()
        self.refresh_devices()
        self._refresh_key_status()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        heading = QLabel("orbit-ai", self)
        heading.setObjectName("Heading")
        root.addWidget(heading)

        subtitle = QLabel(
            "Listens to your meeting's audio, transcribes the questions, and "
            "shows answers on a floating panel. Your microphone is never used.",
            self,
        )
        subtitle.setObjectName("SectionLabel")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        root.addWidget(self._build_key_group())
        root.addWidget(self._build_audio_group())
        root.addWidget(self._build_model_group())
        root.addWidget(self._build_tuning_group())

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("SectionLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        root.addStretch(1)

        self.start_button = QPushButton("Start Transcript", self)
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._on_start_clicked)
        root.addWidget(self.start_button)

        note = QLabel(
            "Meeting audio is sent to OpenAI for transcription. Nothing is saved "
            "to disk. Consent requirements vary by country and state.",
            self,
        )
        note.setObjectName("SectionLabel")
        note.setWordWrap(True)
        root.addWidget(note)

    def _build_key_group(self) -> QGroupBox:
        group = QGroupBox("OpenAI API key", self)
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self.key_edit = QLineEdit(group)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.save_key_button = QPushButton("Save", group)
        self.save_key_button.clicked.connect(self._on_save_key)
        self.forget_key_button = QPushButton("Forget", group)
        self.forget_key_button.clicked.connect(self._on_forget_key)

        row.addWidget(self.key_edit, 1)
        row.addWidget(self.save_key_button)
        row.addWidget(self.forget_key_button)
        layout.addLayout(row)

        self.key_status = QLabel("", group)
        self.key_status.setObjectName("SectionLabel")
        self.key_status.setWordWrap(True)
        layout.addWidget(self.key_status)
        return group

    def _build_audio_group(self) -> QGroupBox:
        group = QGroupBox("Audio source", self)
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self.device_combo = QComboBox(group)
        self.refresh_button = QPushButton("Refresh", group)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.test_button = QPushButton("Test audio", group)
        self.test_button.clicked.connect(self._on_test_audio)
        row.addWidget(self.device_combo, 1)
        row.addWidget(self.refresh_button)
        row.addWidget(self.test_button)
        layout.addLayout(row)

        meter_row = QHBoxLayout()
        self.test_meter = LevelMeter(group)
        meter_row.addWidget(QLabel("Level", group))
        meter_row.addWidget(self.test_meter, 1)
        layout.addLayout(meter_row)

        self.audio_status = QLabel(
            "Play some meeting audio, then press Test audio.", group
        )
        self.audio_status.setObjectName("SectionLabel")
        self.audio_status.setWordWrap(True)
        layout.addWidget(self.audio_status)
        return group

    def _build_model_group(self) -> QGroupBox:
        group = QGroupBox("Models", self)
        layout = QFormLayout(group)

        self.llm_combo = QComboBox(group)
        self.llm_combo.addItems(LLM_MODELS)
        self.llm_combo.setToolTip(
            "gpt-4o-mini is fast and cheap. gpt-4o is stronger on hard problems "
            "but slower and costs more."
        )

        self.stt_combo = QComboBox(group)
        self.stt_combo.addItems(STT_MODELS)

        self.language_combo = QComboBox(group)
        for label, code in LANGUAGES:
            self.language_combo.addItem(label, code)

        self.vocab_edit = QLineEdit(group)
        self.vocab_edit.setPlaceholderText("Kubernetes, gRPC, idempotent")
        self.vocab_edit.setToolTip(
            "Comma-separated terms to help transcription with jargon and names."
        )

        layout.addRow("Answers", self.llm_combo)
        layout.addRow("Transcription", self.stt_combo)
        layout.addRow("Language", self.language_combo)
        layout.addRow("Vocabulary hints", self.vocab_edit)
        return group

    def _build_tuning_group(self) -> QGroupBox:
        group = QGroupBox("Tuning", self)
        layout = QFormLayout(group)

        self.silence_spin = QSpinBox(group)
        self.silence_spin.setRange(200, 3000)
        self.silence_spin.setSingleStep(50)
        self.silence_spin.setSuffix(" ms")
        self.silence_spin.setToolTip(
            "Silence before a question counts as finished. Lower feels faster "
            "but risks cutting people off mid-sentence."
        )

        self.font_spin = QSpinBox(group)
        self.font_spin.setRange(9, 32)
        self.font_spin.setSuffix(" px")

        self.opacity_spin = QSpinBox(group)
        self.opacity_spin.setRange(30, 100)
        self.opacity_spin.setSuffix(" %")

        self.limit_spin = QDoubleSpinBox(group)
        self.limit_spin.setRange(0.0, 100.0)
        self.limit_spin.setDecimals(2)
        self.limit_spin.setPrefix("$ ")
        self.limit_spin.setToolTip(
            "Stop making paid calls after this much spend in one session. "
            "0 means no limit."
        )

        self.drain_check = QCheckBox(
            "Drop stale audio left by a long answer", group
        )
        self.drain_check.setToolTip(
            "Off by default. Follow-up questions asked while an answer is still "
            "generating are normally kept and shown as the next turn. Enable "
            "this only if long answers leave the assistant lagging behind the "
            "conversation."
        )

        layout.addRow("End of question", self.silence_spin)
        layout.addRow("Overlay text size", self.font_spin)
        layout.addRow("Overlay opacity", self.opacity_spin)
        layout.addRow("Session spend limit", self.limit_spin)
        layout.addRow("", self.drain_check)
        return group

    # -- settings binding --------------------------------------------------

    def _load_from_settings(self) -> None:
        s = self._settings
        self.llm_combo.setCurrentText(s.llm_model)
        self.stt_combo.setCurrentText(s.stt_model)

        index = self.language_combo.findData(s.language)
        self.language_combo.setCurrentIndex(index if index >= 0 else 0)

        self.vocab_edit.setText(s.vocabulary_hint)
        self.silence_spin.setValue(s.silence_ms)
        self.font_spin.setValue(s.font_size)
        self.opacity_spin.setValue(s.opacity_percent)
        self.limit_spin.setValue(s.session_cost_limit_usd)
        self.drain_check.setChecked(s.drop_stale_audio)

    def collect_settings(self) -> Settings:
        """Read the form back into the settings object and persist it."""
        s = self._settings
        s.llm_model = self.llm_combo.currentText()
        s.stt_model = self.stt_combo.currentText()
        s.language = self.language_combo.currentData() or "en"
        s.vocabulary_hint = self.vocab_edit.text().strip()
        s.silence_ms = self.silence_spin.value()
        s.font_size = self.font_spin.value()
        s.opacity_percent = self.opacity_spin.value()
        s.session_cost_limit_usd = self.limit_spin.value()
        s.drop_stale_audio = self.drain_check.isChecked()
        s.audio_device_index = self.device_combo.currentData()
        s.save()
        return s

    # -- devices -----------------------------------------------------------

    def refresh_devices(self) -> None:
        """Re-enumerate loopback devices into the combo box."""
        self.device_combo.clear()
        self.device_combo.addItem("Automatic (default output device)", None)

        try:
            import pyaudiowpatch as pyaudio

            from ..audio.devices import list_loopback_devices

            with pyaudio.PyAudio() as pa:
                devices = list_loopback_devices(pa)
        except Exception as exc:
            self.audio_status.setText(f"Could not list audio devices: {exc}")
            return

        for device in devices:
            self.device_combo.addItem(
                f"{device.name}  ({device.sample_rate} Hz, {device.channels}ch)",
                device.index,
            )

        wanted = self._settings.audio_device_index
        if wanted is not None:
            index = self.device_combo.findData(wanted)
            if index >= 0:
                self.device_combo.setCurrentIndex(index)

        if not devices:
            self.audio_status.setText(
                "No loopback devices found. Check that a playback device is "
                "enabled in Windows Sound settings."
            )

    # -- audio test --------------------------------------------------------

    def _on_test_audio(self) -> None:
        if self._test_thread is not None:
            return

        self.test_button.setEnabled(False)
        self.test_meter.reset()
        self.audio_status.setText("Listening for three seconds...")

        thread = QThread(self)
        worker = AudioTestWorker(self.device_combo.currentData())
        worker.moveToThread(thread)

        worker.level_changed.connect(self.test_meter.set_level)
        worker.completed.connect(self._on_test_complete)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_test_finished)

        self._test_thread = thread
        self._test_worker = worker
        thread.start()

    def _on_test_complete(self, peak: float, message: str) -> None:
        if message:
            self.audio_status.setText(message)
        else:
            self.audio_status.setText(
                f"Audio detected, peak level {peak:.2f}. Capture is working."
            )

    def _on_test_finished(self) -> None:
        thread = self._test_thread
        if thread is not None:
            thread.quit()
            thread.wait(3000)
        self._test_thread = None
        self._test_worker = None
        self.test_button.setEnabled(True)

    # -- API key -----------------------------------------------------------

    def _on_save_key(self) -> None:
        key = self.key_edit.text().strip()
        if not key:
            self.key_status.setText("Enter a key first.")
            return
        if not key.startswith("sk-"):
            confirm = QMessageBox.question(
                self,
                "Unusual key format",
                "That does not look like an OpenAI key, which normally starts "
                "with 'sk-'. Save it anyway?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        try:
            save_api_key(key)
        except Exception as exc:
            self.key_status.setText(f"Could not save the key: {exc}")
            return

        self.key_edit.clear()
        self._refresh_key_status()
        self._validate_key()

    def _on_forget_key(self) -> None:
        delete_api_key()
        self._refresh_key_status()

    def _refresh_key_status(self) -> None:
        if has_api_key():
            self.key_status.setText(
                "A key is stored in Windows Credential Manager, encrypted for "
                "your user account. It is never written to a file."
            )
        else:
            self.key_status.setText(
                "No key stored yet. It goes into Windows Credential Manager, "
                "not a config file."
            )

    def _validate_key(self) -> None:
        """Cheap liveness check so a bad key fails now, not mid-meeting."""
        try:
            from openai import OpenAI

            client = OpenAI(api_key=load_api_key())
            client.models.list()
        except Exception as exc:
            self.key_status.setText(f"The key was saved but rejected: {exc}")
            return
        self.key_status.setText("Key saved and verified against OpenAI.")

    # -- start -------------------------------------------------------------

    def _on_start_clicked(self) -> None:
        if not has_api_key():
            self.status_label.setText(
                "Add your OpenAI API key above before starting."
            )
            self.key_edit.setFocus()
            return
        self.status_label.clear()
        self.collect_settings()
        self.start_requested.emit()

    # -- lifecycle ---------------------------------------------------------

    def show_message(self, text: str) -> None:
        self.status_label.setText(text)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        try:
            self.collect_settings()
        except Exception:
            pass
        super().closeEvent(event)


def show_consent_notice(parent: QWidget | None, settings: Settings) -> bool:
    """One-time disclosure about sending participant audio to a third party.

    Returns True if the user accepted. Recorded in settings so it is shown once.
    """
    if settings.consent_acknowledged:
        return True

    box = QMessageBox(parent)
    box.setWindowTitle("Before you start")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText("orbit-ai processes other people's speech.")
    box.setInformativeText(
        "While listening, the audio coming out of your speakers is sent to "
        "OpenAI to be transcribed. That includes what other meeting "
        "participants say.\n\n"
        "Nothing is written to disk, and your microphone is never used. Even "
        "so, recording or processing other people's speech carries legal "
        "obligations in the EU, the UK, and several US states.\n\n"
        "Please make sure you are entitled to do this before continuing."
    )
    box.setStandardButtons(
        QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Ok
    )
    box.setDefaultButton(QMessageBox.StandardButton.Ok)

    if box.exec() != QMessageBox.StandardButton.Ok:
        return False

    settings.consent_acknowledged = True
    settings.save()
    return True
