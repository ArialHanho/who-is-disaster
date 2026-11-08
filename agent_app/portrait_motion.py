"""Continuous, restrained motion for photographic character packs."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class PortraitPose:
    angle: float = 0
    lift: float = 0
    stretch: float = 1
    shift: float = 0


class PhotoGestureMotion:
    """Translate one unwarped photo; never rotate, stretch or replace its face."""
    DURATIONS = {'lift': 0, 'rest': 0, 'startle': .7, 'wriggle': 1.8, 'land': .85, 'pet': .65}

    def __init__(self):
        self.kind, self.age, self.active = None, 0, False
        self.elapsed = 0
        self.x = self.y = self.vx = self.vy = 0

    def trigger(self, kind):
        self.kind, self.age, self.active = kind, 0, True

    @staticmethod
    def spring(value, velocity, target, dt):
        decay = math.exp(-22*dt)
        offset = value-target
        change = (velocity+22*offset)*dt
        return target+(offset+change)*decay, (velocity-22*change)*decay

    def step(self, dt, *, held=False, moving=False, drag=0, reduced=False):
        if reduced:
            self.__init__()
            return PortraitPose()
        dt = max(0, min(dt, .05))
        self.age += dt
        self.elapsed += dt
        duration = self.DURATIONS.get(self.kind, 0)
        if self.kind == 'wriggle' and held and self.age >= duration:
            self.age %= duration
        x, y = 0, -2 if held else 0
        if held and moving:
            phase = self.elapsed * math.tau / 1.15
            x = -max(-3, min(3, drag)) + 2.4*math.sin(phase)
            y = -4 - 1.4*(1-math.cos(phase))
        if duration and self.age < duration:
            u = self.age/duration
            envelope = math.sin(math.pi*u)**2
            if self.kind == 'startle':
                x, y = x+2.5*math.sin(math.tau*u)*envelope, y-8*envelope
            elif self.kind == 'wriggle':
                x += 5*math.sin(2*math.tau*u)*envelope
                y -= 1.8*(1-math.cos(2*math.tau*u))*envelope
            elif self.kind == 'pet':
                y -= 3.5*envelope
            elif self.kind == 'land':
                points = ((0, 0), (.12, 2), (.34, -3.5), (.60, .9), (.85, 0))
                for (a, ya), (b, yb) in zip(points, points[1:]):
                    if a <= self.age <= b:
                        t = (self.age-a)/(b-a)
                        y = ya+(yb-ya)*t*t*(3-2*t)
                        break
        else:
            self.kind = None
        self.x, self.vx = self.spring(self.x, self.vx, x, dt)
        self.y, self.vy = self.spring(self.y, self.vy, y, dt)
        if not held and self.kind is None and max(abs(v) for v in (self.x, self.y, self.vx, self.vy)) < .02:
            self.x = self.y = self.vx = self.vy = 0
            self.active = False
        return PortraitPose(lift=self.y, shift=self.x)


class PortraitMotion:
    """Ease targets without changing the face, outfit, or underlying source image."""
    def __init__(self):
        self.elapsed = self.age = 0
        self.state = 'idle'
        self.angle = self.lift = self.compression = 0

    def step(self, dt, state, *, pressed=False, look=0, drag=0, expressive=False):
        dt = max(0, min(dt, .05))
        self.elapsed += dt
        if state != self.state:
            self.state, self.age = state, 0
        self.age += dt
        # One soft response, never a repeating high-frequency shake or jump.
        pulse = math.sin(math.pi * min(1, self.age / 1.8)) ** 2
        angle, lift, compression = 0, 0, 0
        if state == 'searching': angle = .55 * math.sin(self.elapsed * math.tau / 6)
        elif state == 'thinking': angle = -.65
        elif state == 'organizing': angle = .4
        elif state == 'greeting': angle, compression = .6 * pulse, .005 * pulse
        elif state in {'happy', 'success'}: angle, lift = -.7 * pulse, -1.4 * pulse
        elif state in {'attention', 'error'}: angle, lift = .4 * pulse, -.8 * pulse
        elif state == 'dragging': angle, lift = max(-2, min(2, -drag)), -1
        if expressive and state in {'happy', 'success'}:
            hop = math.sin(math.pi * min(1, max(0, (self.age-.12)/1.05)))
            angle, lift, compression = -.9*hop, -13*hop, -.003*hop
        if not expressive or state != 'idle':
            angle += max(-.65, min(.65, look * .22))
        compression += .004 if pressed else 0
        ease = 1 - math.exp(-dt / .18)
        self.angle += (angle - self.angle) * ease
        self.lift += (lift - self.lift) * ease
        self.compression += (compression - self.compression) * ease
        if abs(self.angle) < .005: self.angle = 0
        if abs(self.lift) < .005: self.lift = 0
        if abs(self.compression) < .00005: self.compression = 0
        # Idle face stays on stable pixels; touch, jumps and other poses still move.
        breath = 0 if expressive and state == 'idle' else .0012 * math.sin(self.elapsed * math.tau / 5.2)
        return PortraitPose(self.angle, self.lift, 1 + breath - self.compression)


def character_geometry(layout, scale, poses=False):
    """Portrait size changes the figure while preserving readable labels."""
    if layout == 'portrait':
        height = round(48 + 272 * scale / 100)
        return height, max(142, round(168*scale/100)) if poses else 142, height
    return 164, round(142 * scale / 100), round(164 * scale / 100)
