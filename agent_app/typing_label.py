"""Timed display of complete messages, with stable layout and grapheme boundaries."""
import re
from PySide6.QtCore import Qt, QTimer, Signal, QTextBoundaryFinder
from PySide6.QtWidgets import QLabel, QSizePolicy
from PySide6.QtGui import QTextDocument
from math import ceil


def sentence_lines(text, width, metrics):
    """Keep a short follow-up sentence together instead of orphaning its first word."""
    result = []
    for paragraph in str(text).split('\n'):
        if metrics.horizontalAdvance(paragraph) <= width:
            result.append(paragraph)
            continue
        parts = re.split(r'(?<=[.!?。！？])\s+', paragraph)
        line = ''
        for part in parts:
            combined = (line+' '+part).strip()
            if line and metrics.horizontalAdvance(combined) > width:
                result.append(line);line=part
            else:line=combined
        result.append(line)
    return '\n'.join(result)
from .review_theme import DisplayLabel, BROWN


def graphemes(text):
    encoded = text.encode("utf-16-le")
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    start, result = 0, []
    while (end := finder.toNextBoundary()) != -1:
        result.append(encoded[start*2:end*2].decode("utf-16-le"))
        start = end
    return result


class TypingLabel(DisplayLabel):
    typing_changed = Signal(bool)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.full_text, self.visible_text, self.clusters, self.index = "", "", [], 0
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(f"font-family:'Jua';font-size:17px;color:{BROWN};")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("누르면 바로 전체 문장을 볼 수 있어요")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance)

    @property
    def typing(self): return self.index < len(self.clusters)

    def heightForWidth(self, width):
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(self.font())
        doc.setHtml(self.text())
        doc.setTextWidth(max(1, width))
        return ceil(doc.size().height()) + 8

    def present(self, text, animate=True):
        self.timer.stop()
        self.clusters = graphemes(str(text))
        if len(self.clusters) > 180: self.clusters = self.clusters[:179] + ["…"]
        self.full_text = "".join(self.clusters)
        self.setAccessibleName(self.full_text)
        self.index = 0 if animate else len(self.clusters)
        self.render_text()
        self.typing_changed.emit(self.typing)
        if self.typing and self.isVisible(): self.timer.start(32)
        elif not self.typing: self.finished.emit()

    def render_text(self):
        self.visible_text = "".join(self.clusters[:self.index])
        rest = "".join(self.clusters[self.index:])
        # Invisible remaining text reserves the final line breaks throughout typing.
        QLabel.setText(self, '<p style="line-height:140%;margin:0">' + self.html(self.visible_text) + '<span style="color:transparent">' + self.html(rest) + '</span></p>')

    def advance(self):
        self.timer.stop()
        if not self.typing: return
        self.index += 1
        self.render_text()
        if not self.typing:
            self.typing_changed.emit(False)
            self.finished.emit()
        elif self.isVisible():
            self.timer.start(130 if self.clusters[self.index-1] in {".", "!", "?", "…", "\n"} else 32)

    def finish(self):
        if not self.typing: return
        self.timer.stop()
        self.index = len(self.clusters)
        self.render_text()
        self.typing_changed.emit(False)
        self.finished.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton: self.finish()
        else: super().mouseReleaseEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.typing: self.timer.start(32)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)
