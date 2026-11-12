"""Local character preferences and image-only animation pack installation."""
import hashlib
import json
from pathlib import Path
import uuid
from PySide6.QtGui import QImageReader
from .actions import ACTION_NAMES

BUILTINS = {
    "ember_fox": ("불여우 탐정", "#d97745"),
}
DEFAULTS = {"character": "ember_fox", "name": "코드 비서", "scale": 100, "reduced_motion": False}
SCALES = (50, 65, 85, 100, 125, 150)


def load_preferences(root):
    try:
        data = json.loads((Path(root) / ".data/character/settings.json").read_text(encoding="utf-8"))
        value = {**DEFAULTS, **data}
        if value["character"] not in {**BUILTINS, "legacy": None} and not pack_path(root, value["character"]):
            value["character"] = DEFAULTS["character"]
        if value["scale"] not in SCALES: value["scale"] = 100
        value["name"] = str(value["name"]).strip()[:10] or DEFAULTS["name"]
        value["reduced_motion"] = bool(value["reduced_motion"])
        return value
    except (OSError, ValueError, TypeError):
        return {**DEFAULTS, "character": "legacy"} if (Path(root) / "character.json").is_file() else DEFAULTS.copy()


def save_preferences(root, value):
    data = {k: value.get(k, v) for k, v in DEFAULTS.items()}
    if data["character"] not in {**BUILTINS, "legacy": None} and not pack_path(root, data["character"]):
        raise ValueError("사용할 캐릭터를 다시 선택해 주세요.")
    data["name"] = str(data["name"]).strip()[:10] or DEFAULTS["name"]
    data["scale"] = data["scale"] if data["scale"] in SCALES else 100
    data["reduced_motion"] = bool(data["reduced_motion"])
    path = Path(root) / ".data/character/settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)
    return data


def pack_path(root, key):
    if not isinstance(key, str) or not key.startswith("pack:"):
        return None
    ident = key[5:]
    if len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
        return None
    path = Path(root) / ".data/character/packs" / ident / "character.json"
    return path if path.is_file() else None


