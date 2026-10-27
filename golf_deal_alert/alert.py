"""골프공 핫딜 검색기.

  python -m golf_deal_alert.alert --mode search    # 지금 검색 + 최근 누적분 → 결과 리포트 (버튼 실행)
  python -m golf_deal_alert.alert --mode collect   # 뽐뿌·딜바다를 모아 state 에 적재 (매시, 백그라운드)

GitHub Actions 에서 실행하면 리포트가 실행 결과 화면(Job Summary)에 표로 나온다.
"""
import argparse
import json
import os
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .balls import Ball, classify, normalize
from .parsing import extract_quantity
from .sources import Deal, enrich_benefits, fetch_aliexpress, fetch_dealbada, fetch_naver, fetch_ppomppu

KST = timezone(timedelta(hours=9))
DEFAULT_CONFIG = Path(__file__).with_name("config.toml")


@dataclass
class Match:
    deal: Deal
    ball: Ball
    quantity: int
    qty_estimated: bool
    per_dozen: int


NO_PRICE = "제목에 가격 없음"


def check(deal, cfg, max_price=None):
    """반환: (Match 또는 None, 탈락 사유). max_price 로 가격 상한만 바꿔 볼 수 있다."""
    f = cfg["filter"]
    text = normalize(deal.title + " " + deal.category)
    hit = [k for k in f["exclude_keywords"] + f["accessory_keywords"] if normalize(k) in text]
    if hit:
        return None, f"제외어 '{hit[0]}'"
    if deal.category and "골프공" not in deal.category:
        return None, "골프공 카테고리 아님"
    ball = classify(deal.title)
    if ball is None:
        return None, "우레탄 3·4피스 모델 판별 불가"
    if not all(p in f["allowed_pieces"] for p in ball.pieces):
        return None, f"{ball.name} {ball.pieces_label}"
    if deal.price is None:
        return None, NO_PRICE
    qty, estimated = extract_quantity(deal.title)
    per_dozen = round(deal.price * 12 / qty)
    if per_dozen < f["min_price_per_dozen"]:
        return None, f"더즌당 {won(per_dozen)} (옵션가/로스트볼 의심)"
    if per_dozen > (max_price or f["max_price_per_dozen"]):
        return None, f"더즌당 {won(per_dozen)} (기준 초과)"
    return Match(deal, ball, qty, estimated, per_dozen), ""


def evaluate(deal, cfg, max_price=None):
    """조건 충족 시 Match, 아니면 None."""
    return check(deal, cfg, max_price)[0]


_GOLF_BALL = re.compile(r"골프공|골프볼|golfball|우레탄")


def is_golf_ball_post(deal):
    return bool(_GOLF_BALL.search(normalize(deal.title)) or classify(deal.title))


# ---------------------------------------------------------------- state

def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"pending": {}}


def save_state(path, state):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def collect(cfg, state, now, pages=1, log=print):
    """커뮤니티 게시판은 최근 글만 보이므로 모아 두었다가 검색 때 함께 보여준다.

    '아깝게 탈락' 표시를 위해 near_max_price_per_dozen 이하인 글까지 저장한다.
    반환: 소스별 수집 글 수
    """
    near_max = cfg["filter"]["near_max_price_per_dozen"]
    stats, deals = {}, []
    if cfg["ppomppu"]["enabled"]:
        got = fetch_ppomppu(cfg["ppomppu"]["feeds"], log=log)
        stats["뽐뿌"] = len(got)
        deals += got
    if cfg["dealbada"]["enabled"]:
        got = fetch_dealbada(cfg["dealbada"]["boards"], pages=pages, log=log)
        stats["딜바다"] = len(got)
        deals += got
    for deal in deals:
        if deal.key in state["pending"] or not evaluate(deal, cfg, near_max):
            continue
        enrich_benefits(deal, log=log)
        state["pending"][deal.key] = {"deal": asdict(deal), "seen_at": now.isoformat()}
    cutoff = now - timedelta(hours=cfg["search"]["keep_hours"])
    state["pending"] = {k: v for k, v in state["pending"].items()
                        if datetime.fromisoformat(v["seen_at"]) >= cutoff}
    log(f"[collect] 수집 {stats}, 누적 후보 {len(state['pending'])}건")
    # 이번에 읽은 골프공 관련 글 중 탈락한 것과 사유 (검색 리포트용)
    rejected = []
    for d in deals:
        if is_golf_ball_post(d):
            m, reason = check(d, cfg, near_max)
            if m is None:
                rejected.append((d, reason))
    return stats, rejected


