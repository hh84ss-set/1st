"""사진·영상 메타데이터 추출: 촬영시각, GPS, 크기·회전, 영상 길이.

사진은 Pillow(+pillow-heif 로 아이폰 HEIC)로 EXIF 를, 영상은 ffprobe 로 QuickTime 태그를 읽는다.
아이폰 영상은 com.apple.quicktime.creationdate(현지시각+오프셋)와
com.apple.quicktime.location.ISO6709(+37.1234+127.1234+050.000/)에 시각과 위치를 담는다.
"""
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png"}
VIDEO_EXT = {".mov", ".mp4", ".m4v"}

# EXIF 태그 번호
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
DATETIME, ORIENTATION = 0x0132, 0x0112
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
GPS_LAT_REF, GPS_LAT, GPS_LNG_REF, GPS_LNG = 1, 2, 3, 4

_heif_registered = False


@dataclass
class MediaItem:
    path: str
    kind: str                      # "photo" | "video"
    taken_at: str | None = None    # ISO 8601, KST
    time_source: str = ""          # "exif" | "quicktime" | "file_mtime"
    lat: float | None = None
    lng: float | None = None
    width: int | None = None
    height: int | None = None
    rotation: int = 0              # 화면에 보일 때 시계방향 회전 각도
    duration: float | None = None  # 초 (영상만)

    @property
    def taken_dt(self):
        return datetime.fromisoformat(self.taken_at) if self.taken_at else None

    @property
    def portrait(self):
        if not self.width or not self.height:
            return None
        w, h = (self.height, self.width) if self.rotation % 180 else (self.width, self.height)
        return h > w


def media_kind(path):
    ext = Path(path).suffix.lower()
    return "photo" if ext in PHOTO_EXT else "video" if ext in VIDEO_EXT else None


def scan(folder):
    """폴더(하위 포함)의 사진·영상 경로를 이름순으로. macOS 의 ._ 파일 등 숨김 파일은 건너뛴다."""
    return sorted(p for p in Path(folder).rglob("*")
                  if p.is_file() and not p.name.startswith(".") and media_kind(p))


def to_kst(dt, default_tz=KST):
    return (dt if dt.tzinfo else dt.replace(tzinfo=default_tz)).astimezone(KST)


# ---------------------------------------------------------------- 사진 (EXIF)

def _register_heif():
    global _heif_registered
    if not _heif_registered:
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        _heif_registered = True


def _rational(v):
    return float(v[0]) / float(v[1]) if isinstance(v, tuple) else float(v)


def gps_to_decimal(dms, ref):
    """EXIF GPS (도, 분, 초) + 'N'/'S'/'E'/'W' → 십진 도."""
    d, m, s = (_rational(x) for x in dms)
    value = d + m / 60 + s / 3600
    return -value if str(ref).upper() in ("S", "W") else value


def exif_datetime(value, offset=None):
    """'2026:10:25 08:12:33' (+ '+09:00') → KST datetime. 오프셋이 없으면 KST 로 간주."""
    if not value:
        return None
    try:
        dt = datetime.strptime(str(value).strip("\x00 ")[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    tz = KST
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", str(offset or "").strip("\x00 "))
    if m:
        sign = -1 if m.group(1) == "-" else 1
        tz = timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))
    return to_kst(dt.replace(tzinfo=tz))


# EXIF Orientation → 시계방향 회전 각도 (좌우반전 값은 회전만 반영)
_ORIENTATION_ROTATION = {3: 180, 4: 180, 5: 90, 6: 90, 7: 270, 8: 270}


def read_photo(path):
    _register_heif()
    from PIL import Image

    item = MediaItem(str(path), "photo")
    with Image.open(path) as img:
        item.width, item.height = img.size
        exif = img.getexif()
    item.rotation = _ORIENTATION_ROTATION.get(exif.get(ORIENTATION), 0)

    sub = exif.get_ifd(EXIF_IFD)
    dt = exif_datetime(sub.get(DATETIME_ORIGINAL), sub.get(OFFSET_TIME_ORIGINAL)) \
        or exif_datetime(exif.get(DATETIME))
    if dt:
        item.taken_at, item.time_source = dt.isoformat(), "exif"

    gps = exif.get_ifd(GPS_IFD)
    if gps.get(GPS_LAT) and gps.get(GPS_LNG):
        try:
            item.lat = round(gps_to_decimal(gps[GPS_LAT], gps.get(GPS_LAT_REF, "N")), 6)
            item.lng = round(gps_to_decimal(gps[GPS_LNG], gps.get(GPS_LNG_REF, "E")), 6)
        except (TypeError, ValueError, ZeroDivisionError, IndexError):
            item.lat = item.lng = None
    return item


# ---------------------------------------------------------------- 영상 (ffprobe)

def parse_iso6709(text):
    """'+37.5665+126.9780+038.000/' → (37.5665, 126.978). 형식이 다르면 None."""
    m = re.match(r"\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", text or "")
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return round(lat, 6), round(lng, 6)


def parse_iso_datetime(text):
    if not text:
        return None
    try:
        return to_kst(datetime.fromisoformat(text.strip()), default_tz=timezone.utc)
    except ValueError:
        return None


def video_from_probe(path, probe):
    """ffprobe -show_format -show_streams JSON → MediaItem."""
    item = MediaItem(str(path), "video")
    fmt = probe.get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}

    # 아이폰: creationdate 가 현지시각+오프셋. creation_time 은 UTC 라 보조로만 쓴다.
    dt = parse_iso_datetime(tags.get("com.apple.quicktime.creationdate")) \
        or parse_iso_datetime(tags.get("creation_time"))
    if dt:
        item.taken_at, item.time_source = dt.isoformat(), "quicktime"

    loc = parse_iso6709(tags.get("com.apple.quicktime.location.iso6709") or tags.get("location"))
    if loc:
        item.lat, item.lng = loc

    if fmt.get("duration"):
        item.duration = round(float(fmt["duration"]), 3)

    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    if video:
        item.width, item.height = video.get("width"), video.get("height")
        # 최신 ffprobe: displaymatrix 의 rotation(반시계 기준, -90 = 시계방향 90도)
        # 옛 ffprobe: 스트림 태그 rotate(시계방향 기준)
        matrix = [sd["rotation"] for sd in video.get("side_data_list") or [] if "rotation" in sd]
        if matrix:
            item.rotation = int(-float(matrix[0])) % 360
        elif (video.get("tags") or {}).get("rotate"):
            item.rotation = int(float(video["tags"]["rotate"])) % 360
    return item


def read_video(path):
    if not shutil.which("ffprobe"):
        raise RuntimeError("ffprobe 를 찾을 수 없습니다. FFmpeg 를 설치하고 PATH 에 추가하세요.")
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, check=True)
    return video_from_probe(path, json.loads(out.stdout or b"{}"))


# ---------------------------------------------------------------- 공통

def read_media(path):
    """파일 하나의 메타데이터. 촬영시각이 없으면 파일 수정시각으로 대신하고 time_source 로 구분한다."""
    kind = media_kind(path)
    item = read_photo(path) if kind == "photo" else read_video(path)
    if not item.taken_at:
        mtime = datetime.fromtimestamp(Path(path).stat().st_mtime, tz=timezone.utc)
        item.taken_at, item.time_source = to_kst(mtime).isoformat(), "file_mtime"
    return item
