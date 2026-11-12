"""Shared native character drawing for the resident pet and action preview."""
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen
from .actions import ACTION_NAMES
from .character_settings import BUILTINS
from .sprite_raster import draw_sprite, nameplate_y


def draw_character(p, config, frame, state, *, style="ember_fox", custom=None, look=(0, 0), hover=0, pressed=False, motion=True, height=164, pose=None, layers=None, stage_width=142, title_override=None, show_nameplate=True):
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    accent = QColor(BUILTINS.get(style, ("", config["accent"]))[1])
    ink = QColor("#3b2219")
    font = QFont("Jua" if config["kind"] == "code" else "Malgun Gothic")
    font.setFamilies([font.family(), "Malgun Gothic", "Segoe UI Symbol"])
    compact_badge = bool(layers)
    font.setPixelSize(14 if compact_badge else 15)
    p.setFont(font)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(59, 34, 25, 20))
    bottom = height-12 if custom is not None and not custom.isNull() else height-21
    shadow_width = min(84, (height-48)*custom.width()/custom.height()*.7) if custom is not None and not custom.isNull() else 84
    p.drawEllipse(QRectF((142-shadow_width)/2, bottom-3 if custom is not None else bottom, shadow_width, 7 if custom is not None else 10))
    p.setBrush(QColor("#fff6de"))
    title = title_override or (config["character_name"] if state == "idle" else ACTION_NAMES.get(state, "코드 비서"))
    badge_height = 26 if compact_badge else 30
    badge_width = min(136,max(112,p.fontMetrics().horizontalAdvance(title)+24)) if compact_badge else 136
    badge_x = 71-badge_width/2
    p.setPen(QPen(accent if hover > .04 else ink, 1.3 if compact_badge else 1.5))
    badge_y = nameplate_y(custom,layers,height,stage_width,pose.lift if pose is not None and motion else 0,badge_height)
    if show_nameplate: p.drawRoundedRect(QRectF(badge_x,badge_y,badge_width,badge_height),12,12)
    p.setPen(ink)
    title = p.fontMetrics().elidedText(title,Qt.TextElideMode.ElideRight,round(badge_width-16))
    if show_nameplate: p.drawText(QRectF(badge_x+8,badge_y,badge_width-16,badge_height),Qt.AlignmentFlag.AlignCenter,title)
    p.save()
    bob = math.sin(frame / 7) * 2 if motion and pose is None else 0
    if motion and pose is None and state in {"success", "happy"}: bob = -abs(math.sin(frame / 2)) * 7
    p.translate(71 + (pose.shift if pose is not None and motion else 0), bottom + (pose.lift if pose is not None and motion else bob) - (hover * 2 if pose is None else 0))
    if pose is not None and motion:
        p.rotate(pose.angle)
        p.scale(1, pose.stretch)
    elif motion:
        p.rotate(math.sin(frame / 2) * 6 if state == "dragging" else math.sin(frame * 2) * 2 if state == "error" else 0)
        if pressed: p.scale(1.05, .94)
    p.translate(-71, -bottom)
    if custom is not None and not custom.isNull():
        fitted = custom.size().scaled(stage_width-10, height-48, Qt.AspectRatioMode.KeepAspectRatio)
        target = QRectF((142-fitted.width())/2, 36+(height-48-fitted.height())/2, fitted.width(), fitted.height())
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if layers:
            if len(layers) == 1:
                source, _, scale = layers[0]
                scaled = QRectF(target.center().x()-target.width()*scale/2,target.bottom()-target.height()*scale,target.width()*scale,target.height()*scale)
                draw_sprite(p,scaled,source)
            else:
                # Blend premultiplied pixels additively offscreen: overlap stays opaque.
                ratio = p.device().devicePixelRatioF()*max(abs(p.transform().m11()),abs(p.transform().m22()))
                blend = QImage(max(1,math.ceil(target.width()*ratio)),max(1,math.ceil(target.height()*ratio)),QImage.Format.Format_ARGB32_Premultiplied)
                blend.setDevicePixelRatio(ratio)
                blend.fill(Qt.GlobalColor.transparent)
                overlay = QPainter(blend)
                overlay.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                overlay.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                for source, opacity, scale in layers:
                    overlay.setOpacity(opacity)
                    rect=QRectF(target.width()*(1-scale)/2,target.height()*(1-scale),target.width()*scale,target.height()*scale)
                    draw_sprite(overlay,rect,source)
                overlay.end()
                p.drawImage(target.topLeft(),blend)
        elif style == "ember_fox":
            draw_sprite(p, target, custom)
        else:
            p.drawPixmap(target, custom, QRectF(custom.rect()))
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent.darker(112))
        for x in (40, 80): p.drawEllipse(QRectF(x, 133, 23, 10))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        body = QPainterPath()
        body.moveTo(29, 85)
        body.quadTo(24, 57, 35, 44)
        body.lineTo(49, 58)
        body.quadTo(71, 48, 94, 58)
        body.lineTo(108, 44)
        body.quadTo(120, 62, 112, 87)
        body.quadTo(121, 135, 93, 139)
        body.lineTo(49, 139)
        body.quadTo(22, 134, 29, 85)
        p.drawPath(body)
        p.setBrush(QColor("#fff7df"))
        p.drawEllipse(QRectF(37, 66, 69, 54))
        for x in (49, 86):
            p.setBrush(ink)
            if state in {"sleep", "happy", "success"} or frame % 55 > 51:
                p.setPen(QPen(ink, 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                if state in {"happy", "success"}: p.drawArc(QRectF(x-1, 80, 10, 10), 0, 180*16)
                else: p.drawLine(QPointF(x, 88), QPointF(x+9, 88))
            else:
                p.setPen(Qt.PenStyle.NoPen)
                dx = math.sin(frame / 4)*3 if state == "searching" and motion else look[0]
                p.drawEllipse(QRectF(x+dx, (77 if state == "thinking" else 80)+look[1], 8, 11))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#eeb6a0"))
        for x in (42, 89): p.drawEllipse(QRectF(x, 94, 13, 5))
        p.setPen(QPen(ink, 1.5))
        p.drawArc(QRectF(65, 91, 13, 12), 0 if state == "error" else 180*16, 180*16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#fffaf0"))
        p.drawRoundedRect(QRectF(47, 111, 50, 20), 4, 4)
        p.setPen(QPen(accent.lighter(130), 1.5))
        for a, b in [((72,113),(72,129)),((52,118),(66,118)),((78,118),(91,118))]: p.drawLine(QPointF(*a), QPointF(*b))
        if state in {"greeting", "dragging", "happy"}:
            p.setPen(QPen(accent.lighter(115), 9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(30,107), QPointF(18+(math.sin(frame)*5 if motion else 0), 86))
    if layers and not (style == "ember_fox" and state == "attention"):
        if state == "sleep":
            p.setPen(accent)
            p.drawText(QRectF(108,height*.56,29,28),Qt.AlignmentFlag.AlignCenter,"zZ")
        p.restore()
        return
    # Legacy packs keep an action cue when an optional action falls back to idle.
    if state == "searching":
        p.setPen(QPen(QColor("#c89743"), 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(103, 102, 16, 16))
        p.drawLine(QPointF(117, 116), QPointF(126, 126))
    elif state == "thinking":
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            p.setBrush(accent.lighter(175 if int(frame)%9//3 == i else 115))
            p.drawEllipse(QRectF(113+i*7, 53-i*3, 5, 5))
    elif state == "organizing":
        p.setPen(QPen(QColor("#8a6750"), 3))
        p.drawLine(QPointF(78,119), QPointF(97+(math.sin(frame)*3 if motion else 0),101))
    elif state == "attention" and style == "ember_fox":
        p.setPen(QPen(ink, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(120, 45, 12, 12), 15*16, 150*16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        p.drawRoundedRect(QRectF(120, 52, 7, 7), 2, 2)
        p.drawEllipse(QRectF(111, 55, 21, 21))
        p.setBrush(QColor("#e9965b"))
        p.drawEllipse(QRectF(129, 43, 6, 6))
        p.setBrush(QColor("#fff6de"))
        p.drawEllipse(QRectF(115, 59, 5, 4))
    elif state in {"success", "attention", "error", "happy"}:
        p.setPen(QPen(QColor("#b75f4b") if state == "error" else QColor("#b78d39"), 2.5))
        if state == "success":
            p.drawLine(QPointF(113,72), QPointF(119,78))
            p.drawLine(QPointF(119,78), QPointF(130,62))
        elif state == "error":
            p.drawLine(QPointF(117,60), QPointF(128,71))
            p.drawLine(QPointF(128,60), QPointF(117,71))
        elif state == "happy":
            p.drawLine(QPointF(122,55), QPointF(122,69))
            p.drawLine(QPointF(115,62), QPointF(129,62))
        else: p.drawText(QRectF(113,50,24,35), Qt.AlignmentFlag.AlignCenter, "!")
    elif state == "sleep":
        p.setPen(accent)
        p.drawText(QRectF(111,47,29,28), Qt.AlignmentFlag.AlignCenter, "zZ")
    p.restore()
