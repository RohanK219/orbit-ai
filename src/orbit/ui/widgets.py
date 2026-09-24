"""Small custom widgets: status indicator and audio level meter."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from . import theme


class StatusIndicator(QWidget):
    """A coloured dot plus a word describing what the pipeline is doing.

    This is the main trust signal in the overlay. During a 5 second wait the
    difference between "thinking" and a frozen application is the only thing
    stopping the user from giving up on it mid-meeting, so the dot pulses while
    work is in progress.
    """

    _BUSY_STATES = {"calibrating", "transcribing", "thinking"}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "idle"
        self._phase = 0.0

        self._dot = _Dot(self)
        self._label = QLabel("idle", self)
        self._label.setObjectName("StatusLabel")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._dot)
        layout.addWidget(self._label)

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def set_state(self, state: str) -> None:
        self._state = state
        self._label.setText(theme.STATE_LABELS.get(state, state))
        self._dot.set_colour(QColor(theme.STATE_COLOURS.get(state, theme.TEXT_DIM)))
        if state in self._BUSY_STATES:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._dot.set_opacity(1.0)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.18) % 1.0
        # Triangle wave between 0.35 and 1.0: a visible pulse without strobing.
        distance = abs(self._phase - 0.5) * 2.0
        self._dot.set_opacity(0.35 + 0.65 * distance)


class _Dot(QWidget):
    """The coloured circle inside :class:`StatusIndicator`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colour = QColor(theme.TEXT_DIM)
        self._opacity = 1.0
        self.setFixedSize(10, 10)

    def set_colour(self, colour: QColor) -> None:
        self._colour = colour
        self.update()

    def set_opacity(self, value: float) -> None:
        self._opacity = value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = QColor(self._colour)
        colour.setAlphaF(self._opacity)
        painter.setBrush(colour)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(1, 1, 8, 8))


class LevelMeter(QWidget):
    """Segmented audio level display.

    Exists to answer one question instantly: is the app actually hearing the
    meeting? Without it, a wrong audio device looks identical to a silent room
    and the user has no way to tell which. Cheap to build, saves the most
    confusing failure mode in the product.
    """

    _SEGMENTS = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._peak = 0.0
        self.setFixedHeight(10)
        self.setMinimumWidth(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # Decay so the meter falls smoothly instead of snapping to zero between
        # signal updates.
        self._decay = QTimer(self)
        self._decay.setInterval(50)
        self._decay.timeout.connect(self._fall)
        self._decay.start()

    def set_level(self, level: float) -> None:
        level = max(0.0, min(1.0, level))
        # Rise immediately, fall slowly: standard meter ballistics.
        self._level = max(self._level, level)
        self._peak = max(self._peak, level)
        self.update()

    def reset(self) -> None:
        self._level = 0.0
        self._peak = 0.0
        self.update()

    def _fall(self) -> None:
        if self._level <= 0.001 and self._peak <= 0.001:
            return
        self._level = max(0.0, self._level - 0.08)
        self._peak = max(0.0, self._peak - 0.02)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        width = self.width()
        height = self.height()
        gap = 2
        segment_width = max(1.0, (width - gap * (self._SEGMENTS - 1)) / self._SEGMENTS)

        active = int(self._level * self._SEGMENTS + 0.5)
        peak_index = int(self._peak * self._SEGMENTS + 0.5) - 1

        inactive = QColor(theme.BORDER)
        painter.setPen(Qt.PenStyle.NoPen)

        for index in range(self._SEGMENTS):
            x = index * (segment_width + gap)
            if index < active:
                painter.setBrush(self._segment_colour(index))
            elif index == peak_index:
                colour = self._segment_colour(index)
                colour.setAlphaF(0.45)
                painter.setBrush(colour)
            else:
                painter.setBrush(inactive)
            painter.drawRoundedRect(QRectF(x, 2, segment_width, height - 4), 1.5, 1.5)

    def _segment_colour(self, index: int) -> QColor:
        fraction = index / max(1, self._SEGMENTS - 1)
        if fraction > 0.88:
            return QColor("#f87171")  # clipping territory
        if fraction > 0.68:
            return QColor("#fbbf24")
        return QColor("#34d399")
