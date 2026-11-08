"""POPME's Jua/body type pairing, palette, compact tabs and paper cards.

Design adapted from Bluewak/popme/popme/ui/index.html (MIT).
Font is bundled under SIL OFL; see assets/fonts and licenses/POPME-MIT.txt.
"""
from pathlib import Path
from html import escape
from math import ceil
from PySide6.QtCore import Qt, QRect, QRectF, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QFontDatabase, QRawFont, QPixmap, QIcon, QPainter, QColor, QPen, QPainterPath, QRegion, QTextDocument
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QLayout, QWidget, QFrame, QDialog, QScrollArea, QVBoxLayout, QSizePolicy

CREAM, PAPER, BROWN, INK, MUTED, LINE, ACCENT = "#fff6de", "#fffbf1", "#3b2219", "#52382a", "#796455", "#ecdcbf", "#e8865a"
_loaded = False
_jua = None
PANEL_WIDTH, PANEL_HEIGHT = 480, 720


class RoundedSurface(QFrame):
    """Paint a smooth alpha edge instead of clipping a styled border to integer pixels."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(BROWN), 2))
        painter.setBrush(QColor(CREAM if self.objectName() == "speech" else PAPER))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 16, 16)


class RoundedDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("roundedDialog")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    paintEvent = RoundedSurface.paintEvent


def load_fonts():
    global _loaded, _jua
    if not _loaded:
        QFontDatabase.addApplicationFont(str(Path(__file__).resolve().parents[1] / "assets/fonts/Jua-Regular.ttf"))
        for filename in ("malgun.ttf", "segoeui.ttf", "seguisym.ttf", "seguiemj.ttf", "consola.ttf"):
            path = Path("C:/Windows/Fonts") / filename
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        QFont.insertSubstitution("Jua", "Malgun Gothic")
        _jua = QRawFont.fromFont(QFont("Jua"))
        _loaded = True
    font = QFont("Malgun Gothic")
    font.setPixelSize(15)
    # Layered Windows surfaces need grayscale antialiasing; subpixel edges can disappear.
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.NoSubpixelAntialias | QFont.StyleStrategy.ContextFontMerging)
    QApplication.instance().setFont(font)


def size_popup(widget, width=PANEL_WIDTH, height=PANEL_HEIGHT, *, available=None):
    area = available if available is not None else widget.screen().availableGeometry()
    widget.setFixedSize(min(width, area.width() - 24), min(height, area.height() - 24))
    if isinstance(widget, (RoundedSurface, RoundedDialog)):
        widget.clearMask()
        return
    path = QPainterPath()
    path.addRoundedRect(0, 0, widget.width(), widget.height(), 16, 16)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


class WrappingLabel(QLabel):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.wordWrap() and self.minimumHeight() != self.maximumHeight():
            doc = QTextDocument()
            doc.setDocumentMargin(0)
            doc.setDefaultFont(self.font())
            if self.textFormat() == Qt.TextFormat.RichText: doc.setHtml(self.text())
            else: doc.setPlainText(self.text())
            # A small reserve covers fractional-DPI glyph advance rounding on Windows.
            doc.setTextWidth(max(1, self.contentsRect().width() - 12))
            margins = self.contentsMargins()
            needed = ceil(doc.size().height()) + margins.top() + margins.bottom() + 2
            if needed > 0 and needed != self.minimumHeight():
                self.setMinimumHeight(needed)


class DisplayLabel(WrappingLabel):
    """Explicit fallback runs for punctuation absent from Jua's character map."""
    @staticmethod
    def html(text):
        runs = []
        for char in str(text):
            family = "Jua" if _jua and _jua.supportsCharacter(ord(char)) else "Malgun Gothic"
            if runs and runs[-1][0] == family:
                runs[-1][1] += char
            else:
                runs.append([family, char])
        return "".join(f'<span style="font-family:{family}">{escape(value).replace(chr(10), "<br>")}</span>' for family, value in runs)

    def setText(self, text):
        super().setText(self.html(text))


