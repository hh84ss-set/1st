"""제목/본문에서 가격, 수량, 할인 조건을 뽑아내는 함수들."""
import html
import re

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?\s*만|\d{4,})"
_PRICE_RE = re.compile(_NUM + r"\s*(원)?")


def _to_won(token):
    token = token.replace(" ", "")
    if token.endswith("만"):
        return int(float(token[:-1]) * 10000)
    return int(token.replace(",", ""))


def _price_candidates(text):
    for m in _PRICE_RE.finditer(text):
        token, won = m.group(1), m.group(2)
        value = _to_won(token)
        plain_4digit = re.fullmatch(r"\d{4}", token) and not won
        if plain_4digit and 1900 <= value <= 2099:  # 연식(2025년형 등)
            continue
        if 1000 <= value <= 3_000_000:
            yield value, bool(won) or "," in token or token.endswith("만")


def extract_price(title):
    """뽐뿌식 제목 '... (49,900원/무료)' 에서 가격을 찾는다. 없으면 None."""
    parens = re.findall(r"\(([^()]*)\)", title)
    for group in reversed(parens):
        for value, _ in _price_candidates(group):
            return value
    for value, confident in _price_candidates(title):
        if confident:
            return value
    return None


_DOZEN = r"(?:더즌|다즌|다스|dozen|dz)"


def extract_quantity(text):
    """골프공 개수를 추정한다. 반환: (개수, 추정여부). 단서가 없으면 (12, True)."""
    t = text.lower()
    m = re.search(r"(\d+)\s*\+\s*(\d+)\s*" + _DOZEN, t)
    if m:
        return (int(m.group(1)) + int(m.group(2))) * 12, False
    m = re.search(r"(\d+)\s*" + _DOZEN, t)
    if m and int(m.group(1)) > 0:
        return int(m.group(1)) * 12, False
    m = re.search(r"(\d+)\s*(?:구|알)\s*[x×*]\s*(\d+)", t)
    if m:
        return int(m.group(1)) * int(m.group(2)), False
    m = re.search(r"(\d+)\s*슬리브", t)
    if m:
        return int(m.group(1)) * 3, False
    counts = [int(n) for n in re.findall(r"(\d+)\s*(?:구|알|개입|ea|balls?|pcs)(?![a-z가-힣])", t)]
    counts += [int(n) for n in re.findall(r"(\d+)\s*개(?![월가-힣])", t) if int(n) >= 6]
    counts = [n for n in counts if 1 <= n <= 240]
    if counts:
        return max(counts), False
    if re.search(_DOZEN, t):
        return 12, False
    if re.search(r"슬리브", t):
        return 3, False
    return 12, True


BENEFIT_KEYWORDS = (
    "카드", "청구할인", "즉시할인", "쿠폰", "적립", "페이백", "캐시백", "네이버페이",
    "n페이", "카카오페이", "토스페이", "페이코", "스마일페이", "무이자", "중복",
    "할인코드", "프로모션", "멤버십", "멤버쉽", "첫구매", "장바구니", "최대혜택",
)


def html_to_text(raw):
    raw = re.sub(r"(?i)<\s*(br|/p|/div|/li|/tr)\s*/?>", "\n", raw or "")
    raw = re.sub(r"<[^>]+>", " ", raw)
    return html.unescape(raw)


def _has_benefit(line):
    low = line.lower()
    return any(k in low for k in BENEFIT_KEYWORDS)


def extract_benefits(title, body="", limit=5, max_len=90):
    """카드/쿠폰/적립 조건이 담긴 문구를 제목 괄호와 본문 줄에서 뽑는다."""
    found = []
    for group in re.findall(r"\(([^()]*)\)|\[([^\[\]]*)\]", title):
        piece = (group[0] or group[1]).strip()
        if _has_benefit(piece):
            found.append(piece)
    for line in re.split(r"[\r\n]+", html_to_text(body)):
        line = re.sub(r"\s+", " ", line).strip(" -*•·▶▷■□※")
        if 4 <= len(line) and _has_benefit(line):
            found.append(line if len(line) <= max_len else line[: max_len - 1] + "…")
    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return unique[:limit]


def store_from_title(title):
    """'[쿠팡] ...' 형식 제목에서 쇼핑몰명을 꺼낸다."""
    m = re.match(r"\s*\[([^\]]+)\]", title)
    return m.group(1).strip() if m else ""
