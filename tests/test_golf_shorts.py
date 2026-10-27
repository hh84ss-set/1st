import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from PIL import Image

from golf_shorts import media
from golf_shorts.enrich import (course_from_kakao, region_from_kakao, representative_code,
                                summarize_weather)
from golf_shorts.media import KST, exif_datetime, gps_to_decimal, parse_iso6709, video_from_probe
from golf_shorts.round import build_round, run

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def write_photo(path, taken="2026:10:25 08:12:33", offset="+09:00", gps=(37.0, 17.0, 24.0),
                lng=(127.0, 39.0, 18.0), fmt=None):
    img = Image.new("RGB", (40, 30), "green")
    exif = Image.Exif()
    exif[media.ORIENTATION] = 6
    if taken:
        exif.get_ifd(media.EXIF_IFD).update({media.DATETIME_ORIGINAL: taken, media.OFFSET_TIME_ORIGINAL: offset})
    if gps:
        exif.get_ifd(media.GPS_IFD).update({media.GPS_LAT_REF: "N", media.GPS_LAT: gps,
                                            media.GPS_LNG_REF: "E", media.GPS_LNG: lng})
    img.save(path, format=fmt, exif=exif)


def write_video(path, creation="2026-10-25T11:40:02+0900", location="+37.2900+127.6560+080.000/"):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x48:d=2",
                    "-c:v", "mpeg4", "-movflags", "use_metadata_tags",
                    "-metadata", f"com.apple.quicktime.creationdate={creation}",
                    "-metadata", f"com.apple.quicktime.location.ISO6709={location}", str(path)],
                   check=True)


