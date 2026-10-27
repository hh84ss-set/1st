"""딜 수집: 뽐뿌 RSS, 딜바다, 알리익스프레스 제휴 API, 네이버 쇼핑 검색 API(기존 키 보유 시)."""
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

from .parsing import extract_benefits, extract_price, html_to_text, store_from_title

USER_AGENT = "Mozilla/5.0 (compatible; golf-deal-alert/1.0)"


@dataclass
class Deal:
    source: str          # "뽐뿌" | "딜바다" | "알리익스프레스" | "네이버쇼핑"
    key: str             # 중복 제거용 고유키
    title: str
    url: str
    price: int | None
    mall: str = ""
    category: str = ""
    benefits: list = field(default_factory=list)
    naverpay: bool = False  # 스마트스토어/네이버페이 결제 → N+ 멤버십 적립 대상
    posted_at: str = ""


def http_get(url, headers=None, timeout=20, retries=3, data=None):
    req = urllib.request.Request(url, data=data,
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def decode(raw):
    """XML encoding 선언 또는 HTML meta charset 을 보고 디코딩. EUC-KR 은 cp949 로."""
    head = raw[:2000].decode("ascii", "ignore")
    m = re.search(r'(?:encoding|charset)=["\']?([\w-]+)', head)
    enc = (m.group(1) if m else "utf-8").lower()
    if enc in ("euc-kr", "ks_c_5601-1987", "x-windows-949"):
        enc = "cp949"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- 커뮤니티 RSS

def parse_rss(raw):
    """RSS 바이트를 (title, link, description, pubDate) 목록으로. EUC-KR 피드도 처리."""
    text = decode(raw)
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    items = []
    try:
        root = ET.fromstring(text)
        for it in root.iter("item"):
            items.append(tuple((it.findtext(tag) or "").strip()
                               for tag in ("title", "link", "description", "pubDate")))
    except ET.ParseError:
        for block in re.findall(r"<item>(.*?)</item>", text, re.S):
            def tag(name):
                m = re.search(rf"<{name}>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", block, re.S)
                return m.group(1).strip() if m else ""
            items.append((tag("title"), tag("link"), tag("description"), tag("pubDate")))
    return items


def _post_key(prefix, link):
    """뽐뿌(id/no), 그누보드(bo_table/wr_id) 글 주소에서 고유키를 만든다."""
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    board = (qs.get("id") or qs.get("bo_table") or [""])[0]
    no = (qs.get("no") or qs.get("wr_id") or [link])[0]
    return f"{prefix}:{board}:{no}"


def _community_deal(source, prefix, title, link, body="", posted=""):
    title = html_to_text(title).strip()
    mall = store_from_title(title)
    naverpay = bool(re.search(r"네이버|스마트스토어|n페이|네이버페이", mall + " " + title, re.I))
    return Deal(
        source=source, key=_post_key(prefix, link), title=title, url=link,
        price=extract_price(title), mall=mall, benefits=extract_benefits(title, body),
        naverpay=naverpay, posted_at=posted,
    )


def community_deals_from_rss(raw, source="뽐뿌", prefix="ppomppu"):
    deals = []
    for title, link, desc, pub in parse_rss(raw):
        try:
            posted = parsedate_to_datetime(pub).isoformat() if pub else ""
        except (TypeError, ValueError):
            posted = ""
        deals.append(_community_deal(source, prefix, title, link, desc, posted))
    return deals


def ppomppu_deals_from_rss(raw):
    return community_deals_from_rss(raw, "뽐뿌", "ppomppu")


def fetch_ppomppu(feeds, log=print):
    deals = []
    for url in feeds:
        try:
            deals += ppomppu_deals_from_rss(http_get(url))
        except Exception as e:  # 한 소스 실패가 전체를 막지 않도록
            log(f"[warn] RSS 수집 실패 {url}: {e}")
    return deals


# ---------------------------------------------------------------- 딜바다 (그누보드)

DEALBADA = "https://www.dealbada.com"


def dealbada_deals_from_list(html_text, board):
    """게시판 목록 HTML 에서 글 링크를 뽑는다 (RSS 가 꺼져 있을 때의 대안)."""
    pattern = (r'<a[^>]+href="([^"]*bo_table=' + re.escape(board) +
               r'(?:&amp;|&)wr_id=(\d+)[^"]*)"[^>]*>(.*?)</a>')
    seen, deals = set(), []
    for href, wr_id, inner in re.findall(pattern, html_text, re.S | re.I):
        title = re.sub(r"\s+", " ", html_to_text(inner)).strip()
        if wr_id in seen or len(title) < 4 or "comment" in href:
            continue
        seen.add(wr_id)
        link = urllib.parse.urljoin(DEALBADA + "/bbs/", href.replace("&amp;", "&"))
        deals.append(_community_deal("딜바다", "dealbada", title, link))
    return deals


def post_body(html_text):
    """그누보드 글 본문(#bo_v_con)만 텍스트로. 못 찾으면 빈 문자열."""
    m = re.search(r'id="bo_v_con"[^>]*>(.*?)(?:<div[^>]+id="bo_v_|<section[^>]+id="bo_vc)',
                  html_text, re.S)
    return m.group(1) if m else ""


def fetch_dealbada(boards, pages=1, log=print):
    """RSS 가 있으면 RSS 로 최근 글을, 없거나 pages>1 이면 목록 페이지를 pages 쪽까지 읽는다."""
    deals = {}
    for board in boards:
        try:
            raw = http_get(f"{DEALBADA}/bbs/rss.php?bo_table={board}")
            got = community_deals_from_rss(raw, "딜바다", "dealbada") if b"<item" in raw else []
        except Exception:
            got = []
        for page in range(1 if not got else 2, pages + 1):
            try:
                html_text = decode(http_get(f"{DEALBADA}/bbs/board.php?bo_table={board}&page={page}"))
                got += dealbada_deals_from_list(html_text, board)
            except Exception as e:
                log(f"[warn] 딜바다 '{board}' {page}쪽 수집 실패: {e}")
                break
        for d in got:
            deals.setdefault(d.key, d)
    return list(deals.values())


def enrich_benefits(deal, log=print):
    """본문이 없던 글(목록 파싱분)은 글을 열어 카드·쿠폰 조건을 채운다."""
    if deal.benefits or deal.source != "딜바다":
        return
    try:
        body = post_body(decode(http_get(deal.url)))
        deal.benefits = extract_benefits(deal.title, body)
    except Exception as e:
        log(f"[warn] 본문 조회 실패 {deal.url}: {e}")


# ---------------------------------------------------------------- 알리익스프레스 제휴 API

ALI_API = "https://api-sg.aliexpress.com/sync"


def ali_sign(secret, params):
    """AliExpress Open Platform md5 서명: MD5(secret + 정렬된 k+v 연결 + secret) 대문자."""
    base = secret + "".join(f"{k}{params[k]}" for k in sorted(params)) + secret
    return hashlib.md5(base.encode("utf-8")).hexdigest().upper()


def _won_float(value):
    try:
        return int(round(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return None


def ali_deals_from_json(payload):
    resp = payload.get("aliexpress_affiliate_product_query_response", {}).get("resp_result", {})
    if resp.get("resp_code") not in (200, "200"):
        if "error_response" in payload or resp:
            raise RuntimeError(f"AliExpress API 오류: {payload.get('error_response') or resp.get('resp_msg')}")
        return []
    products = ((resp.get("result") or {}).get("products") or {}).get("product") or []
    deals = []
    for p in products:
        sale = _won_float(p.get("target_sale_price"))
        app = _won_float(p.get("target_app_sale_price"))
        price = min(x for x in (sale, app) if x) if (sale or app) else None
        benefits = []
        if app and sale and app < sale:
            benefits.append(f"앱 전용가 적용 시 {app:,}원 (웹가 {sale:,}원)")
        if p.get("discount"):
            benefits.append(f"할인율 {p['discount']} (정가 대비)")
        deals.append(Deal(
            source="알리익스프레스", key=f"ali:{p.get('product_id')}",
            title=html_to_text(p.get("product_title", "")).strip(),
            url=p.get("promotion_link") or p.get("product_detail_url", ""),
            price=price, mall=p.get("shop_name", ""), benefits=benefits,
        ))
    return deals


def fetch_aliexpress(queries, app_key, app_secret, tracking_id="", ship_to="KR", log=print):
    deals = {}
    for q in queries:
        # 관련도순 + 판매량순: 단품/저가 잡상품이 위로 오는 가격순은 쓰지 않는다
        for sort in (None, "LAST_VOLUME_DESC"):
            sys_params = {
                "app_key": app_key, "method": "aliexpress.affiliate.product.query",
                "sign_method": "md5", "timestamp": str(int(time.time() * 1000)),
                "v": "2.0", "format": "json",
            }
            app_params = {"keywords": q, "page_size": "50", "target_currency": "KRW",
                          "target_language": "KO", "ship_to_country": ship_to}
            if tracking_id:
                app_params["tracking_id"] = tracking_id
            if sort:
                app_params["sort"] = sort
            sys_params["sign"] = ali_sign(app_secret, {**sys_params, **app_params})
            url = f"{ALI_API}?{urllib.parse.urlencode(sys_params)}"
            try:
                raw = http_get(url, {"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
                               data=urllib.parse.urlencode(app_params).encode())
                for d in ali_deals_from_json(json.loads(raw)):
                    deals.setdefault(d.key, d)
            except Exception as e:
                log(f"[warn] 알리익스프레스 검색 실패 '{q}': {e}")
    return list(deals.values())


# ---------------------------------------------------------------- 네이버 쇼핑

NAVER_SHOP_API = "https://openapi.naver.com/v1/search/shop.json"
NAVERPAY_HOSTS = ("smartstore.naver.com", "brand.naver.com", "shopping.naver.com/window")


def naver_deals_from_json(payload):
    deals = []
    for it in payload.get("items", []):
        # productType 1~3: 일반상품. 4~6 중고, 7~9 단종, 10~12 판매예정은 제외
        if str(it.get("productType", "1")) not in ("1", "2", "3"):
            continue
        link = it.get("link", "")
        category = " > ".join(c for c in (it.get(f"category{i}", "") for i in range(1, 5)) if c)
        title = html_to_text(it.get("title", "")).strip()
        deals.append(Deal(
            source="네이버쇼핑", key=f"naver:{it.get('productId') or link}", title=title,
            url=link, price=int(it["lprice"]) if str(it.get("lprice", "")).isdigit() else None,
            mall=it.get("mallName", ""), category=category,
            naverpay=any(h in link for h in NAVERPAY_HOSTS),
        ))
    return deals


def fetch_naver(queries, client_id, client_secret, display=100, log=print):
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    deals = {}
    for q in queries:
        # 관련도순 + 낮은가격순 두 번 조회해서 '싼데 관련 있는' 상품을 넓게 잡는다
        for sort in ("sim", "asc"):
            params = urllib.parse.urlencode(
                {"query": q, "display": display, "sort": sort, "exclude": "used:rental"})
            try:
                payload = json.loads(http_get(f"{NAVER_SHOP_API}?{params}", headers))
            except Exception as e:
                log(f"[warn] 네이버 검색 실패 '{q}' ({sort}): {e}")
                continue
            for d in naver_deals_from_json(payload):
                deals.setdefault(d.key, d)
            time.sleep(0.15)  # 초당 호출 제한 여유
    return list(deals.values())
