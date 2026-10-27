import tomllib
import unittest
from datetime import datetime
from pathlib import Path

from golf_deal_alert import alert
from golf_deal_alert.alert import KST, evaluate, render_report
from golf_deal_alert.balls import classify
from golf_deal_alert.parsing import extract_benefits, extract_price, extract_quantity
from golf_deal_alert.sources import (Deal, ali_deals_from_json, ali_sign, dealbada_deals_from_list,
                                     naver_deals_from_json, post_body, ppomppu_deals_from_rss)

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


DEALBADA_LIST = """
<ul><li class="list"><a href="https://www.dealbada.com/bbs/board.php?bo_table=golf&amp;wr_id=9001">
 <span class="cate">공구</span> [스마트스토어] 스릭슨 Z-STAR 1더즌 (26,500원/무료)</a>
 <a href="https://www.dealbada.com/bbs/board.php?bo_table=golf&amp;wr_id=9001#c_1">3</a></li>
<li><a href="./board.php?bo_table=golf&amp;wr_id=9002">드라이버 샤프트 고민입니다</a></li>
<li><a href="./board.php?bo_table=other&amp;wr_id=1">다른 게시판</a></li></ul>"""

ALI_JSON = {"aliexpress_affiliate_product_query_response": {"resp_result": {
    "resp_code": 200, "resp_msg": "success", "result": {"current_record_count": 2, "products": {"product": [
        {"product_id": 1005001, "product_title": "타이틀리스트 프로 V1 골프공 12 개",
         "target_sale_price": "27800.0", "target_app_sale_price": "25900.0", "discount": "30%",
         "promotion_link": "https://s.click.aliexpress.com/e/_abc"},
        {"product_id": 1005002, "product_title": "Titleist Pro V1 Used Golf Balls 12pcs",
         "target_sale_price": "15000", "product_detail_url": "https://www.aliexpress.com/item/1005002.html"},
    ]}}}}}


class MoreSourcesTest(unittest.TestCase):
    def test_dealbada_list(self):
        deals = dealbada_deals_from_list(DEALBADA_LIST, "golf")
        self.assertEqual([d.key for d in deals], ["dealbada:golf:9001", "dealbada:golf:9002"])
        d = deals[0]
        self.assertTrue(d.url.startswith("https://www.dealbada.com/bbs/board.php?bo_table=golf&wr_id=9001"))
        self.assertTrue(d.naverpay)
        self.assertEqual(evaluate(d, CFG).per_dozen, 26500)
        self.assertIsNone(evaluate(deals[1], CFG))

    def test_post_body(self):
        page = '<div id="nav">쿠폰함</div><div id="bo_v_con">KB카드 5% 할인<br>본문</div><div id="bo_v_share">'
        self.assertEqual(extract_benefits("t", post_body(page)), ["KB카드 5% 할인"])

    def test_ali_sign_matches_official_sdk_rule(self):
        import hashlib
        params = {"b": "2", "a": "1", "method": "x"}
        expected = hashlib.md5("SECa1b2methodxSEC".encode()).hexdigest().upper()
        self.assertEqual(ali_sign("SEC", params), expected)

    def test_ali_parse(self):
        deals = ali_deals_from_json(ALI_JSON)
        self.assertEqual(deals[0].price, 25900)
        self.assertIn("앱 전용가", deals[0].benefits[0])
        self.assertEqual(evaluate(deals[0], CFG).per_dozen, 25900)
        self.assertIsNone(evaluate(deals[1], CFG))  # used 제외

    def test_ali_error(self):
        with self.assertRaises(RuntimeError):
            ali_deals_from_json({"error_response": {"code": "IncompleteSignature"}})


class SearchReportTest(unittest.TestCase):
    def run_search(self):
        from unittest import mock
        with mock.patch.object(alert, "fetch_ppomppu", lambda feeds, log: ppomppu_deals_from_rss(RSS)), \
             mock.patch.object(alert, "fetch_dealbada",
                               lambda boards, pages, log: dealbada_deals_from_list(DEALBADA_LIST, "golf")), \
             mock.patch.object(alert, "enrich_benefits", lambda d, log: None):
            state = {"pending": {}}
            return alert.search(CFG, state, datetime(2026, 10, 27, 9, tzinfo=KST), log=lambda *_: None), state

    def test_search_and_report(self):
        (hits, near, stats, rejected), state = self.run_search()
        self.assertEqual([m.per_dozen for m in hits], [26500, 27000])
        self.assertEqual(stats["뽐뿌"], 1)
        report = render_report(hits, near, stats, rejected, CFG, datetime(2026, 10, 27, 9, tzinfo=KST))
        self.assertIn("✅ 조건 충족 2건", report)
        self.assertIn("네이버현대카드 5% 추가적립", report)
        self.assertIn("N+멤버십", report)
        self.assertEqual(len(state["pending"]), 2)

    def test_near_miss(self):
        d = deal("[쿠팡] 프로V1 1더즌 (32,000원/무료)", 32000)
        self.assertIsNone(evaluate(d, CFG))
        m = evaluate(d, CFG, CFG["filter"]["near_max_price_per_dozen"])
        report = render_report([], [m], {}, [(d, "더즌당 32,000원 (기준 초과)")], CFG, datetime(2026, 10, 27, 9, tzinfo=KST))
        self.assertIn("지금은 조건에 맞는 딜이 없습니다", report)
        self.assertIn("아깝게 탈락", report)
        self.assertIn("기준 초과", report)

    def test_no_price_section(self):
        d = deal("스릭슨 Z Star 반반공 할인 정보", None)
        report = render_report([], [], {}, [(d, alert.check(d, CFG)[1])], CFG,
                               datetime(2026, 10, 27, 9, tzinfo=KST))
        self.assertIn("가격 확인 필요 1건", report)
        self.assertIn("Srixon Z-Star", report)

    def test_dealbada_comment_count_stripped(self):
        html_text = '<a href="./board.php?bo_table=forum_golf&amp;wr_id=1">커클랜드볼 6박스 댓글 8 개</a>'
        self.assertEqual(dealbada_deals_from_list(html_text, "forum_golf")[0].title, "커클랜드볼 6박스")

    def test_table_escapes_pipe(self):
        m = evaluate(deal("[쿠팡] 프로V1 | 1더즌 (25,000원)", 25000), CFG)
        report = render_report([m], [], {}, [], CFG, datetime(2026, 10, 27, 9, tzinfo=KST))
        row = [l for l in report.splitlines() if l.startswith("| 1 |")][0]
        self.assertEqual(row.count("|"), 7)


if __name__ == "__main__":
    unittest.main()
