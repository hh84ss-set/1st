"""촬영 좌표·시각으로 골프장 이름과 그날 날씨를 붙인다.

- 골프장: Kakao Local 키워드 검색('골프장', 좌표 반경 내 거리순). 환경변수 KAKAO_REST_API_KEY 필요.
  골프장을 못 찾으면 좌표 → 행정구역(예: 경기도 여주시)으로 대신한다.
- 날씨: Open-Meteo (키 불필요). 최근 80일은 forecast API, 그보다 오래된 날짜는 archive API.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime

USER_AGENT = "golf-shorts/0.1"
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_REGION_URL = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "weather_code",
               "wind_speed_10m", "wind_gusts_10m"]
WEEKDAYS = "월화수목금토일"


def http_get_json(url, params, headers=None, timeout=20, retries=3):
    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}",
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)


# ---------------------------------------------------------------- 골프장

@dataclass
class Place:
    name: str
    address: str = ""
    lat: float | None = None
    lng: float | None = None
    distance_m: int | None = None
    source: str = ""   # "kakao" | "region" | "manual"


def _is_golf_course(category):
    """'스포츠,레저 > 골프 > 골프장' 은 통과, 연습장·스크린골프는 제외."""
    last = category.split(">")[-1].strip()
    return last.endswith("골프장") and not any(w in last for w in ("연습장", "스크린"))


def course_from_kakao(payload):
    """Kakao 키워드 검색 응답에서 가장 가까운 골프장. 없으면 None."""
    docs = [d for d in payload.get("documents", []) if _is_golf_course(d.get("category_name", ""))]
    if not docs:
        return None
    d = min(docs, key=lambda d: int(d.get("distance") or 10 ** 9))
    return Place(name=d["place_name"], address=d.get("road_address_name") or d.get("address_name", ""),
                 lat=float(d["y"]), lng=float(d["x"]),
                 distance_m=int(d["distance"]) if d.get("distance") else None, source="kakao")


def region_from_kakao(payload):
    """coord2regioncode 응답 → '경기도 여주시 북내면'. 법정동(B)보다 행정동(H)을 우선."""
    docs = sorted(payload.get("documents", []), key=lambda d: d.get("region_type") != "H")
    if not docs:
        return None
    d = docs[0]
    name = " ".join(p for p in (d.get("region_1depth_name"), d.get("region_2depth_name")) if p)
    return Place(name=name, address=d.get("address_name", ""), source="region")


def find_course(lat, lng, api_key, radius_m=3000):
    headers = {"Authorization": f"KakaoAK {api_key}"}
    payload = http_get_json(KAKAO_KEYWORD_URL, {"query": "골프장", "x": lng, "y": lat,
                                                "radius": radius_m, "sort": "distance", "size": 15},
                            headers)
    course = course_from_kakao(payload)
    if course:
        return course
    region = region_from_kakao(http_get_json(KAKAO_REGION_URL, {"x": lng, "y": lat}, headers))
    if region:
        region.lat, region.lng = lat, lng
    return region


# ---------------------------------------------------------------- 날씨

# WMO weather code → (한글, 이모지)
WMO = {
    0: ("맑음", "☀️"), 1: ("대체로 맑음", "🌤️"), 2: ("구름 조금", "⛅"), 3: ("흐림", "☁️"),
    45: ("안개", "🌫️"), 48: ("안개", "🌫️"),
    51: ("약한 이슬비", "🌦️"), 53: ("이슬비", "🌦️"), 55: ("강한 이슬비", "🌧️"),
    56: ("어는 이슬비", "🌧️"), 57: ("어는 이슬비", "🌧️"),
    61: ("약한 비", "🌦️"), 63: ("비", "🌧️"), 65: ("강한 비", "🌧️"),
    66: ("어는 비", "🌧️"), 67: ("어는 비", "🌧️"),
    71: ("약한 눈", "🌨️"), 73: ("눈", "🌨️"), 75: ("강한 눈", "❄️"), 77: ("싸락눈", "🌨️"),
    80: ("약한 소나기", "🌦️"), 81: ("소나기", "🌧️"), 82: ("강한 소나기", "⛈️"),
    85: ("약한 소낙눈", "🌨️"), 86: ("소낙눈", "❄️"),
    95: ("뇌우", "⛈️"), 96: ("우박 뇌우", "⛈️"), 99: ("우박 뇌우", "⛈️"),
}


@dataclass
class Weather:
    summary: str          # "맑음"
    emoji: str
    temp_min: float
    temp_max: float
    wind_avg: float       # m/s
    gust_max: float | None
    precipitation: float  # mm, 라운드 시간 합계
    humidity_avg: float | None
    hours: list           # 집계에 쓴 시각 목록 ("08:00" …)
    source: str = "open-meteo"

    @property
    def caption(self):
        temp = f"{self.temp_min:.0f}℃" if round(self.temp_min) == round(self.temp_max) \
            else f"{self.temp_min:.0f}~{self.temp_max:.0f}℃"
        parts = [f"{self.emoji} {self.summary}", temp, f"바람 {self.wind_avg:.0f}m/s"]
        if self.precipitation >= 0.5:
            parts.append(f"강수 {self.precipitation:.1f}mm")
        return " · ".join(parts)


def representative_code(codes):
    """라운드 시간대의 대표 날씨. 강수 코드(51+)가 1/3 이상이면 강수, 아니면 가장 흔한 하늘 상태."""
    wet = [c for c in codes if c >= 51]
    if wet and len(wet) * 3 >= len(codes):
        return Counter(wet).most_common(1)[0][0]
    dry = [c for c in codes if c < 51] or codes
    return Counter(dry).most_common(1)[0][0]


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else None


def summarize_weather(payload, start, end):
    """Open-Meteo hourly 응답에서 start~end(KST datetime) 시간대를 요약. 데이터가 없으면 None."""
    hourly = payload.get("hourly", {})
    lo = start.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    hi = end.replace(tzinfo=None)
    idx = [i for i, t in enumerate(hourly.get("time", []))
           if lo <= datetime.fromisoformat(t) <= hi
           and hourly["temperature_2m"][i] is not None and hourly["weather_code"][i] is not None]
    if not idx:
        return None

    def col(name):
        return [hourly.get(name, [None] * (max(idx) + 1))[i] for i in idx]

    code = representative_code([int(c) for c in col("weather_code")])
    label, emoji = WMO.get(code, ("알 수 없음", "🌡️"))
    temps = col("temperature_2m")
    gusts = [g for g in col("wind_gusts_10m") if g is not None]
    return Weather(summary=label, emoji=emoji, temp_min=min(temps), temp_max=max(temps),
                   wind_avg=_avg(col("wind_speed_10m")) or 0.0,
                   gust_max=max(gusts) if gusts else None,
                   precipitation=round(sum(p or 0 for p in col("precipitation")), 1),
                   humidity_avg=_avg(col("relative_humidity_2m")),
                   hours=[hourly["time"][i][11:16] for i in idx])


def fetch_weather(lat, lng, start, end, today=None):
    """start~end(같은 날, KST) 날씨. 최근 80일은 forecast(관측 반영), 그 이전은 archive(ERA5 재분석)."""
    day = start.date().isoformat()
    params = {"latitude": lat, "longitude": lng, "start_date": day, "end_date": day,
              "hourly": ",".join(HOURLY_VARS), "timezone": "Asia/Seoul", "wind_speed_unit": "ms"}
    recent = ((today or date.today()) - start.date()).days <= 80
    urls = [FORECAST_URL, ARCHIVE_URL] if recent else [ARCHIVE_URL, FORECAST_URL]
    for url in urls:
        try:
            weather = summarize_weather(http_get_json(url, params), start, end)
        except urllib.error.HTTPError:
            continue  # 범위 밖 날짜는 400 → 다른 API 로
        if weather:
            return weather
    return None


def date_label(d):
    """date → '2026.10.25 (토)'"""
    return f"{d:%Y.%m.%d} ({WEEKDAYS[d.weekday()]})"