def packs(root):
    result = []
    for path in sorted((Path(root) / ".data/character/packs").glob("*/character.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append(("pack:" + path.parent.name, str(data.get("name", "내 캐릭터"))[:40]))
        except (OSError, ValueError, TypeError):
            continue
    return result


def install_pack(root, manifest):
    manifest = Path(manifest).resolve()
    if manifest.stat().st_size > 100_000:
        raise ValueError("캐릭터 설정 파일이 너무 커요.")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8-sig"))
        animations = data["animations"]
        if not isinstance(animations, dict) or not {"idle", "thinking", "attention"} <= animations.keys():
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("대기(idle)·분석(thinking)·알림(attention) 동작이 있는 character.json을 골라 주세요.") from None
    assets, normalized, total, pixels = {}, {}, 0, 0
    def asset(value, animated=False):
        nonlocal total, pixels
        if not isinstance(value, str): raise ValueError("캐릭터 파일 경로를 확인해 주세요.")
        path = (manifest.parent / value).resolve()
        if not path.is_relative_to(manifest.parent) or not path.is_file() or path.suffix.lower() not in {".png", ".gif", ".webp"}:
            raise ValueError("팩 폴더 안의 PNG·GIF·WebP 파일만 사용할 수 있어요.")
        if path.stat().st_size > 20_000_000: raise ValueError("캐릭터 파일은 하나당 20MB 이하여야 해요.")
        reader = QImageReader(str(path))
        size = reader.size()
        if not reader.canRead() or not (0 < size.width() <= 4096 and 0 < size.height() <= 4096):
            raise ValueError("읽을 수 없는 이미지이거나 크기가 4096px를 넘어요.")
        if animated and (not reader.supportsAnimation() or not 2 <= reader.imageCount() <= 240):
            raise ValueError("동작은 2~240프레임의 애니메이션이어야 해요. 한 장의 이미지는 프레임 목록이나 시트로 묶어 주세요.")
        if path not in assets:
            total += path.stat().st_size
            pixels += size.width()*size.height()
            if total > 256_000_000: raise ValueError("캐릭터 팩은 전체 256MB 이하여야 해요.")
            if pixels > 120_000_000: raise ValueError("이미지의 전체 해상도가 너무 커요. 캐릭터 프레임 크기를 줄여 주세요.")
            assets[path] = hashlib.sha256(str(path).encode()).hexdigest()[:20] + path.suffix.lower()
        return assets[path], size
    for state, spec in animations.items():
        if state not in ACTION_NAMES: continue
        if not isinstance(spec, dict): raise ValueError("동작별 설정 형식을 확인해 주세요.")
        clip = {"fps": max(1, min(30, int(spec.get("fps", 8)))), "loop": bool(spec.get("loop", True))}
        if "file" in spec:
            clip["file"] = asset(spec["file"], animated=True)[0]
        elif "sheet" in spec:
            name, size = asset(spec["sheet"])
            w, h, count, row = (int(spec.get(k, 0)) for k in ("frame_width", "frame_height", "count", "row"))
            if not (w > 0 and h > 0 and 2 <= count <= 120 and row >= 0 and w*count <= size.width() and h*(row+1) <= size.height()):
                raise ValueError("스프라이트 시트의 프레임 크기·행·개수를 확인해 주세요.")
            clip.update(sheet=name, frame_width=w, frame_height=h, count=count, row=row)
        else:
            frames = spec.get("frames", [])
            if not isinstance(frames, list) or not 2 <= len(frames) <= 120: raise ValueError("각 동작에 2~120개의 프레임이 필요해요.")
            clip["frames"] = []
            for entry in frames:
                if isinstance(entry, str):
                    clip["frames"].append(asset(entry)[0])
                    continue
                if not isinstance(entry, dict) or set(entry) != {"file", "rect"}:
                    raise ValueError("프레임은 이미지 경로나 file·rect 설정이어야 해요.")
                name, size = asset(entry["file"])
                rect = entry["rect"]
                if not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect):
                    raise ValueError("프레임 영역은 정수 x·y·너비·높이로 지정해 주세요.")
                x, y, w, h = rect
                if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > size.width() or y+h > size.height():
                    raise ValueError("프레임 영역이 원본 이미지 밖으로 나가요.")
                clip["frames"].append({"file": name, "rect": rect})
        if "durations_ms" in spec:
            durations = spec["durations_ms"]
            count = len(clip["frames"]) if "frames" in clip else clip.get("count", 0)
            if (not isinstance(durations, list) or len(durations) != count or not count or
                    any(type(v) is not int or not 10 <= v <= 60_000 for v in durations) or sum(durations) > 120_000):
                raise ValueError("프레임별 시간은 프레임 수와 같은 10~60000ms 정수 목록이어야 해요.")
            clip["durations_ms"] = durations
        if data.get("motion") == "poses":
            if "frames" not in clip:
                raise ValueError("자세 팩은 PNG·WebP 프레임 목록을 사용해 주세요.")
            scale = spec.get("display_scale", 1)
            if type(scale) not in (float, int) or not .25 <= scale <= 1.5:
                raise ValueError("자세 배율은 0.25~1.5로 지정해 주세요.")
            clip["display_scale"] = scale
        normalized[state] = clip
    ident = uuid.uuid4().hex
    target = Path(root) / ".data/character/packs" / ident
    target.mkdir(parents=True)
    for path, name in assets.items():
        (target / name).write_bytes(path.read_bytes())
    layout = "portrait" if data.get("layout") == "portrait" else "default"
    installed = {"name": str(data.get("name", manifest.parent.name))[:40], "layout": layout, "animations": normalized}
    if layout == "portrait" and data.get("motion") in {"portrait", "poses"}: installed["motion"] = data["motion"]
    (target / "character.json").write_text(json.dumps(installed, ensure_ascii=False), encoding="utf-8")
    return "pack:" + ident
