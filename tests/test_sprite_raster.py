import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from PySide6.QtGui import QImage,QColor,QPixmap
from PySide6.QtWidgets import QApplication
from agent_app.sprite_raster import raster_for, nameplate_y, subject_top
from agent_app.portrait_motion import PortraitMotion, PortraitPose, PhotoGestureMotion


class SpriteRasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_downsampling_keeps_fine_detail_average_and_reuses_physical_size(self):
        image=QImage(64,64,QImage.Format.Format_ARGB32)
        for y in range(64):
            for x in range(64):image.setPixelColor(x,y,QColor('white' if (x+y)%2 else 'black'))
        source=QPixmap.fromImage(image)
        small=raster_for(source,8,8)
        color=small.toImage().pixelColor(4,4)
        self.assertTrue(126 <= color.red() <= 129)
        self.assertEqual(small.cacheKey(),raster_for(source,8,8).cacheKey())
        self.assertEqual(raster_for(source,16,16).width(),16)

    def test_idle_settles_to_stable_pixels_while_jump_still_moves(self):
        motion=PortraitMotion()
        jumps=[motion.step(.016,'happy',expressive=True) for _ in range(70)]
        self.assertLess(min(p.lift for p in jumps),-8)
        for _ in range(180):pose=motion.step(.016,'idle',look=3,expressive=True)
        self.assertEqual((pose.angle,pose.lift,pose.stretch),(0,0,1))

    def test_badge_tracks_visible_person_instead_of_transparent_padding(self):
        from PySide6.QtCore import Qt
        image=QImage(100,200,QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.transparent)
        image.setPixelColor(50,2,QColor(0,0,0,1))
        for y in range(60,200):
            for x in range(30,70):image.setPixelColor(x,y,QColor('black'))
        source=QPixmap.fromImage(image)
        self.assertEqual(subject_top(source),.3)
        # Target is 132x264 at y=40; visible top is y=119.2.
        y=nameplate_y(source,[(source,1,1)],320,142)
        self.assertAlmostEqual(119.2-(y+30),10)
        scaled_y=nameplate_y(source,[(source,1,.7)],320,142)
        self.assertGreater(scaled_y,y)

    def test_photo_gestures_settle_without_stretching_or_jumping_between_reactions(self):
        motion=PhotoGestureMotion()
        motion.trigger('lift')
        for _ in range(60):motion.step(1/60,held=True)
        samples={}
        for kind in ('startle','wriggle','land'):
            before=motion.step(0,held=True)
            motion.trigger(kind)
            frames=[before]+[motion.step(1/60,held=kind!='land') for _ in range(180)]
            self.assertTrue(all(p.angle==0 and p.stretch==1 for p in frames))
            self.assertLess(max(abs(a.lift-b.lift)+abs(a.shift-b.shift) for a,b in zip(frames,frames[1:])),2)
            samples[kind]=frames
        self.assertLess(min(p.lift for p in samples['startle']),-6)
        self.assertGreater(max(p.shift for p in samples['wriggle'])-min(p.shift for p in samples['wriggle']),4)
        self.assertGreater(max(p.lift for p in samples['land']),.3)
        self.assertEqual(samples['land'][-1],PortraitPose())
        self.assertFalse(motion.active)
        motion.trigger('wriggle')
        self.assertEqual(motion.step(1/60,held=True,reduced=True),PortraitPose())

    def test_drag_keeps_moving_while_held_without_warping_the_photo(self):
        motion=PhotoGestureMotion()
        frames=[motion.step(1/60,held=True,moving=True,drag=2) for _ in range(180)]
        self.assertGreater(max(p.shift for p in frames[60:])-min(p.shift for p in frames[60:]),3)
        self.assertTrue(all(p.angle==0 and p.stretch==1 for p in frames))
        motion.trigger('wriggle')
        for _ in range(150):motion.step(1/60,held=True)
        self.assertEqual(motion.kind,'wriggle')

    def test_photo_gesture_pixels_match_the_same_raster_at_display_scaling(self):
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QPainter
        from agent_app.sprite_raster import draw_sprite
        source=QImage(256,512,QImage.Format.Format_ARGB32)
        for y in range(source.height()):
            for x in range(source.width()):source.setPixelColor(x,y,QColor(x%256,y%256,(x*13+y*7)%256))
        source=QPixmap.fromImage(source)
        for ratio in (1,1.25,2):
            expected=raster_for(source,80*ratio,160*ratio).toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            motion=PhotoGestureMotion()
            for kind in ('startle','wriggle','land'):
                motion.trigger(kind)
                for _ in range(50):
                    pose=motion.step(1/30,held=kind!='land')
                    canvas=QImage(round(200*ratio),round(250*ratio),QImage.Format.Format_ARGB32)
                    canvas.setDevicePixelRatio(ratio);canvas.fill(Qt.GlobalColor.transparent)
                    painter=QPainter(canvas);painter.translate(pose.shift,pose.lift)
                    draw_sprite(painter,QRectF(40,40,80,160),source);painter.end()
                    crop=canvas.copy(round((40+pose.shift)*ratio),round((40+pose.lift)*ratio),expected.width(),expected.height()).convertToFormat(QImage.Format.Format_RGBA8888)
                    self.assertEqual(bytes(crop.bits()),bytes(expected.bits()))