STYLE = """
QWidget {font-family:'Malgun Gothic','Segoe UI';font-size:15px;color:#52382a;background:transparent;}
QDialog {background:#fffbf1;border:2px solid #3b2219;border-radius:16px;}
QWidget#popover,QFrame#speech,QDialog#roundedDialog {background:transparent;border:0;}
QFrame#header {background:#fff6de;border-bottom:2px solid #ecdcbf;border-top-left-radius:14px;border-top-right-radius:14px;}
QFrame#nav {background:#fff6de;border-bottom:2px solid #ecdcbf;}
QFrame#footer {background:#fff6de;border-top:2px solid #ecdcbf;border-bottom-left-radius:14px;border-bottom-right-radius:14px;}
QLabel {border:0;background:transparent;padding:2px 0;}
QLabel#tag {font-family:'Jua';font-size:16px;min-height:22px;color:white;background:#e8865a;border:2px solid #3b2219;border-radius:15px;padding:2px 12px;}
QPushButton {font-family:'Jua';font-size:16px;min-height:22px;background:#fffbf1;border:2px solid #3b2219;border-radius:16px;padding:5px 14px;}
QPushButton[textFallback=true] {font-family:'Malgun Gothic','Segoe UI Symbol';font-size:14px;}
QPushButton:hover {background:white;}
QPushButton:pressed {background:#f7d8bd;padding-top:7px;padding-bottom:3px;}
QPushButton:focus {border-color:#e8865a;}
QPushButton[primary=true] {background:#e8865a;color:white;}
QPushButton[primary=true]:hover {background:#dc764a;}
QPushButton[primary=true]:pressed {background:#c96841;}
QPushButton[role=icon] {border:0;background:transparent;color:#8a7465;padding:2px 5px;}
QPushButton[role=tab] {font-size:17px;border:0;border-radius:0;padding:11px 0;color:#8a7465;background:transparent;border-bottom:2px solid transparent;}
QPushButton[role=tab]:checked {color:#3b2219;border-bottom:2px solid #e8865a;}
QPushButton[role=tab]:hover {background:#fbeacd;color:#3b2219;}
QPushButton[role=chip] {font-size:14px;min-height:20px;border-width:1px;border-radius:14px;padding:3px 11px;background:white;}
QPushButton[role=chip]:checked {background:#5b6f86;border-color:#3e5068;color:white;}
QPushButton[role=filter]:checked {background:#e8865a;color:white;}
QPushButton[role=filter] {font-size:14px;min-height:20px;border-width:1px;border-radius:13px;padding:2px 10px;}
QPushButton[role=danger] {color:#b75f4b;border:1px solid #e4b8a8;background:#fff6f1;}
QPushButton[role=heading] {font-size:14px;text-align:left;border:0;border-radius:0;padding:0;background:transparent;color:#3b2219;}
QPushButton:disabled {color:#b7a896;border-color:#dccfb9;}
QFrame#news {background:white;border:1px solid #ecdcbf;border-radius:10px;}
QFrame#important {background:white;border:1px solid #ecdcbf;border-left:4px solid #e8865a;border-radius:10px;}
QFrame#detail {background:white;border:2px solid #9b6e4c;border-radius:10px;}
QFrame#note {background:#fff6de;border:2px solid #3b2219;border-radius:14px;}
QLineEdit,QPlainTextEdit,QComboBox,QListWidget {background:white;border:1px solid #ecdcbf;border-radius:9px;padding:9px 11px;selection-background-color:#f7d8bd;selection-color:#3b2219;}
QLineEdit:focus,QPlainTextEdit:focus {border-color:#e8865a;}
QListWidget::item {padding:10px 4px;border-bottom:1px solid #f3ebdb;}
QListWidget::item:selected {background:#fff6de;color:#3b2219;}
QCheckBox {spacing:10px;padding:4px 0;}
QCheckBox::indicator {width:18px;height:18px;border:1px solid #8a7465;border-radius:4px;background:white;}
QCheckBox::indicator:checked {background:#e8865a;border:2px solid #3b2219;}
QComboBox::drop-down {border:0;width:26px;}
QScrollArea {border:0;background:transparent;}
QScrollBar:vertical {background:#f6efdf;width:10px;margin:3px 1px;}
QScrollBar::handle:vertical {background:#c2a57f;border-radius:4px;min-height:36px;}
QScrollBar::handle:vertical:hover {background:#9b6e4c;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
QToolTip {background:#fff6de;color:#52382a;border:1px solid #ecdcbf;}
"""
_icons = (Path(__file__).resolve().parents[1] / "assets/icons").as_posix()
STYLE += f'''
QComboBox::down-arrow {{image:url("{_icons}/chevron-down.svg");width:16px;height:12px;}}
QCheckBox::indicator:checked {{image:url("{_icons}/check.svg");}}
QListWidget::indicator {{width:18px;height:18px;border:1px solid #8a7465;border-radius:4px;background:white;}}
QListWidget::indicator:checked {{background:#e8865a;border:1px solid #3b2219;image:url("{_icons}/check.svg");}}
'''


