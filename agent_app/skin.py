"""Optional, action-specific GIF clips or PNG frame sequences."""
import json
from bisect import bisect_right
from itertools import accumulate
from pathlib import Path
from PySide6.QtGui import QMovie, QPixmap


class AnimationSet:
    def __init__(self, root, repaint, manifest=None, motion=True):
        self.root, self.repaint = root.resolve(), repaint
        self.animations, self.cache, self.active = {}, {}, None
        self.layout = "default"
        self.continuous_motion = False
        self.pose_motion = False
        self.poses, self.pose_scales = {}, {}
        self._pose_action = self._pose_target = self._pose_previous = None
        self._pose_action_start = self._pose_mix_start = 0
        self.motion = motion
        path = manifest or root / "character.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                animations = data.get("animations", {})
                if not isinstance(animations, dict):
                    raise ValueError("Invalid character animations")
                self.animations = animations
                self.layout = "portrait" if data.get("layout") == "portrait" else "default"
                self.pose_motion = self.layout == "portrait" and data.get("motion") == "poses"
                self.continuous_motion = self.layout == "portrait" and data.get("motion") in {"portrait", "poses"}
            except (OSError, ValueError, AttributeError):
                self.animations = {}

    def safe_path(self, value):
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("캐릭터 파일은 앱의 assets 폴더에 넣어 주세요.")
        return path

    def frame(self, state, ticks):
        if self.pose_motion:
            return self.poses.get(state, self.poses.get("idle"))
        if self.continuous_motion:
            state, ticks = "idle", 0
        state = state if state in self.animations else "idle"
        spec = self.animations.get(state)
        if not spec:
            previous = self.cache.get(self.active)
            if isinstance(previous, QMovie):
                previous.stop()
            self.active = None
            return None
        if self.active != state:
            previous = self.cache.get(self.active)
            if isinstance(previous, QMovie): previous.stop()
            elif isinstance(previous, list): self.cache.pop(self.active, None)
        if state not in self.cache:
            if "file" in spec:
                movie = QMovie(str(self.safe_path(spec["file"])))
                movie.frameChanged.connect(lambda _: self.repaint())
                self.cache[state] = movie
            elif "sheet" in spec:
                sheet = QPixmap(str(self.safe_path(spec["sheet"])))
                width, height = int(spec["frame_width"]), int(spec["frame_height"])
                count, row = min(120, max(1, int(spec.get("count", 1)))), int(spec.get("row", 0))
                self.cache[state] = [sheet.copy(i * width, row * height, width, height) for i in range(count)]
            else:
                sheets, frames = {}, []
                entries = spec.get("frames", [])
                for entry in entries[:1] if self.continuous_motion else entries:
                    if isinstance(entry, str):
                        frames.append(QPixmap(str(self.safe_path(entry))))
                    else:
                        path = str(self.safe_path(entry["file"]))
                        if path not in sheets: sheets[path] = QPixmap(path)
                        frames.append(sheets[path].copy(*entry["rect"]))
                self.cache[state] = frames
        clip = self.cache[state]
        if isinstance(clip, QMovie):
            if self.active != state:
                previous = self.cache.get(self.active)
                if isinstance(previous, QMovie):
                    previous.stop()
                clip.start()
                if not self.motion:
                    clip.jumpToFrame(0)
                    clip.setPaused(True)
                self.active = state
            return clip.currentPixmap() if clip.isValid() else None
        if clip:
            self.active = state
            durations = spec.get("durations_ms")
            if self.motion and durations:
                ends = list(accumulate(durations))
                elapsed = max(0, ticks * 100)
                elapsed = elapsed % ends[-1] if spec.get("loop", True) else min(elapsed, ends[-1]-1)
                index = bisect_right(ends, elapsed)
            else:
                index = int(ticks * max(1, min(30, int(spec.get("fps", 8)))) / 10) if self.motion else 0
                index = index % len(clip) if spec.get("loop", True) else min(index, len(clip) - 1)
            return clip[index]
        return None

    def prepare_poses(self):
        sheets, sources = {}, {}
        for state, spec in self.animations.items():
            entry = spec["frames"][0]
            file, rect = (entry, None) if isinstance(entry, str) else (entry["file"], tuple(entry["rect"]))
            key = (file, rect)
            if key not in sources:
                if file not in sheets:
                    sheets[file] = QPixmap(str(self.safe_path(file)))
                image = sheets[file]
                sources[key] = image.copy(*rect) if rect else image
            if not sources[key].isNull():
                self.poses[state] = sources[key]
                self.pose_scales[state] = spec.get("display_scale", 1)

    def layers(self, state, ticks):
        """Blend only on pose changes; pose holds never cycle different clothes."""
        if not self.pose_motion or "idle" not in self.poses: return None
        state = state if state in self.poses else "idle"
        if state != self._pose_action:
            self._pose_action, self._pose_action_start = state, ticks
        age = max(0, (ticks-self._pose_action_start)/10)
        target = "idle" if state in {"happy", "success"} and age >= 1.3 else state
        if target != self._pose_target:
            self._pose_previous, self._pose_target = self._pose_target, target
            self._pose_mix_start = ticks
        blend = min(1, max(0, (ticks-self._pose_mix_start)/2.4)) if self.motion else 1
        blend = blend*blend*(3-2*blend)
        def layer(key, opacity): return (self.poses[key], opacity, self.pose_scales[key])
        if self._pose_previous is None or blend >= 1:
            if self.motion and state == "idle" and "blink" in self.poses and 3.8 <= age % 4.7 < 3.96:
                return [layer("blink", 1)]
            return [layer(target, 1)]
        return [layer(self._pose_previous, 1-blend), layer(target, blend)]

    def restart_pose(self):
        self._pose_action = None

    def reset_pose_to_idle(self):
        """Resume from the original photograph without blending in a stale pose."""
        self._pose_action = self._pose_target = 'idle'
        self._pose_previous = None

    def pause_movie(self, paused):
        current = self.cache.get(self.active)
        if self.motion and isinstance(current, QMovie) and current.state() != QMovie.MovieState.NotRunning:
            current.setPaused(paused)

    def close(self):
        for clip in self.cache.values():
            if isinstance(clip, QMovie):
                clip.stop()
                clip.deleteLater()
        self.cache.clear()
        self.poses.clear()
        self.pose_scales.clear()


def selected_skin(root, repaint, preferences):
    from .character_settings import pack_path
    key = preferences["character"]
    path = (Path(__file__).resolve().parents[1] / "assets/character/ember-fox.json"
            if key == "ember_fox" else pack_path(root, key))
    base = path.parent if path else root
    skin = AnimationSet(base, repaint, path, motion=not preferences["reduced_motion"])
    if key == "ember_fox" and skin.animations:
        skin.pose_motion = skin.continuous_motion = True
    elif key != "legacy" and not path:
        skin.animations = {}
    try:
        if skin.pose_motion:
            skin.prepare_poses()
            if "idle" not in skin.poses:
                raise ValueError("Missing idle pose")
        elif skin.continuous_motion:
            skin.frame("idle", 0)  # Decode once, before the first mouse interaction.
        elif path and not skin.animations:
            raise ValueError("Missing animations")
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        skin.close()
        if key != "ember_fox":
            return selected_skin(root, repaint, {**preferences, "character": "ember_fox"})
        skin.animations = {}
        skin.pose_motion = skin.continuous_motion = False
    return skin