def search(cfg, state, now, log=print):
    """반환: (조건충족 목록, 아깝게 탈락 목록, 소스별 수집 현황, 골프공 글 탈락 사유)"""
    stats, rejected = collect(cfg, state, now, pages=cfg["search"]["dealbada_pages"], log=log)
    deals = {k: Deal(**v["deal"]) for k, v in state["pending"].items()}

    ncfg = cfg["naver"]
    cid, secret = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if ncfg["enabled"] and cid and secret:
        got = fetch_naver(ncfg["queries"], cid, secret, ncfg["display"], log=log)
        stats["네이버쇼핑"] = len(got)
        for d in got:
            deals.setdefault(d.key, d)
    acfg = cfg["aliexpress"]
    ali_key, ali_secret = os.environ.get("ALI_APP_KEY"), os.environ.get("ALI_APP_SECRET")
    if acfg["enabled"] and ali_key and ali_secret:
        got = fetch_aliexpress(acfg["queries"], ali_key, ali_secret,
                               os.environ.get("ALI_TRACKING_ID", ""), acfg["ship_to_country"], log=log)
        stats["알리익스프레스"] = len(got)
        for d in got:
            deals.setdefault(d.key, d)
    elif acfg["enabled"]:
        stats["알리익스프레스"] = "키 없음(건너뜀)"

    near_max = cfg["filter"]["near_max_price_per_dozen"]
    hits, near = [], []
    for d in deals.values():
        if m := evaluate(d, cfg):
            hits.append(m)
        elif m := evaluate(d, cfg, near_max):
            near.append(m)
    hits.sort(key=lambda m: m.per_dozen)
    near.sort(key=lambda m: m.per_dozen)
    return hits, near, stats, rejected


# ---------------------------------------------------------------- 리포트

def won(n):
    return f"{n:,}원"


def _cell(text):
    return re.sub(r"\s+", " ", text).replace("|", "／")


def _qty_label(m):
    if m.qty_estimated:
        return "수량표기 없음→1더즌 가정"
    return f"{m.quantity // 12}더즌" if m.quantity % 12 == 0 else f"{m.quantity}구"


def _table(matches):
    rows = ["| # | 모델 | 더즌당 | 판매가 (수량) | 출처 · 쇼핑몰 | 글/상품 |",
            "|---|---|---:|---|---|---|"]
    for i, m in enumerate(matches, 1):
        d = m.deal
        where = d.source + (f" · {d.mall}" if d.mall else "")
        rows.append(f"| {i} | {m.ball.name} ({m.ball.pieces_label}) | **{won(m.per_dozen)}** | "
                    f"{won(d.price)} ({_qty_label(m)}) | {_cell(where)} | [{_cell(d.title)[:50]}]({d.url}) |")
    return rows


