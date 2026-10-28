# ⛳ 골프공 핫딜 검색기

**버튼 한 번**으로 조건에 맞는 골프공 딜을 찾아 표로 보여줍니다. 키 발급이나 알림 설정은 필요 없습니다.

| 조건 | 기본값 (`config.toml`) |
|---|---|
| 공 | 우레탄 커버 **3·4피스** (브랜드 무관, 카탈로그 모델 + 제목에 '우레탄 N피스' 표기) |
| 가격 | **더즌당 28,000원 이하** (판매가 ÷ 수량 × 12), 35,000원까지는 '아깝게 탈락'으로 참고 표시 |
| 제외 | 로스트볼·중고·리퍼·A급·연습볼, 5피스(TP5/TP5x) |
| 표시 | 카드·쿠폰·적립 조건 원문, N+멤버십 적립 대상 여부 |

## 사용법
1. GitHub에서 **Actions → golf-deal-search → Run workflow → Run workflow** 를 누릅니다. GitHub 모바일 앱에서도 됩니다.
2. 1~2분 뒤 실행 항목을 열면 **Summary** 에 결과표가 나옵니다.

바로가기: https://github.com/hh84ss-set/1st/actions/workflows/golf-deal-search.yml

## 검색 범위
| 소스 | 방식 | 키 |
|---|---|---|
| 뽐뿌 | RSS. 최근 글만 담기므로 **매시간 백그라운드로 모아 두고**, 버튼을 누르면 최근 72시간치를 함께 보여줍니다 | 불필요 |
| 딜바다 골프포럼·국내핫딜 | RSS 또는 게시판 목록 (버튼 실행 시 3쪽까지) | 불필요 |
| 알리익스프레스 | 제휴 API. Secrets `ALI_APP_KEY`, `ALI_APP_SECRET`, `ALI_TRACKING_ID` 를 등록했을 때만 검색 | 선택 |
| 네이버쇼핑 | 쇼핑 검색 API가 2026-07-31 종료되어 자동 조회는 꺼져 있습니다. 네이버 딜은 커뮤니티 글로 잡히고, 결과표 하단에 모델별 가격 낮은순 검색 링크가 붙습니다 | - |

백그라운드 수집은 알림을 보내지 않습니다. 끄려면 워크플로의 `schedule` 블록을 지우면 됩니다. 다만 그러면 뽐뿌는 버튼을 누른 시점의 최근 글만 검색됩니다.

## 로컬 PC에서 실행 (Python 3.11+)
```bash
python -m golf_deal_alert.alert --out report.md
```

## 조정 포인트 (`config.toml`)
- `max_price_per_dozen`, `near_max_price_per_dozen`, `allowed_pieces` (TP5를 넣으려면 `5` 추가)
- `naver.plus_extra_rate`: N+멤버십 추가 적립률 **가정값(4%)** 입니다. 실제 요율로 바꿔 주세요.
- `my_benefits.naverpay`: 보유한 카드의 N페이 상시 혜택
- `search.keep_hours`, `search.dealbada_pages`
- `links.items`: 결과표 하단 네이버쇼핑·알리 검색 링크 (모델명, 네이버 검색어, 알리 검색어)

## 한계
- 카드·쿠폰 조건은 커뮤니티 글 본문에서 뽑습니다. 글에 적혀 있지 않으면 "표기 없음"으로 나옵니다.
- 알리: 앱 전용가는 반영하지만 알리 쿠폰이나 카드사 프로모션은 API로 받을 수 없습니다. 프리미엄 브랜드 공은 가품 여부를 확인하세요.
- 수량 표기가 없는 상품은 1더즌으로 가정하고, 결과표에 그렇게 표시합니다.

## 테스트
```bash
python -m unittest discover -s tests -v
```
