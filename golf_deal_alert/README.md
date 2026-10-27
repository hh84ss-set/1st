# ⛳ 골프공 핫딜 알리미

매일 **08:00(KST)** 에 조건에 맞는 골프공 딜이 있으면 **카카오톡 '나에게 보내기'** 로 알려줍니다. 맞는 딜이 없으면 보내지 않습니다.

| 조건 | 기본값 (`config.toml`) |
|---|---|
| 공 | 우레탄 커버 **3·4피스** (브랜드 무관, 카탈로그 모델 + 제목에 '우레탄 N피스' 표기) |
| 가격 | **더즌당 28,000원 이하** (판매가 ÷ 수량 × 12) |
| 제외 | 로스트볼·중고·리퍼·A급·연습볼, 5피스(TP5/TP5x) |
| 혜택 | 뽐뿌 글의 카드·쿠폰·적립 조건 원문, N+멤버십 적립 대상 표시 |

## 동작 방식

```
매시 17분  뽐뿌 RSS 수집 → 조건 충족 글을 state(Actions 캐시)에 쌓아 둠
매일 08시  쌓인 뽐뿌 글 + 네이버쇼핑 검색 API → 필터 → 더즌당 최저가순 → 카카오톡
```

- 뽐뿌 RSS에는 최근 글만 담겨서, 하루 한 번만 읽으면 밤사이 올라온 딜을 놓칩니다. 그래서 매시간 모아 둡니다.
- 같은 딜은 가격이 내려가지 않는 한 7일 동안 다시 보내지 않습니다.
- 카카오 텍스트 메시지는 200자 제한이 있어, 딜마다 따로 보내고 길면 나눠서 보냅니다.

## 설정 (최초 1회)

### 1. 네이버 검색 API
1. [developers.naver.com](https://developers.naver.com) → 애플리케이션 등록 → 사용 API **검색** 선택
2. Client ID / Secret → GitHub Secrets `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`

### 2. 카카오톡 나에게 보내기
1. [developers.kakao.com](https://developers.kakao.com) → 내 애플리케이션 → 앱 추가
2. **카카오 로그인** 활성화, Redirect URI 에 `https://localhost` 추가
3. **동의항목** → '카카오톡 메시지 전송(talk_message)' 사용 설정
4. **플랫폼 → Web 사이트 도메인** 에 메시지 링크 도메인 등록 (미등록 도메인은 링크가 열리지 않음)
   `https://www.ppomppu.co.kr`, `https://m.ppomppu.co.kr`, `https://smartstore.naver.com`, `https://brand.naver.com`, `https://search.shopping.naver.com`
5. 리프레시 토큰 발급 (로컬 PC에서):
   ```bash
   python -m golf_deal_alert.kakao_auth url --key <REST_API_KEY>
   # 출력된 주소를 브라우저로 열어 동의 → 이동한 주소의 code=... 복사
   python -m golf_deal_alert.kakao_auth token --key <REST_API_KEY> --code <CODE>
   ```
6. GitHub Secrets: `KAKAO_REST_API_KEY`, `KAKAO_REFRESH_TOKEN` (Client Secret을 켰다면 `KAKAO_CLIENT_SECRET`)

### 3. 리프레시 토큰 자동 교체 (권장)
카카오 리프레시 토큰은 약 2개월 동안 유효하고, 만료 1개월 전부터는 갱신할 때 새 값이 내려옵니다. 워크플로가 이 새 값으로 Secret을 직접 교체하려면 다음이 필요합니다.
- Fine-grained PAT 발급 (이 레포만, **Secrets: Read and write**) → Secret `GH_PAT`

이 설정이 없으면 약 2개월마다 5번 과정을 다시 해야 합니다.

### 4. 워크플로 활성화
GitHub의 예약 실행(schedule)은 **기본 브랜치에 있는 워크플로만** 돌아갑니다. 이 브랜치를 기본 브랜치에 머지하세요.
머지한 뒤 Actions → `golf-deal-alert` → **Run workflow** 에서 `dry_run` 을 체크하고 실행하면 로그에서 메시지를 미리 볼 수 있습니다.

## 조정 포인트 (`config.toml`)
- `max_price_per_dozen`, `allowed_pieces` (TP5를 넣으려면 `5` 추가)
- `naver.plus_extra_rate`: N+멤버십 추가 적립률 **가정값(4%)** 입니다. 실제 요율로 바꿔 주세요.
- `my_benefits.naverpay`: 보유한 카드의 N페이 상시 혜택 (예: 네이버현대카드)
- `naver.queries`: 검색할 모델 키워드

## 한계
- 네이버쇼핑 API는 **카드 할인이나 쿠폰 정보를 주지 않습니다.** 그래서 이 항목은 '상품페이지 혜택 확인'으로 표시합니다. 카드·쿠폰 조건 원문은 뽐뿌 글에서 가져옵니다.
- 네이버 `lprice`는 최저 옵션가라서, 1슬리브 옵션 가격이 대표가로 잡힐 수 있습니다. 이런 경우를 거르려고 더즌당 10,000원 미만은 제외합니다.
- 수량 표기가 없는 상품은 1더즌으로 가정하고, 메시지에 그렇게 표시합니다.
- GitHub 예약 실행은 부하에 따라 수 분~수십 분 늦을 수 있습니다.

## 테스트
```bash
python -m unittest discover -s tests -v
```
