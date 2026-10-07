"""Setup window: the first thing you see, and where all configuration lives.

Keeping configuration here is what lets the overlay stay down to four controls.
You set this up once before a meeting, press Start, and this window gets out of
the way.
"""

from __future__ import annotations

import platform

from PySide6.QtCore import QPointF, Qt, QThread, Signal
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import create_api_client, load_api_key
from ..core.audiotest import AudioTestWorker
from ..settings import Settings, delete_api_key, has_api_key, save_api_key
from . import theme
from .widgets import LevelMeter

LLM_MODELS = ["gpt-4o-mini", "gpt-4o"]
STT_MODELS = [
    ("API: gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe"),
    ("API: gpt-4o-transcribe", "gpt-4o-transcribe"),
    ("API: whisper-1", "whisper-1"),
    ("Local: faster-whisper", "local-whisper"),
]
LANGUAGES = [
    ("English", "en"),
    ("Auto-detect", "auto"),
    ("Hindi", "hi"),
    ("Marathi", "mr"),
    ("Spanish", "es"),
    ("German", "de"),
    ("French", "fr"),
]


class ScrollPageComboBox(QComboBox):
    """Let wheel scrolling pass through to the setup page while the list is closed."""

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.view().isVisible():
            parent = self.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent is None:
                event.ignore()
                return

            global_position = event.globalPosition()
            forwarded = QWheelEvent(
                QPointF(parent.viewport().mapFromGlobal(global_position.toPoint())),
                global_position,
                event.pixelDelta(),
                event.angleDelta(),
                event.buttons(),
                event.modifiers(),
                event.phase(),
                event.inverted(),
                event.source(),
            )
            QApplication.sendEvent(parent.viewport(), forwarded)
            event.setAccepted(forwarded.isAccepted())
            return
        super().wheelEvent(event)


