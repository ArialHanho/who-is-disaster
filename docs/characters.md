# 캐릭터 만들기

기본 캐릭터를 그대로 써도 됩니다. 직접 만들 때는 본인이 제작했거나 사용 권한이 있는 그림을 준비하세요. 개인 사진·인물 팩은 앱에 포함되지 않습니다.

## 가장 쉬운 방법: 동작별 GIF 또는 WebP

투명 배경으로 세 동작을 만듭니다. 모든 동작의 캔버스 크기, 캐릭터 비율, 발 위치를 맞추면 전환할 때 덜 튑니다.

```text
my-character/
  character.json
  idle.webp
  thinking.webp
  attention.webp
  happy.webp
```

```json
{
  "name": "내 비서",
  "animations": {
    "idle": {"file": "idle.webp"},
    "thinking": {"file": "thinking.webp"},
    "attention": {"file": "attention.webp"},
    "happy": {"file": "happy.webp", "loop": false}
  }
}
```

**캐릭터 우클릭 → 캐릭터 바꾸기 → 애니메이션 팩 가져오기**에서 `character.json`을 선택합니다. 이름·크기를 조절하고 미리보기 후 적용하세요. 파일은 로컬 사용자 데이터 폴더로 복사되므로 원본 폴더는 따로 보관할 수 있습니다.

필수 동작은 `idle`, `thinking`, `attention`입니다. 선택 동작은 `searching`, `organizing`, `greeting`, `happy`, `success`, `error`, `dragging`, `sleep`입니다. 빠진 선택 동작은 대기 이미지로 보완합니다.

## PNG 프레임과 시트

GIF 대신 프레임 목록을 쓸 수 있습니다.

```json
{"frames": ["idle-001.png", "idle-002.png", "idle-003.png"], "fps": 12, "loop": true}
```

또는 가로로 같은 크기의 프레임이 놓인 시트를 사용합니다.

```json
{"sheet": "atlas.png", "frame_width": 128, "frame_height": 128, "row": 0, "count": 8, "fps": 12, "loop": true}
```

동작별 PNG 목록·시트는 2~120프레임, GIF·애니메이션 WebP는 2~240프레임입니다. PNG 재생 속도는 1~30fps입니다. 60개 프레임을 30fps로 재생하면 2초 동작이 됩니다.

이미지 한 변은 4096px 이하, 파일당 20MB 이하, 팩 전체는 256MB 이하입니다. 서로 다른 이미지의 총 픽셀 수는 1억 2천만 이하입니다. 큰 시트는 여러 PNG로 나눠 주세요. 고해상도 제작 원본은 별도로 보관하고 표시용 크기로 내보내는 편이 낫습니다.

## 전신 자세 팩

서 있는 자세, 만세, 생각하기처럼 완성된 전신 그림을 동작마다 연결할 수도 있습니다.

```json
{
  "name": "내 전신 비서",
  "layout": "portrait",
  "motion": "poses",
  "animations": {
    "idle": {"frames": ["idle.png", "idle.png"], "display_scale": 1},
    "thinking": {"frames": ["thinking.png", "thinking.png"], "display_scale": 1},
    "attention": {"frames": ["attention.png", "attention.png"], "display_scale": 1},
    "happy": {"frames": ["happy.png", "happy.png"], "display_scale": 1}
  }
}
```

이 모드는 각 동작의 첫 그림을 유지하고 자세 전환과 작은 움직임을 앱이 처리합니다. 같은 파일을 두 번 적은 것은 프레임 목록 형식에 맞춘 것입니다. `display_scale`은 0.25~1.5 범위이며, 같은 캔버스에서 누운 자세 등 눈에 보이는 크기를 맞출 때만 사용하세요.

## 얼굴·옷을 유지하려면

1. 먼저 기준 캐릭터와 의상을 완성합니다. 동작마다 별도로 다시 생성하면 얼굴·옷이 달라질 수 있습니다.
2. 동일한 2D 리그나 3D 모델에서 자세를 바꾸고 같은 카메라·조명·비율로 출력하면 일관성을 관리하기 쉽습니다.
3. 사진 누끼는 원본 RGB를 다시 그리지 않고 알파 마스크만 적용하는 방식으로 만듭니다.
4. 눈·입·옷 경계는 실제 앱 크기에서도 확인합니다. 단순 확대나 업스케일로 없던 디테일이 복원되는 것은 아닙니다.
5. 앱은 이동 반응에서 원본을 늘이거나 얼굴을 재생성하지 않습니다. 다만 서로 다른 자세 이미지 자체의 불일치는 앱이 고쳐주지 않습니다.

대기·이동·쓰다듬기·점프·잠자기 미리보기를 보고, 어색한 동작은 사용하지 말고 원본 자세로 바꿔 주세요. 가져온 팩은 개인 데이터이며 소스 저장소에 자동 업로드되지 않습니다.

실제 드래그는 미리보기의 `dragging` 자세를 사용합니다. 해당 자세가 없는 팩은 `idle`로 돌아갑니다. 이동·놀람·버둥거림·착지는 선택된 이미지 전체를 부드럽게 이동시키며 얼굴이나 옷을 새로 그리거나 늘리지 않습니다. 중요한 코드 알림이 떠 있어도 동작 반응은 유지합니다.
