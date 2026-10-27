import tomllib
import unittest
from datetime import datetime
from pathlib import Path

from golf_deal_alert import kakao
from golf_deal_alert.alert import KST, build_messages, evaluate
from golf_deal_alert.balls import classify
from golf_deal_alert.parsing import extract_benefits, extract_price, extract_quantity
from golf_deal_alert.sources import Deal, naver_deals_from_json, ppomppu_deals_from_rss

CFG = tomllib.loads(
    (Path(__file__).parents[1] / "golf_deal_alert" / "config.toml").read_text(encoding="utf-8"))


class ParsingTest(unittest.TestCase):
    def test_price(self):
        self.assertEqual(extract_price("[G마켓] 프로V1 2025년형 1더즌 (49,900원/무료)"), 49900)
        self.assertEqual(extract_price("[쿠팡] 크롬소프트 2더즌 (52000/무료)"), 52000)
        self.assertEqual(extract_price("[11번가] Z스타 (5.2만/무료) (12구)"), 52000)
        self.assertIsNone(extract_price("프로V1 2025 신형 출시"))

    def test_quantity(self):
        self.assertEqual(extract_quantity("프로V1 2더즌"), (24, False))
        self.assertEqual(extract_quantity("투어B X 3+1더즌"), (48, False))
        self.assertEqual(extract_quantity("Z스타 3구 x 4"), (12, False))
        self.assertEqual(extract_quantity("크롬소프트 4슬리브"), (12, False))
        self.assertEqual(extract_quantity("볼빅 S3 24구"), (24, False))
        self.assertEqual(extract_quantity("프로V1 골프공"), (12, True))
        self.assertEqual(extract_quantity("프로V1 3피스 1다스"), (12, False))

    def test_benefits(self):
        body = "구매 링크<br>삼성카드 10% 청구할인 (5만원 이상)<br>배송비 무료<br>앱 첫구매 쿠폰 3천원"
        got = extract_benefits("[옥션] 프로V1 (39,000원/무료) (스마일카드 5%)", body)
        self.assertEqual(got, ["스마일카드 5%", "삼성카드 10% 청구할인 (5만원 이상)", "앱 첫구매 쿠폰 3천원"])


class ClassifyTest(unittest.TestCase):
    def test_models(self):
        self.assertEqual(classify("타이틀리스트 PRO V1x 골프공").name, "Titleist Pro V1x")
        self.assertEqual(classify("타이틀리스트 프로V1 골프공").name, "Titleist Pro V1")
        self.assertEqual(classify("브리지스톤 TOUR B XS").name, "Bridgestone Tour B XS")
        self.assertEqual(classify("스릭슨 Z-STAR XV").name, "Srixon Z-Star XV")
        self.assertEqual(classify("테일러메이드 TP5x").pieces, (5,))
        self.assertEqual(classify("무명브랜드 우레탄 3피스 골프공").pieces, (3,))
        self.assertIsNone(classify("타이틀리스트 투어소프트 골프공"))
        self.assertIsNone(classify("볼빅 비비드 2피스"))


def deal(title, price, **kw):
    return Deal(source=kw.pop("source", "뽐뿌"), key=title, title=title, url="https://x", price=price, **kw)


class EvaluateTest(unittest.TestCase):
    def test_pass(self):
        m = evaluate(deal("[쿠팡] 프로V1x 2더즌 (51,800원/무료)", 51800), CFG)
        self.assertEqual(m.per_dozen, 25900)

    def test_rejects(self):
        self.assertIsNone(evaluate(deal("[쿠팡] 프로V1 1더즌 (32,000원/무료)", 32000), CFG))
        self.assertIsNone(evaluate(deal("[G마켓] 프로V1 로스트볼 A급 30구 (25,000원)", 25000), CFG))
        self.assertIsNone(evaluate(deal("[쿠팡] TP5 1더즌 (25,000원)", 25000), CFG))
        self.assertIsNone(evaluate(deal("[쿠팡] 투어소프트 1더즌 (20,000원)", 20000), CFG))
        self.assertIsNone(evaluate(deal("[쿠팡] 프로V1 1더즌 (5,000원)", 5000), CFG))

    def test_gift_mention_is_not_accessory(self):
        self.assertIsNotNone(evaluate(deal("[네이버] Z스타 1더즌 볼마커 증정 (27,000원)", 27000), CFG))

    def test_naver_category(self):
        payload = {"items": [
            {"title": "<b>프로V1</b> 골프공 12구", "link": "https://smartstore.naver.com/a/products/1",
             "lprice": "27500", "mallName": "골프샵", "productId": "1", "productType": "2",
             "category1": "스포츠/레저", "category2": "골프", "category3": "골프공", "category4": "일반골프공"},
            {"title": "<b>프로V1</b> 로스트볼", "link": "https://x", "lprice": "15000",
             "productId": "2", "productType": "2", "category3": "골프공", "category4": "로스트볼"},
            {"title": "<b>프로V1</b> 12구", "link": "https://x", "lprice": "20000",
             "productId": "3", "productType": "5", "category3": "골프공"},
        ]}
        deals = naver_deals_from_json(payload)
        self.assertEqual(len(deals), 2)  # 중고(productType 5) 제외
        self.assertTrue(deals[0].naverpay)
        self.assertIsNotNone(evaluate(deals[0], CFG))
        self.assertIsNone(evaluate(deals[1], CFG))


RSS = """<?xml version="1.0" encoding="euc-kr"?>
<rss version="2.0"><channel><title>뽐뿌</title>
<item><title><![CDATA[[네이버] 스릭슨 Z-STAR XV 2더즌 (54,000원/무료)]]></title>
<link>https://www.ppomppu.co.kr/zboard/view.php?id=ppomppu&amp;no=123456</link>
<description><![CDATA[링크<br>네이버현대카드 5% 추가적립<br>스토어찜 쿠폰 2,000원]]></description>
<pubDate>Mon, 26 Oct 2026 21:10:00 +0900</pubDate></item>
</channel></rss>""".encode("cp949")


class SourcesTest(unittest.TestCase):
    def test_ppomppu_rss(self):
        [d] = ppomppu_deals_from_rss(RSS)
        self.assertEqual(d.key, "ppomppu:ppomppu:123456")
        self.assertEqual(d.price, 54000)
        self.assertEqual(d.mall, "네이버")
        self.assertTrue(d.naverpay)
        self.assertIn("네이버현대카드 5% 추가적립", d.benefits)
        self.assertEqual(evaluate(d, CFG).per_dozen, 27000)


class MessageTest(unittest.TestCase):
    def test_split_limit(self):
        long = "\n".join(f"- 조건 {i} " + "가" * 60 for i in range(10))
        self.assertTrue(all(len(c) <= kakao.TEXT_LIMIT for c in kakao.split_text(long)))
        self.assertEqual(kakao.split_text("a\nb"), ["a\nb"])

    def test_build(self):
        d = deal("[네이버] 프로V1 2더즌 (50,000원)", 50000, naverpay=True,
                 benefits=["삼성카드 7% 청구할인 " + "(조건)" * 30])
        msgs = build_messages([evaluate(d, CFG)], CFG, datetime(2026, 10, 27, 8, tzinfo=KST))
        self.assertTrue(all(len(t) <= kakao.TEXT_LIMIT for t, _ in msgs))
        joined = "\n".join(t for t, _ in msgs)
        self.assertIn("더즌당 25,000원", joined)
        self.assertIn("N+멤버십", joined)


if __name__ == "__main__":
    unittest.main()