class MainWindow(QWidget):
    """Configuration and session launcher."""

    start_requested = Signal()

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._test_thread: QThread | None = None
        self._test_worker: AudioTestWorker | None = None

        self.setWindowTitle("orbit-ai — setup")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        available_width = available.width() if available is not None else 1200
        available_height = available.height() if available is not None else 900
        self.setMinimumSize(min(480, available_width), min(420, available_height))

        self._build_ui()
        self._load_from_settings()
        self.refresh_devices()
        self._refresh_key_status()
        self.resize(min(680, available_width), min(820, available_height))

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        content = QWidget(self)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(10)

        scroll = QScrollArea(self)
        scroll.setObjectName("SetupScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        heading = QLabel("orbit-ai", self)
        heading.setObjectName("Heading")
        content_layout.addWidget(heading)

        subtitle = QLabel(
            "Listens to your meeting's audio, transcribes the questions, and "
            "shows answers on a floating panel. Your microphone is never used.",
            self,
        )
        subtitle.setObjectName("SectionLabel")
        subtitle.setWordWrap(True)
        content_layout.addWidget(subtitle)

        content_layout.addWidget(self._build_key_group())
        content_layout.addWidget(self._build_audio_group())
        content_layout.addWidget(self._build_model_group())
        content_layout.addWidget(self._build_tuning_group())

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("SectionLabel")
        self.status_label.setWordWrap(True)
        content_layout.addWidget(self.status_label)
        content_layout.addStretch(1)

        self.start_button = QPushButton("Start Transcript", self)
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self._on_start_clicked)
        root.addWidget(self.start_button)

        note = QLabel(
            "Meeting audio is sent to your selected transcription provider unless "
            "Local: faster-whisper is selected. Transcribed text is sent to your "
            "selected answer provider. Nothing is saved to disk.",
            self,
        )
        note.setObjectName("SectionLabel")
        note.setWordWrap(True)
        root.addWidget(note)

    def _build_key_group(self) -> QGroupBox:
        group = QGroupBox("AI provider (OpenAI-compatible)", self)
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self.api_base_url_edit = QLineEdit(group)
        self.api_base_url_edit.setPlaceholderText(
            "Optional — defaults to https://api.openai.com/v1"
        )
        self.api_base_url_edit.setToolTip(
            "Use an OpenAI-compatible API endpoint. Include its /v1 path when required."
        )
        self.llm_combo = ScrollPageComboBox(group)
        self.llm_combo.setEditable(True)
        self.llm_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.llm_combo.addItems(LLM_MODELS)
        self.llm_combo.setToolTip(
            "Choose a model returned by Load models, or enter the model ID from your provider."
        )

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("API base URL", self.api_base_url_edit)
        form.addRow("Answer model", self.llm_combo)
        layout.addLayout(form)

        self.key_edit = QLineEdit(group)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste provider API key")
        self.save_key_button = QPushButton("Save", group)
        self.save_key_button.clicked.connect(self._on_save_key)
        self.forget_key_button = QPushButton("Forget", group)
        self.forget_key_button.clicked.connect(self._on_forget_key)
        self.load_models_button = QPushButton("Load models", group)
        self.load_models_button.clicked.connect(self._load_models)

        row.addWidget(self.key_edit, 1)
        row.addWidget(self.save_key_button)
        row.addWidget(self.forget_key_button)
        layout.addLayout(row)
        layout.addWidget(self.load_models_button)

        self.key_status = QLabel("", group)
        self.key_status.setObjectName("SectionLabel")
        self.key_status.setWordWrap(True)
        layout.addWidget(self.key_status)
        return group

    def _build_audio_group(self) -> QGroupBox:
        group = QGroupBox("Audio source", self)
        layout = QVBoxLayout(group)

        row = QHBoxLayout()
        self.device_combo = ScrollPageComboBox(group)
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
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.stt_combo = ScrollPageComboBox(group)
        for label, value in STT_MODELS:
            self.stt_combo.addItem(label, value)
        self.local_model_edit = QLineEdit(group)
        self.local_model_edit.setPlaceholderText("base, small, medium...")
        self.local_model_edit.setToolTip("faster-whisper model name used for local transcription.")

        self.language_combo = ScrollPageComboBox(group)
        for label, code in LANGUAGES:
            self.language_combo.addItem(label, code)

        self.vocab_edit = QLineEdit(group)
        self.vocab_edit.setPlaceholderText("Kubernetes, gRPC, idempotent")
        self.vocab_edit.setToolTip(
            "Comma-separated terms to help transcription with jargon and names."
        )
        self.target_language_edit = QLineEdit(group)
        self.target_language_edit.setPlaceholderText("Leave blank to keep original language")
        self.target_language_edit.setToolTip("Translate finalized questions to this language.")

        layout.addRow("Transcription", self.stt_combo)
        layout.addRow("Local Whisper model", self.local_model_edit)
        layout.addRow("Language", self.language_combo)
        layout.addRow("Vocabulary hints", self.vocab_edit)
        layout.addRow("Translate questions to", self.target_language_edit)
        return group

    def _build_tuning_group(self) -> QGroupBox:
        group = QGroupBox("Tuning", self)
        layout = QFormLayout(group)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

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
            "Uses built-in OpenAI rate estimates; custom provider pricing may differ. "
            "0 means no limit."
        )

        self.drain_check = QCheckBox(
            "Drop stale audio left by a long answer", group
        )
        self.diarization_check = QCheckBox("Label alternating participants", group)
        self.export_check = QCheckBox("Allow explicit session export", group)
        self.domain_edit = QLineEdit(group)
        self.domain_edit.setPlaceholderText("Folder with private reference files")
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
        layout.addRow("Domain knowledge folder", self.domain_edit)
        layout.addRow("", self.diarization_check)
        layout.addRow("", self.export_check)
        return group

    # -- settings binding --------------------------------------------------

    def _load_from_settings(self) -> None:
        s = self._settings
        self.llm_combo.setCurrentText(s.llm_model)
        self.api_base_url_edit.setText(s.api_base_url)
        stt_index = self.stt_combo.findData(s.stt_model)
        self.stt_combo.setCurrentIndex(stt_index if stt_index >= 0 else 0)
        self.local_model_edit.setText(s.local_whisper_model)

        index = self.language_combo.findData(s.language)
        self.language_combo.setCurrentIndex(index if index >= 0 else 0)

        self.vocab_edit.setText(s.vocabulary_hint)
        self.target_language_edit.setText(s.target_language)
        self.domain_edit.setText(s.domain_knowledge_dir)
        self.diarization_check.setChecked(s.enable_diarization)
        self.export_check.setChecked(s.allow_session_export)
        self.silence_spin.setValue(s.silence_ms)
        self.font_spin.setValue(s.font_size)
        self.opacity_spin.setValue(s.opacity_percent)
        self.limit_spin.setValue(s.session_cost_limit_usd)
        self.drain_check.setChecked(s.drop_stale_audio)

    def collect_settings(self) -> Settings:
        """Read the form back into the settings object and persist it."""
        s = self._settings
        s.llm_model = self.llm_combo.currentText()
        s.api_base_url = self.api_base_url_edit.text().strip()
        s.stt_model = self.stt_combo.currentData() or "gpt-4o-mini-transcribe"
        s.local_whisper_model = self.local_model_edit.text().strip() or "base"
        s.language = self.language_combo.currentData() or "en"
        s.vocabulary_hint = self.vocab_edit.text().strip()
        s.target_language = self.target_language_edit.text().strip()
        s.domain_knowledge_dir = self.domain_edit.text().strip()
        s.enable_diarization = self.diarization_check.isChecked()
        s.allow_session_export = self.export_check.isChecked()
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

        if platform.system() == "Darwin":
            try:
                import sounddevice as sd

                for index, device in enumerate(sd.query_devices()):
                    if device.get("max_input_channels", 0) > 0:
                        self.device_combo.addItem(
                            f"{device['name']} ({int(device['default_samplerate'])} Hz)",
                            index,
                        )
                self.audio_status.setText(
                    "Select a CoreAudio loopback device such as BlackHole."
                )
                return
            except Exception as exc:
                self.audio_status.setText(
                    f"Could not list macOS audio devices: {exc}"
                )
                return

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

        try:
            # Validate URL syntax before persisting it or the credential.
            client = create_api_client(key, self.api_base_url_edit.text())
            client.close()
            save_api_key(key)
            self._settings.api_base_url = self.api_base_url_edit.text().strip()
            self._settings.llm_model = (
                self.llm_combo.currentText().strip() or "gpt-4o-mini"
            )
            self._settings.save()
        except Exception as exc:
            self.key_status.setText(
                f"Could not save provider settings: {type(exc).__name__}"
            )
            return

        self.key_edit.clear()
        self._refresh_key_status()
        self.key_status.setText(
            "Provider settings saved. The API key is encrypted in Windows Credential "
            "Manager. Use Load models to check the connection."
        )

    def _on_forget_key(self) -> None:
        try:
            delete_api_key()
        except Exception as exc:
            self.key_status.setText(
                f"Could not remove the API key: {type(exc).__name__}"
            )
        else:
            self._refresh_key_status()

    def _refresh_key_status(self) -> None:
        if has_api_key():
            self.key_status.setText(
                "An API key is stored in Windows Credential Manager, encrypted for "
                "your user account. It is never written to a file."
            )
        else:
            self.key_status.setText(
                "No API key stored yet. Save your provider key to Windows "
                "Credential Manager; it is not written to a config file."
            )

    def _load_models(self) -> None:
        """Load model IDs from compatible endpoints that expose a model list."""
        client = None
        try:
            client = create_api_client(
                load_api_key(), self.api_base_url_edit.text()
            )
            models = client.models.list()
            model_ids = sorted({model.id for model in models.data if model.id})
        except Exception as exc:
            self.key_status.setText(
                f"Could not load models ({type(exc).__name__}). Check the API URL/key; "
                "if model listing is unsupported, enter the model ID manually."
            )
            return
        finally:
            if client is not None:
                client.close()
        if not model_ids:
            self.key_status.setText(
                "The provider returned no models. Enter a model ID manually."
            )
            return
        selected = self.llm_combo.currentText().strip()
        self.llm_combo.clear()
        self.llm_combo.addItems(model_ids)
        self.llm_combo.setCurrentText(
            selected if selected in model_ids else model_ids[0]
        )
        self.key_status.setText(f"Loaded {len(model_ids)} model(s) from the provider.")

    # -- start -------------------------------------------------------------

    def _on_start_clicked(self) -> None:
        if not has_api_key():
            self.status_label.setText(
                "Save your provider API key above before starting."
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
    if (
        settings.consent_acknowledged
        and settings.consented_api_base_url == settings.api_base_url
        and settings.consented_stt_model == settings.stt_model
    ):
        return True

    box = QMessageBox(parent)
    box.setWindowTitle("Before you start")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText("orbit-ai processes other people's speech.")
    box.setInformativeText(
        "While listening, meeting audio is sent to the configured transcription "
        "provider (unless Local: faster-whisper is selected). Transcribed text "
        "is sent to the configured answer provider. This includes other "
        "participants' speech.\n\n"
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
    settings.consented_api_base_url = settings.api_base_url
    settings.consented_stt_model = settings.stt_model
    settings.save()
    return True
