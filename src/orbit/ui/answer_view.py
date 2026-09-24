"""Scrollable multi-turn conversation feed with syntax-highlighted code.

The overlay used to show one answer and wipe it whenever the next question
arrived, which lost the previous answer and dropped follow-ups asked while an
answer was still streaming. This view instead keeps every turn in the session
and renders them stacked, newest at the bottom, so the user can scroll back to
an earlier question and answer during the call.

Two rendering problems this solves that a plain text widget does not.

**Re-render cost.** Tokens arrive faster than a full document rebuild is worth
doing, so deltas accumulate and the document is rebuilt on a timer at a fixed
rate rather than per token. Only the streaming turn changes between rebuilds, so
this stays cheap even with many turns on screen.

**Unterminated code fences.** While an answer streams, a code block is open with
no closing fence yet. Rendering treats the trailing segment as code rather than
waiting for the fence, otherwise a code answer shows as raw markdown for several
seconds and then reformats.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QTextBrowser, QWidget

from . import theme

#: Document rebuild rate while streaming. Fast enough to read as live text,
#: slow enough that Pygments is not re-run on every delta.
_RENDER_INTERVAL_MS = 90

_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+#-]*)[ \t]*\n?")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_SCROLL_EPSILON = 6


@dataclass
class _Turn:
    """One question and its streamed answer."""

    question: str = ""
    answer: str = ""
    #: True once the answer has finished streaming.
    complete: bool = False

    def code_blocks(self) -> list[str]:
        return [code for kind, code, _ in _split_segments(self.answer) if kind == "code"]


class AnswerView(QTextBrowser):
    """Renders the running conversation as a scrollable feed."""

    def __init__(self, font_size: int = 15, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._turns: list[_Turn] = []
        self._font_size = font_size
        self._follow = True
        self._dirty = False
        self._suppress_scroll_signal = False
        self._placeholder = ""

        self.setOpenExternalLinks(False)
        self.setReadOnly(True)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        self._timer = QTimer(self)
        self._timer.setInterval(_RENDER_INTERVAL_MS)
        self._timer.timeout.connect(self._render_if_dirty)

    # -- public API --------------------------------------------------------

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        self._render_now()

    def set_placeholder(self, text: str) -> None:
        """Shown when the feed is empty."""
        self._placeholder = text
        if not self._turns:
            self._render_placeholder()

    def add_question(self, text: str) -> None:
        """Begin a new turn with the transcribed question.

        Called when a question is recognised, before the answer starts. Keeps
        the whole prior conversation on screen.
        """
        self._turns.append(_Turn(question=text))
        self._follow = True
        self._dirty = True
        if not self._timer.isActive():
            self._timer.start()
        self._render_now()

    def start_answer(self) -> None:
        """Mark that the answer to the current turn is starting.

        If no question turn exists (a forced answer with no transcript shown),
        create one so deltas have somewhere to land.
        """
        if not self._turns or self._turns[-1].complete:
            self._turns.append(_Turn())
        self._follow = True
        self._dirty = True
        if not self._timer.isActive():
            self._timer.start()

    def append_delta(self, text: str) -> None:
        if not self._turns:
            self._turns.append(_Turn())
        self._turns[-1].answer += text
        self._dirty = True

    def finish_answer(self) -> None:
        """Flush the current turn and stop the render timer."""
        if self._turns:
            self._turns[-1].complete = True
        self._timer.stop()
        self._render_now()

    def clear_feed(self) -> None:
        """Wipe the whole conversation. Bound to Clear."""
        self._timer.stop()
        self._turns = []
        self._follow = True
        self._dirty = False
        self._render_placeholder()

    def show_message(self, text: str, *, colour: str | None = None) -> None:
        """Display a one-off message such as an error, appended to the feed."""
        self._timer.stop()
        shade = colour or theme.TEXT_DIM
        body = html.escape(text).replace("\n", "<br>")
        block = (
            f'<div style="color:{shade}; font-family:\'{theme.UI_FONT}\'; '
            f'font-size:{self._font_size}px; line-height:150%; '
            f'margin:6px 0;">{body}</div>'
        )
        # Render existing turns above the message so context is not lost.
        self.setHtml(self._document_html(extra=block))
        self._scroll_to_end()

    @property
    def text(self) -> str:
        """The most recent answer, for tests and the copy action."""
        return self._turns[-1].answer if self._turns else ""

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    def code_blocks(self) -> list[str]:
        """Every code block across the whole feed, in order."""
        blocks: list[str] = []
        for turn in self._turns:
            blocks.extend(turn.code_blocks())
        return blocks

    def copy_last_code_block(self) -> bool:
        """Copy the final code block in the feed. False if there is none."""
        blocks = self.code_blocks()
        if not blocks:
            return False
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(blocks[-1].rstrip())
        return True

    # -- scrolling ---------------------------------------------------------

    def _on_scrolled(self, value: int) -> None:
        if self._suppress_scroll_signal:
            return
        bar = self.verticalScrollBar()
        # Scrolling up parks the view; returning to the bottom resumes following
        # the newest turn.
        self._follow = value >= bar.maximum() - _SCROLL_EPSILON

    def _scroll_to_end(self) -> None:
        bar = self.verticalScrollBar()
        self._suppress_scroll_signal = True
        try:
            bar.setValue(bar.maximum())
        finally:
            self._suppress_scroll_signal = False

    # -- rendering ---------------------------------------------------------

    def _render_placeholder(self) -> None:
        if not self._placeholder:
            self.clear()
            return
        body = html.escape(self._placeholder).replace("\n", "<br>")
        self.setHtml(
            f'<div style="color:{theme.TEXT_DIM}; font-family:\'{theme.UI_FONT}\'; '
            f'font-size:{self._font_size}px; line-height:150%;">{body}</div>'
        )

    def _render_if_dirty(self) -> None:
        if self._dirty:
            self._render_now()

    def _render_now(self) -> None:
        if not self._turns:
            self._render_placeholder()
            self._dirty = False
            return

        bar = self.verticalScrollBar()
        previous = bar.value()

        self._suppress_scroll_signal = True
        try:
            self.setHtml(self._document_html())
            if self._follow:
                bar.setValue(bar.maximum())
            else:
                bar.setValue(min(previous, bar.maximum()))
        finally:
            self._suppress_scroll_signal = False
        self._dirty = False

    def _document_html(self, *, extra: str = "") -> str:
        blocks = [self._render_turn(turn, index) for index, turn in enumerate(self._turns)]
        if extra:
            blocks.append(extra)
        body = "".join(blocks)
        return (
            f'<div style="font-family:\'{theme.UI_FONT}\'; '
            f'font-size:{self._font_size}px; color:{theme.TEXT}; '
            f'line-height:152%;">{body}</div>'
        )

    def _render_turn(self, turn: _Turn, index: int) -> str:
        parts: list[str] = []

        # A divider above every turn after the first, so the boundary between an
        # older answer and a newer one is unmistakable when scrolling.
        if index > 0:
            parts.append(
                f'<div style="border-top:1px solid {theme.BORDER}; '
                f'margin:12px 0 8px 0;"></div>'
            )

        if turn.question:
            parts.append(
                f'<div style="color:{theme.TEXT_DIM}; '
                f'font-size:{max(10, self._font_size - 2)}px; '
                f'border-left:2px solid {theme.ACCENT}; padding:0 0 0 8px; '
                f'margin:0 0 6px 0;">{_inline(turn.question)}</div>'
            )

        if turn.answer:
            parts.append(self._render_answer_body(turn.answer))
        elif not turn.question:
            return ""

        return "".join(parts)

    def _render_answer_body(self, source: str) -> str:
        parts: list[str] = []
        for kind, content, language in _split_segments(source):
            if kind == "code":
                parts.append(self._render_code(content, language))
            else:
                rendered = self._render_prose(content)
                if rendered:
                    parts.append(rendered)
        return "".join(parts)

    def _render_prose(self, text: str) -> str:
        lines = text.split("\n")
        out: list[str] = []
        in_list = False

        for line in lines:
            bullet = _BULLET.match(line)
            if bullet:
                if not in_list:
                    out.append('<ul style="margin:4px 0 4px 18px; padding:0;">')
                    in_list = True
                out.append(f'<li style="margin:2px 0;">{_inline(bullet.group(1))}</li>')
                continue

            if in_list:
                out.append("</ul>")
                in_list = False

            if not line.strip():
                out.append('<div style="height:7px;"></div>')
                continue

            out.append(f'<div style="margin:1px 0;">{_inline(line)}</div>')

        if in_list:
            out.append("</ul>")
        return "".join(out)

    def _render_code(self, code: str, language: str) -> str:
        code = code.strip("\n")
        if not code:
            return ""

        highlighted = _highlight(code, language)
        return (
            f'<div style="background-color:{theme.CODE_BG}; '
            f"border:1px solid {theme.BORDER}; border-radius:4px; "
            f'margin:8px 0; padding:9px 11px;">'
            f'<pre style="margin:0; font-family:{theme.CODE_FONT}; '
            f'font-size:{max(11, self._font_size - 1)}px; '
            f'line-height:138%; white-space:pre-wrap;">{highlighted}</pre>'
            f"</div>"
        )


# ----------------------------------------------------------------------
# Parsing and highlighting helpers
# ----------------------------------------------------------------------

def _split_segments(source: str) -> list[tuple[str, str, str]]:
    """Split markdown-ish text into prose and code segments.

    Returns ``(kind, content, language)`` tuples where ``kind`` is ``"prose"`` or
    ``"code"``. A trailing unclosed fence is treated as code, which is the normal
    state while an answer containing code is still streaming.
    """
    segments: list[tuple[str, str, str]] = []
    position = 0
    in_code = False
    language = ""

    while True:
        match = _FENCE.search(source, position)
        if match is None:
            tail = source[position:]
            if tail:
                segments.append(("code" if in_code else "prose", tail, language))
            return segments

        chunk = source[position : match.start()]
        if chunk:
            segments.append(("code" if in_code else "prose", chunk, language))

        if in_code:
            in_code = False
            language = ""
        else:
            in_code = True
            language = match.group(1) or ""

        position = match.end()


def _inline(text: str) -> str:
    """Escape a line of prose and apply inline bold and code formatting."""
    escaped = html.escape(text)
    escaped = _BOLD.sub(r"<b>\1</b>", escaped)
    escaped = _INLINE_CODE.sub(
        f'<span style="font-family:{theme.CODE_FONT}; '
        f'background-color:{theme.CODE_BG}; padding:1px 4px; '
        r'border-radius:3px;">\1</span>',
        escaped,
    )
    return escaped


def _highlight(code: str, language: str) -> str:
    """Syntax highlight with Pygments, degrading to escaped plain text.

    ``noclasses`` inlines the styles because Qt's rich text engine does not
    support stylesheet classes, and ``nowrap`` keeps Pygments from emitting its
    own container so the caller controls the ``<pre>``.
    """
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name, guess_lexer
        from pygments.util import ClassNotFound

        lexer = None
        if language:
            try:
                lexer = get_lexer_by_name(language, stripall=False)
            except ClassNotFound:
                lexer = None
        if lexer is None:
            try:
                lexer = guess_lexer(code)
            except ClassNotFound:
                return _plain_code(code)

        formatter = HtmlFormatter(noclasses=True, nowrap=True, style="monokai")
        return highlight(code, lexer, formatter).rstrip("\n")
    except Exception:
        # Highlighting is cosmetic. Never let it break the answer display.
        return _plain_code(code)


def _plain_code(code: str) -> str:
    return f'<span style="color:{theme.TEXT};">{html.escape(code)}</span>'
