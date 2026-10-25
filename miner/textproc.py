"""리뷰를 의미 있는 단어로 쪼갠다. 한국어는 형태소 분석(Kiwi), 영어는 단순 규칙."""
from __future__ import annotations

import re
from functools import lru_cache

# 불만의 '주제'를 가리지 못하는 말들. 너무 넓게 지우면 '없다', '안 되다' 같은 핵심 신호까지 사라지므로 최소한만 둔다.
STOP = {
    "앱", "어플", "어플리케이션", "애플리케이션", "진짜", "정말", "너무", "그냥", "이거", "그거", "저거", "이것",
    "하다", "되다", "있다", "보다", "같다", "주다", "쓰다", "사용", "사용하다", "이용", "이용하다", "때", "것", "수", "좀",
    "번", "더", "왜", "다", "또", "계속", "자꾸", "후", "거", "제", "저", "나", "내", "우리", "분", "님", "점", "개", "별",
    "app", "the", "and", "is", "it", "to", "of", "a", "i", "this", "for", "in", "on", "my", "not",
}
KEEP_TAGS = {"NNG", "NNP", "SL", "XR"}           # 일반명사, 고유명사, 영어, 어근
VERB_TAGS = {"VV", "VA"}                           # 동사, 형용사 → '끊기다', '느리다'로 살린다
NEG_WORDS = {"안", "못"}                           # '안 되다' → '안되다'로 붙여 의미를 지킨다


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi
    return Kiwi()


_HANGUL = re.compile(r"[가-힣]")
_EN_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")
EN_NEG = {"not", "no", "never", "nothing", "don't", "doesn't", "didn't", "won't", "can't", "isn't", "wasn't"}
EN_STOP = None
EN_EXTRA_STOP = {"product", "products", "item", "really", "just", "like", "use", "used", "using", "get", "got",
                 "buy", "bought", "one", "would", "also", "even", "will", "much", "very", "well", "amazon",
                 "it's", "i'm", "i've", "it", "this", "that", "they", "them"}


def _en_stop():
    global EN_STOP
    if EN_STOP is None:
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS
        EN_STOP = (set(ENGLISH_STOP_WORDS) | EN_EXTRA_STOP) - EN_NEG
    return EN_STOP


def _en_norm(w: str) -> str:
    # 아주 단순한 복수형 정리: bottles→bottle, smells→smell (ss로 끝나는 말은 그대로)
    if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def tokenize_en(text: str) -> list[str]:
    stop = _en_stop()
    out: list[str] = []
    neg = False
    for w in _EN_WORD.findall(text.lower()):
        if w in EN_NEG:
            neg = True
            continue
        if len(w) < 3 or w in stop:
            continue
        w = _en_norm(w)
        out.append("not_" + w if neg else w)   # "doesn't work" → not_work
        neg = False
    return out


def tokenize(text: str) -> list[str]:
    if len(_HANGUL.findall(text)) < max(2, len(text) * 0.1):
        return tokenize_en(text)
    text = re.sub(r"\s+", " ", text)
    toks = _kiwi().tokenize(text)
    out: list[str] = []
    prev_neg = False
    for t in toks:
        form = t.form.lower()
        if t.form in NEG_WORDS and t.tag.startswith("MAG"):
            prev_neg = True
            continue
        word = None
        if t.tag in KEEP_TAGS and len(form) > 1:
            word = form
        elif t.tag in VERB_TAGS:
            word = form + "다"
        if word and word not in STOP:
            out.append(("안" + word) if prev_neg and t.tag in VERB_TAGS else word)
        if t.tag in VERB_TAGS or t.tag in KEEP_TAGS:
            prev_neg = False
    return out
