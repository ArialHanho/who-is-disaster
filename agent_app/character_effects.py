"""Small, short-lived touch feedback; no image loading or persistent timers."""
import math
import time
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainterPath, QPen


class TouchFeedback:
    def __init__(self):
        self.events = []

    def add(self, x, y, kind='tap'):
        self.events = self.events[-5:] + [(time.monotonic(), x, y, kind)]

    def paint(self, painter):
        now = time.monotonic()
        self.events = [e for e in self.events if now-e[0] < (1 if e[3]=='heart' else .45)]
        for start, x, y, kind in self.events:
            t = (now-start)/(1 if kind=='heart' else .45)
            painter.save()
            painter.setOpacity(math.sin(math.pi*t)*.8)
            if kind == 'heart':
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor('#d8898c'))
                for side in (-1, 1):
                    painter.save()
                    painter.translate(x+side*(8+12*t), y-18*t)
                    heart=QPainterPath()
                    heart.moveTo(0, 3)
                    heart.cubicTo(-11,-3,-4,-11,0,-5)
                    heart.cubicTo(4,-11,11,-3,0,3)
                    painter.drawPath(heart)
                    painter.restore()
            else:
                radius=4+12*t
                painter.setPen(QPen(QColor('#d39a66'),1.3))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(x-radius,y-radius,radius*2,radius*2))
            painter.restore()