def label(text, size=13, color=INK, display=False):
    w = DisplayLabel() if display else WrappingLabel()
    w.setTextFormat(Qt.TextFormat.RichText if display else Qt.TextFormat.PlainText)
    w.setText(text)
    w.setWordWrap(True)
    w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    pixels = max(14, size + 2) if not display else max(16, size + 2)
    w.setStyleSheet(f"font-family:'{'Jua' if display else 'Malgun Gothic'}';font-size:{pixels}px;color:{color};")
    return w


def button(text, fn, primary=False, role=""):
    w = QPushButton(text)
    w.setProperty("textFallback", bool(_jua and any(not _jua.supportsCharacter(ord(c)) for c in text if not c.isspace())))
    w.setProperty("primary", primary)
    if role:
        w.setProperty("role", role)
    if text in {"✕", "×", "▴", "▾"}:
        # Draw controls instead of depending on a display font's symbol coverage.
        icon = QPixmap(24, 24)
        icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(MUTED), 2))
        if text in {"✕", "×"}:
            painter.drawLine(7, 7, 17, 17)
            painter.drawLine(17, 7, 7, 17)
        else:
            a, b = (14, 9) if text == "▴" else (9, 14)
            painter.drawLine(7, a, 12, b)
            painter.drawLine(12, b, 17, a)
        painter.end()
        w.setText("")
        w.setIcon(QIcon(icon))
        w.setIconSize(QSize(20, 20))
        w.setAccessibleName("닫기" if text in {"✕", "×"} else "접기" if text == "▴" else "펼치기")
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    w.clicked.connect(lambda: fn())
    return w


def scroll_body(parent_layout):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
    layout.setContentsMargins(20, 18, 20, 22)
    layout.setSpacing(12)
    scroll.setWidget(body)
    parent_layout.addWidget(scroll, 1)
    return scroll, layout


def reveal(widget, reduced=False):
    if reduced or QApplication.platformName() == "offscreen": return
    previous = getattr(widget, "_reveal", None)
    if previous:
        previous.stop()
        previous.deleteLater()
    animation = QPropertyAnimation(widget, b"windowOpacity", widget)
    animation.setDuration(150)
    animation.setStartValue(.25)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    widget._reveal = animation
    animation.start()


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class FlowLayout(QLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(7)

    def addItem(self, item): self.items.append(item)
    def count(self): return len(self.items)
    def itemAt(self, index): return self.items[index] if 0 <= index < len(self.items) else None
    def takeAt(self, index): return self.items.pop(index) if 0 <= index < len(self.items) else None
    def expandingDirections(self): return Qt.Orientation(0)
    def hasHeightForWidth(self): return True
    def heightForWidth(self, width): return self.arrange(QRect(0, 0, width, 0), False)
    def sizeHint(self): return self.minimumSize()
    def minimumSize(self): return QSize(0, 26)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, True)

    def arrange(self, rect, apply):
        x, y, row_height = rect.x(), rect.y(), 0
        for item in self.items:
            size = item.sizeHint()
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y, row_height = rect.x(), y + row_height + self.spacing(), 0
            if apply:
                item.setGeometry(QRect(x, y, min(size.width(), rect.width()), size.height()))
            x += size.width() + self.spacing()
            row_height = max(row_height, size.height())
        return y + row_height - rect.y()
