"""골프공 핫딜 알리미.

  python -m golf_deal_alert.alert --mode collect   # 뽐뿌 RSS 를 모아 state 에 적재 (매시)
  python -m golf_deal_alert.alert --mode digest    # 적재분 + 네이버쇼핑 검색 → 카카오톡 (매일 08시)
  python -m golf_deal_alert.alert --mode digest --dry-run   # 전송 없이 메시지만 출력
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

from . import kakao
from .balls import Ball, classify, normalize
from .parsing import extract_quantity
from .sources import Deal, fetch_naver, fetch_ppomppu

KST = timezone(timedelta(hours=9))
DEFAULT_CONFIG = Path(__file__).with_name("config.toml")


@dataclass
class Match:
    deal: Deal
    ball: Ball
    quantity: int
    qty_estimated: bool
    per_dozen: int


def evaluate(deal, cfg):
    """조건 충족 시 Match, 아니면 None."""
    f = cfg["filter"]
    if deal.price is None:
        return None
    text = normalize(deal.title + " " + deal.category)
    if any(normalize(k) in text for k in f["exclude_keywords"] + f["accessory_keywords"]):
        return None
    if deal.category and "골프공" not in deal.category:
        return None
    ball = classify(deal.title)
    if ball is None or not all(p in f["allowed_pieces"] for p in ball.pieces):
        return None
    qty, estimated = extract_quantity(deal.title)
    per_dozen = round(deal.price * 12 / qty)
    if not f["min_price_per_dozen"] <= per_dozen <= f["max_price_per_dozen"]:
        return None
    return Match(deal, ball, qty, estimated, per_dozen)


# ---------------------------------------------------------------- state

def load_state(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"pending": {}, "sent": {}}


def save_state(path, state):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _deal_from_dict(d):
    return Deal(**d)


def collect(cfg, state, now, log=print):
    """뽐뿌 RSS 는 최근 글만 담기므로 매시간 모아 두었다가 아침에 한꺼번에 보낸다."""
    if not cfg["ppomppu"]["enabled"]:
        return []
    found = []
    for deal in fetch_ppomppu(cfg["ppomppu"]["feeds"], log=log):
        if evaluate(deal, cfg):
            state["pending"][deal.key] = {"deal": asdict(deal), "seen_at": now.isoformat()}
            found.append(deal)
    cutoff = now - timedelta(hours=cfg["notify"]["pending_hours"])
    state["pending"] = {k: v for k, v in state["pending"].items()
                        if datetime.fromisoformat(v["seen_at"]) >= cutoff}
    log(f"[collect] 뽐뿌 조건충족 {len(found)}건, 대기중 {len(state['pending'])}건")
    return found


def gather_matches(cfg, state, now, log=print):
    collect(cfg, state, now, log)
    deals = {k: _deal_from_dict(v["deal"]) for k, v in state["pending"].items()}
    ncfg = cfg["naver"]
    cid, secret = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if ncfg["enabled"] and cid and secret:
        for d in fetch_naver(ncfg["queries"], cid, secret, ncfg["display"], log=log):
            deals.setdefault(d.key, d)
    elif ncfg["enabled"]:
        log("[warn] NAVER_CLIENT_ID/SECRET 이 없어 네이버쇼핑 검색을 건너뜁니다")

    matches = [m for m in (evaluate(d, cfg) for d in deals.values()) if m]
    matches.sort(key=lambda m: m.per_dozen)

    # 최근에 같은(또는 더 싼) 가격으로 보낸 딜은 다시 보내지 않는다
    resend_after = timedelta(days=cfg["notify"]["resend_after_days"])
    fresh = []
    for m in matches:
        prev = state["sent"].get(m.deal.key)
        if prev and m.deal.price >= prev["price"] and \
                now - datetime.fromisoformat(prev["ts"]) < resend_after:
            continue
        fresh.append(m)

    # 같은 모델이 목록을 도배하지 않도록 모델당 개수 제한
    per_model, picked = {}, []
    for m in fresh:
        if per_model.get(m.ball.name, 0) >= cfg["notify"]["max_per_model"]:
            continue
        per_model[m.ball.name] = per_model.get(m.ball.name, 0) + 1
        picked.append(m)
    return picked[: cfg["notify"]["max_deals"]]


# ---------------------------------------------------------------- 메시지

def won(n):
    return f"{n:,}원"


def format_match(m, idx, total, cfg):
    d = m.deal
    qty_label = f"{m.quantity // 12}더즌" if m.quantity % 12 == 0 else f"{m.quantity}구"
    if m.qty_estimated:
        qty_label = "수량표기 없음→1더즌 가정"
    title = re.sub(r"\s+", " ", d.title)
    lines = [
        f"⛳ [{idx}/{total}] {m.ball.name} ({m.ball.pieces_label}·우레탄)",
        f"더즌당 {won(m.per_dozen)} (판매가 {won(d.price)}, {qty_label})",
        f"{d.source}" + (f" · {d.mall}" if d.mall else ""),
        f"📝 {title[:60]}",
    ]
    if d.benefits:
        lines.append("💳 할인·쿠폰 조건")
        lines += [f"- {b}" for b in d.benefits]
    elif d.source == "네이버쇼핑":
        lines.append("💳 카드·쿠폰: API 미제공 → 상품페이지 '혜택' 확인")
    if d.naverpay and cfg["naver"]["plus_membership"]:
        rate = cfg["naver"]["plus_extra_rate"]
        lines.append(f"🟢 N+멤버십 적립대상(N페이 결제) · 약 {won(int(d.price * rate))}"
                     f" ({rate * 100:g}% 가정)")
        lines += [f"- {b}" for b in cfg["my_benefits"]["naverpay"]]
    return "\n".join(lines)


def build_messages(matches, cfg, now):
    """반환: [(text, url)] — 카카오 텍스트 템플릿 200자 제한에 맞춰 분할."""
    f = cfg["filter"]
    pieces = "·".join(str(p) for p in f["allowed_pieces"])
    header = (f"⛳ 골프공 핫딜 {now:%m/%d} 아침 알림\n"
              f"조건: 우레탄 {pieces}피스 · 더즌당 {won(f['max_price_per_dozen'])} 이하 · 로스트볼 제외\n"
              f"충족 {len(matches)}건 (더즌당 최저가순)")
    msgs = [(header, matches[0].deal.url)]
    for i, m in enumerate(matches, 1):
        for j, chunk in enumerate(kakao.split_text(format_match(m, i, len(matches), cfg))):
            msgs.append((chunk if j == 0 else f"(이어서 {i})\n{chunk}"[:kakao.TEXT_LIMIT],
                         m.deal.url))
    return msgs


def send(messages, dry_run, log=print):
    if dry_run:
        for text, url in messages:
            log(f"----- ({len(text)}자) {url}\n{text}")
        return
    rest_key = os.environ["KAKAO_REST_API_KEY"]
    access, new_refresh = kakao.refresh_tokens(
        rest_key, os.environ["KAKAO_REFRESH_TOKEN"], os.environ.get("KAKAO_CLIENT_SECRET", ""))
    if new_refresh and os.environ.get("KAKAO_NEW_REFRESH_TOKEN_FILE"):
        Path(os.environ["KAKAO_NEW_REFRESH_TOKEN_FILE"]).write_text(new_refresh)
        log("[info] 카카오 리프레시 토큰이 갱신되었습니다")
    for text, url in messages:
        kakao.send_text(access, text, url)


def main(argv=None):
    p = argparse.ArgumentParser(description="골프공 핫딜 알리미")
    p.add_argument("--mode", choices=["collect", "digest"], default="digest")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--state", default=".state/state.json")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    cfg = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))
    state = load_state(args.state)
    now = datetime.now(KST)

    if args.mode == "collect":
        collect(cfg, state, now)
    else:
        matches = gather_matches(cfg, state, now)
        if not matches:
            print("[digest] 조건에 맞는 딜이 없어 알림을 보내지 않습니다")
        else:
            send(build_messages(matches, cfg, now), args.dry_run)
            if not args.dry_run:
                for m in matches:
                    state["sent"][m.deal.key] = {"price": m.deal.price, "ts": now.isoformat()}
                    state["pending"].pop(m.deal.key, None)
            print(f"[digest] {len(matches)}건 {'출력' if args.dry_run else '전송'} 완료")
        keep = now - timedelta(days=30)
        state["sent"] = {k: v for k, v in state["sent"].items()
                         if datetime.fromisoformat(v["ts"]) >= keep}
    if not args.dry_run:
        save_state(args.state, state)


if __name__ == "__main__":
    sys.exit(main())
