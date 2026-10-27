"""우레탄 커버 멀티피스 골프공 카탈로그와 모델 판별 로직.

패턴은 normalize() 를 거친 텍스트(소문자, 공백/하이픈 제거)에 대해 검사한다.
더 구체적인 모델(예: Pro V1x)을 덜 구체적인 모델(Pro V1)보다 앞에 둬야 한다.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Ball:
    name: str
    pieces: tuple  # 가능한 피스 수. 연식에 따라 다른 경우 여러 값
    pattern: str

    @property
    def pieces_label(self):
        return "~".join(str(p) for p in self.pieces) + "피스"


# 모두 우레탄(또는 TPU) 커버 모델. 아이오노머/써린 커버 모델(Tour Soft, Velocity,
# ERC Soft, Supersoft, e12, Q-Star 일반, Vivid 등)은 의도적으로 넣지 않았다.
CATALOG = [
    Ball("Titleist Pro V1x Left Dash", (4,), r"(prov1x|프로v1x|프로브이원엑스).*(leftdash|레프트대시)"),
    Ball("Titleist Pro V1x", (4,), r"prov1x|프로v1x|프로브이원엑스"),
    Ball("Titleist Pro V1 Left Dash", (4,), r"leftdash|레프트대시"),
    Ball("Titleist Pro V1", (3,), r"prov1|프로v1|프로브이원"),
    Ball("Titleist AVX", (3,), r"avx"),
    Ball("Titleist Tour Speed", (3,), r"tourspeed|투어스피드"),
    Ball("Callaway Chrome Tour X", (4,), r"chrometourx|크롬투어x|크롬투어엑스"),
    Ball("Callaway Chrome Tour", (4,), r"chrometour|크롬투어"),
    Ball("Callaway Chrome Soft X", (3, 4), r"chromesoftx|크롬소프트x|크롬소프트엑스"),
    Ball("Callaway Chrome Soft", (3, 4), r"chromesoft|크롬소프트"),
    Ball("TaylorMade TP5x", (5,), r"tp5x"),
    Ball("TaylorMade TP5", (5,), r"tp5"),
    Ball("TaylorMade Tour Response", (3,), r"tourresponse|투어리스폰스"),
    Ball("Bridgestone Tour B RXS", (3,), r"(tourb|투어b|투어비)(rxs|알엑스에스)"),
    Ball("Bridgestone Tour B RX", (3,), r"(tourb|투어b|투어비)(rx|알엑스)"),
    Ball("Bridgestone Tour B XS", (3,), r"(tourb|투어b|투어비)(xs|엑스에스)"),
    Ball("Bridgestone Tour B X", (3,), r"(tourb|투어b|투어비)(x|엑스)"),
    Ball("Srixon Z-Star XV", (4,), r"(zstar|z스타|제트스타)(xv|엑스브이)"),
    Ball("Srixon Z-Star Diamond", (3,), r"(zstar|z스타|제트스타)(diamond|다이아몬드)"),
    Ball("Srixon Z-Star", (3,), r"zstar|z스타|제트스타"),
    Ball("Srixon Q-Star Tour", (3,), r"qstartour|q스타투어|큐스타투어"),
    Ball("Mizuno RB Tour X", (4,), r"rbtourx|rb투어x"),
    Ball("Mizuno RB Tour", (3,), r"rbtour|rb투어"),
    Ball("Wilson Staff Model", (4,), r"staffmodel|스태프모델"),
    Ball("Kirkland Signature", (3,), r"kirkland|커클랜드"),
    Ball("Vice Pro Plus", (4,), r"(vicepro|바이스프로)(plus|플러스|\+)"),
    Ball("Vice Pro", (3,), r"vicepro|바이스프로"),
    Ball("Snell MTB", (3,), r"mtb(black|x|블랙)|스넬"),
    Ball("Volvik S4", (4,), r"(볼빅|volvik).{0,12}s4"),
    Ball("Volvik S3", (3,), r"(볼빅|volvik).{0,12}s3"),
]

_URETHANE = re.compile(r"우레탄|urethane")
_PIECES = re.compile(r"([2-5])(?:피스|pc|piece|겹)|(투|쓰리|포|파이브)피스")
_PIECE_WORDS = {"투": 2, "쓰리": 3, "포": 4, "파이브": 5}


def normalize(text):
    return re.sub(r"[\s\-_·.]+", "", text.lower())


def classify(text):
    """텍스트에서 모델을 판별한다. 카탈로그에 없으면 '우레탄 + N피스' 표기로 판별.

    반환: Ball 또는 None
    """
    norm = normalize(text)
    for ball in CATALOG:
        if re.search(ball.pattern, norm):
            return ball
    if _URETHANE.search(norm):
        m = _PIECES.search(norm)
        if m:
            pieces = int(m.group(1)) if m.group(1) else _PIECE_WORDS[m.group(2)]
            return Ball("우레탄 커버 골프공(브랜드 기타)", (pieces,), "")
    return None
