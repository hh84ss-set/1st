"""카카오톡 '나에게 보내기' (REST API).

- 액세스 토큰은 수명이 짧으므로 매 실행마다 리프레시 토큰으로 새로 발급받는다.
- 리프레시 토큰은 만료 1개월 전부터 갱신 시 새 값이 내려온다. 새 값은
  KAKAO_NEW_REFRESH_TOKEN_FILE 경로에 기록해 워크플로가 Secret 을 교체하게 한다.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://kauth.kakao.com/oauth/token"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
TEXT_LIMIT = 200  # 텍스트 템플릿 text 최대 길이


def _post(url, data, headers=None):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Kakao API {e.code}: {e.read().decode(errors='replace')}") from None


def refresh_tokens(rest_api_key, refresh_token, client_secret=""):
    """반환: (access_token, 새 refresh_token 또는 None)"""
    data = {"grant_type": "refresh_token", "client_id": rest_api_key,
            "refresh_token": refresh_token}
    if client_secret:
        data["client_secret"] = client_secret
    res = _post(TOKEN_URL, data)
    return res["access_token"], res.get("refresh_token")


def split_text(text, limit=TEXT_LIMIT):
    """200자 제한에 맞춰 줄 단위로 나눈다. 한 줄이 넘치면 강제로 자른다."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{cur}\n{line}" if cur else line
        if len(candidate) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def send_text(access_token, text, url, button_title="딜 보기"):
    template = {
        "object_type": "text",
        "text": text[:TEXT_LIMIT],
        "link": {"web_url": url, "mobile_web_url": url},
        "button_title": button_title,
    }
    res = _post(MEMO_URL, {"template_object": json.dumps(template, ensure_ascii=False)},
                {"Authorization": f"Bearer {access_token}"})
    if res.get("result_code") != 0:
        raise RuntimeError(f"카카오톡 전송 실패: {res}")
