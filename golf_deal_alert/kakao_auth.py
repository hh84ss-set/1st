"""카카오 리프레시 토큰 최초 발급 도우미 (한 번만 실행).

1) python -m golf_deal_alert.kakao_auth url  --key <REST_API_KEY>
   → 출력된 주소를 브라우저에서 열고 동의 → 이동된 주소의 ?code=... 값을 복사
2) python -m golf_deal_alert.kakao_auth token --key <REST_API_KEY> --code <CODE> [--secret <CLIENT_SECRET>]
   → refresh_token 을 GitHub Secret KAKAO_REFRESH_TOKEN 에 저장
"""
import argparse
import urllib.parse

from .kakao import TOKEN_URL, _post

AUTH_URL = "https://kauth.kakao.com/oauth/authorize"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("step", choices=["url", "token"])
    p.add_argument("--key", required=True, help="카카오 앱 REST API 키")
    p.add_argument("--redirect", default="https://localhost",
                   help="카카오 앱에 등록한 Redirect URI")
    p.add_argument("--code")
    p.add_argument("--secret", default="", help="Client Secret 을 켰다면 입력")
    a = p.parse_args()

    if a.step == "url":
        print(AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": a.key, "redirect_uri": a.redirect,
            "response_type": "code", "scope": "talk_message"}))
        return
    data = {"grant_type": "authorization_code", "client_id": a.key,
            "redirect_uri": a.redirect, "code": a.code}
    if a.secret:
        data["client_secret"] = a.secret
    res = _post(TOKEN_URL, data)
    print("refresh_token:", res["refresh_token"])
    print("refresh_token 만료(초):", res.get("refresh_token_expires_in"))


if __name__ == "__main__":
    main()
