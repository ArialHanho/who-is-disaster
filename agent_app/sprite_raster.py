"""Area-filtered sprite sizes for the actual display pixels, with a bounded cache."""
from collections import OrderedDict
import math
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPixmap

_cache = OrderedDict()
_pixels = 0
_MAX_PIXELS = 4_000_000
_tops = OrderedDict()


def subject_top(source):
    """Visible silhouette top as a fraction of the image height; ignore faint fringe."""
    key = source.cacheKey()
    if key not in _tops:
        from PySide6.QtGui import QImage
        image = source.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        alpha = bytes(image.bits())[3::4]
        visible = alpha.translate(bytes(1 if v >= 128 else 0 for v in range(256)))
        first = visible.find(b'\x01')
        _tops[key] = (first//image.width())/image.height() if first >= 0 else 0
        if len(_tops) > 64: _tops.popitem(last=False)
    return _tops[key]


def visible_subject_y(custom, layers, height, stage_width, lift=0):
    """Visible figure top in drawing coordinates, ignoring transparent padding."""
    if custom is None or custom.isNull(): return 40+lift
    fitted = custom.size().scaled(stage_width-10,height-48,Qt.AspectRatioMode.KeepAspectRatio)
    bottom = 36+(height-48-fitted.height())/2+fitted.height()
    return lift+sum((bottom-fitted.height()*scale*(1-subject_top(source)))*opacity
                    for source,opacity,scale in (layers or [(custom,1,1)]))


def nameplate_y(custom, layers, height, stage_width, lift=0, badge_height=30):
    """Place the badge ten logical pixels above the visible pose, not its empty canvas."""
    if not layers or custom is None or custom.isNull(): return 2
    return max(2,visible_subject_y(custom,layers,height,stage_width,lift)-badge_height-10)


def raster_for(source, width, height):
    """Keep original art intact; downsample once at the physical drawing resolution."""
    global _pixels
    width, height = max(1, round(width)), max(1, round(height))
    key = (source.cacheKey(), width, height)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    image = source.toImage().scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    result = QPixmap.fromImage(image)
    pixels = width*height
    if pixels <= _MAX_PIXELS:
        while _cache and _pixels+pixels > _MAX_PIXELS:
            _, old = _cache.popitem(last=False)
            _pixels -= old.width()*old.height()
        _cache[key] = result
        _pixels += pixels
    return result


def draw_sprite(painter, target, source):
    transform = painter.transform()
    dpr = painter.device().devicePixelRatioF()
    sx = dpr*math.hypot(transform.m11(),transform.m12())
    sy = dpr*math.hypot(transform.m21(),transform.m22())
    image = raster_for(source,target.width()*sx,target.height()*sy)
    if (abs(transform.m12()) < 1e-8 and abs(transform.m21()) < 1e-8 and
            transform.m11() > 0 and transform.m22() > 0):
        # The cached image is already at physical resolution. A fractional target
        # would filter it again, softening eyes and lips. Blit it pixel-for-pixel.
        x = (round((target.x()*transform.m11()+transform.dx())*dpr)/dpr-transform.dx())/transform.m11()
        y = (round((target.y()*transform.m22()+transform.dy())*dpr)/dpr-transform.dy())/transform.m22()
        aligned = QRectF(x,y,image.width()/sx,image.height()/sy)
        painter.save()
        painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform,False)
        painter.drawPixmap(aligned,image,QRectF(image.rect()))
        painter.restore()
    else:
        painter.drawPixmap(target,image,QRectF(image.rect()))