def _conditions(m, cfg):
    """할인·쿠폰·적립 조건 줄 목록."""
    d, lines = m.deal, list(m.deal.benefits)
    if not lines and d.source in ("뽐뿌", "딜바다"):
        lines.append("글 본문에 카드·쿠폰 조건 표기 없음")
    if d.source == "네이버쇼핑" and not d.benefits:
        lines.append("카드·쿠폰: API 미제공 → 상품페이지 '혜택' 확인")
    if d.source == "알리익스프레스":
        lines.append("알리 앱 쿠폰·카드사 프로모션은 결제화면 확인")
        lines.append("⚠️ 해외직구: 정품여부·배송비·도착일 확인")
    if d.naverpay and cfg["naver"]["plus_membership"]:
        rate = cfg["naver"]["plus_extra_rate"]
        lines.append(f"🟢 N+멤버십 적립대상(N페이 결제) · 약 {won(int(d.price * rate))} "
                     f"({rate * 100:g}% 가정)")
        lines += cfg["my_benefits"]["naverpay"]
    return lines


def render_report(hits, near, stats, rejected, cfg, now):
    f = cfg["filter"]
    pieces = "·".join(str(p) for p in f["allowed_pieces"])
    out = [
        f"## ⛳ 골프공 핫딜 검색 결과 · {now:%Y-%m-%d %H:%M} KST",
        f"조건: 우레탄 {pieces}피스 · 더즌당 **{won(f['max_price_per_dozen'])} 이하** · 로스트볼 제외  ",
        f"범위: 지금 수집한 글 + 최근 {cfg['search']['keep_hours']}시간 누적분 · "
        "수집: " + ", ".join(f"{k} {v}" for k, v in stats.items()),
        "",
    ]
    if hits:
        out += [f"### ✅ 조건 충족 {len(hits)}건 (더즌당 최저가순)", ""] + _table(hits) + ["",
                "#### 💳 할인·쿠폰·적립 조건", ""]
        for i, m in enumerate(hits, 1):
            out.append(f"**{i}. {m.ball.name} — 더즌당 {won(m.per_dozen)}**")
            out += [f"- {_cell(c)}" for c in _conditions(m, cfg)]
            out.append("")
    else:
        out += ["### ❌ 지금은 조건에 맞는 딜이 없습니다", ""]
    if near:
        out += [f"### 🔸 아깝게 탈락 (더즌당 {won(f['max_price_per_dozen'] + 1)} ~ "
                f"{won(f['near_max_price_per_dozen'])})", ""] + _table(near[:10]) + [""]
    # 모델은 조건에 맞는데 제목에 가격이 없는 글(포럼 글 등)은 본문을 직접 봐야 한다
    no_price = [(d, r) for d, r in rejected if r == NO_PRICE]
    others = [(d, r) for d, r in rejected if r != NO_PRICE]
    if no_price:
        out += [f"### 🔎 가격 확인 필요 {len(no_price)}건 (모델은 조건 충족, 제목에 가격 없음)", ""]
        out += [f"- {classify(d.title).name} — [{_cell(d.title)[:60]}]({d.url}) · {d.source}"
                for d, _ in no_price[:15]]
        out.append("")
    if others:
        out += [f"<details><summary>이번에 읽은 골프공 관련 글 중 탈락 {len(others)}건 · 사유</summary>", ""]
        out += [f"- [{_cell(d.title)[:60]}]({d.url}) — {reason}" for d, reason in others[:30]]
        out += ["", "</details>", ""]
    else:
        out += ["_이번에 읽은 글 중 골프공 관련 글이 없었습니다._", ""]
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="골프공 핫딜 검색기")
    p.add_argument("--mode", choices=["search", "collect"], default="search")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--state", default=".state/state.json")
    p.add_argument("--out", help="리포트(Markdown) 저장 경로")
    args = p.parse_args(argv)

    cfg = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))
    state = load_state(args.state)
    now = datetime.now(KST)

    if args.mode == "collect":
        collect(cfg, state, now)
    else:
        report = render_report(*search(cfg, state, now), cfg, now)
        print(report)
        for path in filter(None, (args.out, os.environ.get("GITHUB_STEP_SUMMARY"))):
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(report + "\n")
    save_state(args.state, state)


if __name__ == "__main__":
    sys.exit(main())
