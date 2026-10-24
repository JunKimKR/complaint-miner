"""한국어 리뷰를 의미 있는 단어로 쪼갠다."""
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


def tokenize(text: str) -> list[str]:
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
