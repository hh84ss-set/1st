"""라운드 폴더 → round.json (날짜, 골프장, 날씨, 자막 문구, 미디어 목록).

  python -m golf_shorts.round ./2026-10-25            # 폴더 안에 round.json 생성
  python -m golf_shorts.round ./2026-10-25 --course "○○CC"   # 골프장 이름 직접 지정
  python -m golf_shorts.round ./2026-10-25 --offline  # API 호출 없이 메타데이터만

KAKAO_REST_API_KEY 는 환경변수나 저장소 루트의 .env 파일(KAKAO_REST_API_KEY=...)에 둔다.
이후 단계(영상 합성, 업로드, 장부)는 모두 이 round.json 을 읽는다.
"""
import argparse
import json
import os
import statistics
import sys
from collections import Counter
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

from .enrich import Place, date_label, fetch_weather, find_course
from .media import read_media, scan

ENV_FILE = Path(__file__).parents[1] / ".env"


def load_env(path=ENV_FILE):
    """KEY=VALUE 형식 .env 를 읽어 아직 설정되지 않은 환경변수만 채운다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_round(items):
    """MediaItem 목록 → 라운드 기본 정보(날짜, 시간대, 대표 좌표)와 경고.

    여러 날짜가 섞여 있으면 파일이 가장 많은 날을 라운드 날짜로 보고, 나머지는 경고로 알린다.
    """
    warnings = []
    no_meta = [i for i in items if i.time_source == "file_mtime"]
    for i in no_meta:
        warnings.append(f"촬영시각 없음(파일 수정시각 사용): {Path(i.path).name}")
    dated = [i for i in items if i.time_source != "file_mtime"] or items
    day = Counter(i.taken_dt.date() for i in dated).most_common(1)[0][0]
    for i in items:
        if i.taken_dt.date() != day and i not in no_meta:
            warnings.append(f"다른 날짜({i.taken_dt:%Y-%m-%d}) 파일: {Path(i.path).name}")

    same_day = sorted((i for i in dated if i.taken_dt.date() == day), key=lambda i: i.taken_dt)
    start, end = same_day[0].taken_dt, same_day[-1].taken_dt
    if same_day[-1].duration:
        end += timedelta(seconds=same_day[-1].duration)

    located = [i for i in same_day if i.lat is not None]
    center = None
    if located:
        center = (round(statistics.median(i.lat for i in located), 6),
                  round(statistics.median(i.lng for i in located), 6))
    else:
        warnings.append("GPS 정보가 있는 파일이 없습니다. --course 로 골프장을 지정하세요. "
                        "(아이폰 공유 시 '옵션 → 위치 포함'을 켜거나 원본 파일을 옮겨 주세요)")
    missing_gps = len(same_day) - len(located)
    if located and missing_gps:
        warnings.append(f"GPS 없는 파일 {missing_gps}개 (위치는 나머지 파일로 계산)")
    return day, start, end, center, warnings


def make_caption(day, place, weather):
    parts = [date_label(day)]
    if place:
        parts.append(place.name)
    if weather:
        parts.append(weather.caption)
    return " | ".join(parts)


def run(folder, course=None, offline=False, log=print):
    paths = scan(folder)
    if not paths:
        raise SystemExit(f"사진·영상 파일이 없습니다: {folder}")
    items = []
    for p in paths:
        try:
            items.append(read_media(p))
        except Exception as e:
            log(f"  ! 읽기 실패 {p.name}: {e}")
    if not items:
        raise SystemExit("읽을 수 있는 파일이 없습니다.")

    day, start, end, center, warnings = build_round(items)

    place = Place(name=course, source="manual", lat=center and center[0], lng=center and center[1]) \
        if course else None
    weather = None
    if not offline and center:
        key = os.environ.get("KAKAO_REST_API_KEY")
        if not place and key:
            try:
                place = find_course(*center, key)
            except Exception as e:
                warnings.append(f"골프장 조회 실패: {e}")
        elif not place:
            warnings.append("KAKAO_REST_API_KEY 가 없어 골프장 이름을 찾지 않았습니다.")
        try:
            weather = fetch_weather(*center, start, end)
        except Exception as e:
            warnings.append(f"날씨 조회 실패: {e}")
        if not weather and not any(w.startswith("날씨") for w in warnings):
            warnings.append("해당 날짜의 날씨 데이터를 찾지 못했습니다.")

    return {
        "date": day.isoformat(),
        "date_label": date_label(day),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "location": {"lat": center[0], "lng": center[1]} if center else None,
        "course": asdict(place) if place else None,
        "weather": {**asdict(weather), "caption": weather.caption} if weather else None,
        "caption": make_caption(day, place, weather),
        "media": [{**asdict(i), "path": str(Path(i.path).relative_to(folder)), "portrait": i.portrait}
                  for i in sorted(items, key=lambda i: i.taken_at)],
        "warnings": warnings,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="라운드 폴더의 사진·영상에서 날짜·장소·날씨를 추출")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--course", help="골프장 이름 직접 지정 (자동 검색 생략)")
    ap.add_argument("--offline", action="store_true", help="골프장·날씨 API 를 호출하지 않음")
    ap.add_argument("--out", type=Path, help="결과 파일 (기본: <folder>/round.json)")
    args = ap.parse_args(argv)

    load_env()
    result = run(args.folder, course=args.course, offline=args.offline)
    out = args.out or args.folder / "round.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    photos = sum(m["kind"] == "photo" for m in result["media"])
    print(f"⛳ {result['caption']}")
    print(f"   사진 {photos}개 · 영상 {len(result['media']) - photos}개 · "
          f"{result['start'][11:16]}~{result['end'][11:16]}")
    for w in result["warnings"]:
        print(f"   ⚠️ {w}")
    print(f"   → {out}")


if __name__ == "__main__":
    sys.exit(main())
