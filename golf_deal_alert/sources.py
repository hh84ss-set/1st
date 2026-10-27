"""딜 수집: 뽐뿌 RSS, 네이버 쇼핑 검색 API."""
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
    source: str          # "뽐뿌" | "네이버쇼핑"
    key: str             # 중복 제거용 고유키
    title: str
    url: str
    price: int | None
    mall: str = ""
    category: str = ""
    benefits: list = field(default_factory=list)
    naverpay: bool = False  # 스마트스토어/네이버페이 결제 → N+ 멤버십 적립 대상
    posted_at: str = ""


def http_get(url, headers=None, timeout=20, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


# ---------------------------------------------------------------- 뽐뿌 RSS

def parse_rss(raw):
    """RSS 바이트를 (title, link, description, pubDate) 목록으로. EUC-KR 피드도 처리."""
    head = raw[:200].decode("ascii", "ignore")
    m = re.search(r'encoding=["\']([\w-]+)["\']', head)
    enc = (m.group(1) if m else "utf-8").lower()
    if enc in ("euc-kr", "ks_c_5601-1987", "x-windows-949"):
        enc = "cp949"
    text = raw.decode(enc, errors="replace")
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


def _ppomppu_key(link):
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
    board = qs.get("id", ["ppomppu"])[0]
    no = qs.get("no", [link])[0]
    return f"ppomppu:{board}:{no}"


def ppomppu_deals_from_rss(raw):
    deals = []
    for title, link, desc, pub in parse_rss(raw):
        title = html_to_text(title).strip()
        mall = store_from_title(title)
        try:
            posted = parsedate_to_datetime(pub).isoformat() if pub else ""
        except (TypeError, ValueError):
            posted = ""
        body = html_to_text(desc)
        naverpay = bool(re.search(r"네이버|스마트스토어|n페이|네이버페이", mall + " " + title, re.I))
        deals.append(Deal(
            source="뽐뿌", key=_ppomppu_key(link), title=title, url=link,
            price=extract_price(title), mall=mall, benefits=extract_benefits(title, body),
            naverpay=naverpay, posted_at=posted,
        ))
    return deals


def fetch_ppomppu(feeds, log=print):
    deals = []
    for url in feeds:
        try:
            deals += ppomppu_deals_from_rss(http_get(url))
        except Exception as e:  # 한 소스 실패가 전체를 막지 않도록
            log(f"[warn] RSS 수집 실패 {url}: {e}")
    return deals


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