class MetadataTest(unittest.TestCase):
    def test_gps_to_decimal(self):
        self.assertAlmostEqual(gps_to_decimal(((37, 1), (17, 1), (2400, 100)), "N"), 37.29, places=4)
        self.assertAlmostEqual(gps_to_decimal((127.5, 0, 0), "W"), -127.5)

    def test_exif_datetime(self):
        self.assertEqual(exif_datetime("2026:10:25 08:12:33", "+09:00"), datetime(2026, 10, 25, 8, 12, 33, tzinfo=KST))
        # 해외 라운드(괌 +10:00)도 KST 로 맞춘다
        self.assertEqual(exif_datetime("2026:10:25 09:12:33", "+10:00").hour, 8)
        self.assertEqual(exif_datetime("2026:10:25 08:12:33").hour, 8)
        self.assertIsNone(exif_datetime("0000:00:00 00:00:00"))

    def test_iso6709(self):
        self.assertEqual(parse_iso6709("+37.5665+126.9780+038.000/"), (37.5665, 126.978))
        self.assertEqual(parse_iso6709("-33.8688+151.2093/"), (-33.8688, 151.2093))
        self.assertIsNone(parse_iso6709("abc"))

    def test_video_from_probe_iphone(self):
        probe = {"format": {"duration": "12.5", "tags": {
            "creation_time": "2026-10-24T23:40:05.000000Z",
            "com.apple.quicktime.creationdate": "2026-10-25T08:40:02+0900",
            "com.apple.quicktime.location.ISO6709": "+37.2901+127.6561+080.000/"}},
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}]}
        item = video_from_probe("a.MOV", probe)
        self.assertEqual(item.taken_at, "2026-10-25T08:40:02+09:00")
        self.assertEqual((item.lat, item.lng, item.duration, item.rotation), (37.2901, 127.6561, 12.5, 90))
        self.assertTrue(item.portrait)

    def test_video_utc_creation_time_only(self):
        item = video_from_probe("b.mp4", {"format": {"tags": {"creation_time": "2026-10-24T23:40:05.000000Z"}}})
        self.assertEqual(item.taken_at, "2026-10-25T08:40:05+09:00")

    def test_read_photo_jpeg(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "IMG_0001.JPG"
            write_photo(p)
            item = media.read_media(p)
        self.assertEqual(item.taken_at, "2026-10-25T08:12:33+09:00")
        self.assertEqual((item.lat, item.lng, item.rotation, item.portrait), (37.29, 127.655, 90, True))

    def test_read_photo_heic(self):
        try:
            import pillow_heif
        except ImportError:
            self.skipTest("pillow-heif 미설치")
        pillow_heif.register_heif_opener()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "IMG_0002.HEIC"
            write_photo(p, fmt="HEIF")
            item = media.read_media(p)
        self.assertEqual((item.taken_at, item.lat), ("2026-10-25T08:12:33+09:00", 37.29))

    def test_photo_without_exif_falls_back_to_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "kakao.jpg"
            write_photo(p, taken=None, gps=None)
            item = media.read_media(p)
        self.assertEqual(item.time_source, "file_mtime")
        self.assertIsNone(item.lat)

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg 미설치")
    def test_read_video_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "IMG_0003.MOV"
            write_video(p)
            item = media.read_media(p)
        self.assertEqual(item.taken_at, "2026-10-25T11:40:02+09:00")
        self.assertEqual((item.lat, item.lng), (37.29, 127.656))
        self.assertAlmostEqual(item.duration, 2.0, places=1)


class EnrichTest(unittest.TestCase):
    def test_course_from_kakao_skips_practice_ranges(self):
        payload = {"documents": [
            {"place_name": "○○스크린골프", "category_name": "스포츠,레저 > 골프 > 스크린골프장",
             "distance": "120", "x": "127.65", "y": "37.29"},
            {"place_name": "○○골프연습장", "category_name": "스포츠,레저 > 골프 > 골프연습장",
             "distance": "300", "x": "127.65", "y": "37.29"},
            {"place_name": "남여주CC", "category_name": "스포츠,레저 > 골프 > 골프장", "distance": "850",
             "road_address_name": "경기 여주시 ...", "x": "127.656", "y": "37.291"},
            {"place_name": "먼CC", "category_name": "스포츠,레저 > 골프 > 골프장", "distance": "2900",
             "x": "127.6", "y": "37.3"}]}
        c = course_from_kakao(payload)
        self.assertEqual((c.name, c.distance_m, c.source), ("남여주CC", 850, "kakao"))
        self.assertIsNone(course_from_kakao({"documents": payload["documents"][:2]}))

    def test_region_fallback(self):
        payload = {"documents": [
            {"region_type": "B", "region_1depth_name": "경기도", "region_2depth_name": "여주시", "address_name": "B"},
            {"region_type": "H", "region_1depth_name": "경기도", "region_2depth_name": "여주시", "address_name": "H"}]}
        r = region_from_kakao(payload)
        self.assertEqual((r.name, r.address, r.source), ("경기도 여주시", "H", "region"))

    def test_representative_code(self):
        self.assertEqual(representative_code([0, 0, 1, 61, 2]), 0)    # 잠깐 비 → 하늘 상태
        self.assertEqual(representative_code([3, 61, 61, 63, 3]), 61)  # 절반 이상 비 → 비

    def test_summarize_weather(self):
        hours = [f"2026-10-25T{h:02d}:00" for h in range(24)]
        payload = {"hourly": {
            "time": hours,
            "temperature_2m": [10 + h * 0.5 for h in range(24)],
            "relative_humidity_2m": [60] * 24,
            "precipitation": [0] * 24,
            "weather_code": [0] * 24,
            "wind_speed_10m": [2.0, 4.0] * 12,
            "wind_gusts_10m": [5.0] * 24}}
        w = summarize_weather(payload, datetime(2026, 10, 25, 7, 40, tzinfo=KST),
                              datetime(2026, 10, 25, 12, 10, tzinfo=KST))
        self.assertEqual(w.hours, ["07:00", "08:00", "09:00", "10:00", "11:00", "12:00"])
        self.assertEqual((w.temp_min, w.temp_max, w.wind_avg, w.summary), (13.5, 16.0, 3.0, "맑음"))
        self.assertEqual(w.caption, "☀️ 맑음 · 14~16℃ · 바람 3m/s")

    def test_summarize_weather_missing_data(self):
        payload = {"hourly": {"time": ["2026-10-25T08:00"], "temperature_2m": [None], "weather_code": [None]}}
        self.assertIsNone(summarize_weather(payload, datetime(2026, 10, 25, 8, tzinfo=KST),
                                            datetime(2026, 10, 25, 9, tzinfo=KST)))


class RoundTest(unittest.TestCase):
    def test_build_round_picks_main_day_and_warns(self):
        items = [media.MediaItem("a.jpg", "photo", "2026-10-25T07:40:00+09:00", "exif", 37.29, 127.65),
                 media.MediaItem("b.mov", "video", "2026-10-25T11:58:00+09:00", "quicktime", 37.30, 127.66,
                                 duration=120),
                 media.MediaItem("c.jpg", "photo", "2026-10-25T09:00:00+09:00", "exif"),
                 media.MediaItem("old.jpg", "photo", "2026-09-01T09:00:00+09:00", "exif", 35.0, 129.0)]
        day, start, end, center, warnings = build_round(items)
        self.assertEqual(day, date(2026, 10, 25))
        self.assertEqual((start.hour, start.minute, end.hour, end.minute), (7, 40, 12, 0))
        self.assertEqual(center, (37.295, 127.655))
        self.assertTrue(any("old.jpg" in w for w in warnings))
        self.assertTrue(any("GPS 없는 파일 1개" in w for w in warnings))

    @unittest.skipUnless(HAS_FFMPEG, "ffmpeg 미설치")
    def test_run_offline_with_manual_course(self):
        with tempfile.TemporaryDirectory() as d:
            write_photo(Path(d) / "IMG_0001.JPG")
            write_video(Path(d) / "IMG_0002.MOV")
            result = run(Path(d), course="남여주CC", offline=True)
        self.assertEqual(result["caption"], "2026.10.25 (일) | 남여주CC")
        self.assertEqual([m["path"] for m in result["media"]], ["IMG_0001.JPG", "IMG_0002.MOV"])
        self.assertEqual(result["course"]["source"], "manual")
        json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
