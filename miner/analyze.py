"""불만 리뷰를 주제별로 묶고, 주제마다 '창업 기회 점수'를 매긴다."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import Normalizer

from .sources import Review
from .textproc import tokenize

RECENT_DAYS = 90


@dataclass
class Theme:
    id: int
    keywords: list[str]
    reviews: list[Review]
    samples: list[Review]
    share: float                      # 전체 불만 중 비중 (0~1)
    avg_likes: float
    growth: float | None              # 최근 90일 비중 ÷ 이전 비중. 1보다 크면 늘어나는 불만
    by_app: dict[str, float]          # 앱별로 그 앱 불만 중 이 주제 비중
    cross_app: int                    # 이 주제가 8% 이상인 앱 수 (여러 앱 공통 불만일수록 시장 전체의 빈틈)
    score: float = 0.0
    label: str = ""                   # LLM이 붙이는 이름 (없으면 키워드로 대체)
    pain: str = ""
    opportunity: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.label or " · ".join(self.keywords[:3])


def _analyzer(text: str) -> list[str]:
    toks = tokenize(text)
    return toks + [a + " " + b for a, b in zip(toks, toks[1:])]


def _pick_k(X: np.ndarray, n: int, k_min: int, k_max: int, seed: int) -> int:
    """실루엣 점수로 주제 수를 고른다. 점수는 k가 클수록 조금씩 오르는 경향이 있어서,
    최고점의 90% 안에 드는 가장 작은 k를 쓴다(잘게 쪼개진 주제보다 큰 주제가 읽기 쉽다)."""
    k_max = max(k_min, min(k_max, n // 20))
    if k_max <= k_min:
        return k_min
    scores = {}
    for k in range(k_min, k_max + 1):
        labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(X)
        scores[k] = silhouette_score(X, labels, sample_size=min(2000, n), random_state=seed)
    best = max(scores.values())
    return min(k for k, s in scores.items() if s >= 0.9 * best)


def _split_big(X: np.ndarray, labels: np.ndarray, n: int, seed: int, max_share: float = 0.25) -> np.ndarray:
    """전체의 25%가 넘는 '잡동사니' 주제는 한 번 더 잘게 나눈다. 큰 묶음은 보통 여러 불만이 섞인 것이다."""
    labels = labels.copy()
    next_id = int(labels.max()) + 1
    for c in range(int(labels.max()) + 1):
        idx = np.where(labels == c)[0]
        if len(idx) < 60 or len(idx) / n <= max_share:
            continue
        sub_k = _pick_k(X[idx], len(idx), 2, 6, seed)
        sub = KMeans(n_clusters=sub_k, n_init=10, random_state=seed).fit_predict(X[idx])
        for j in range(1, sub_k):
            labels[idx[sub == j]] = next_id
            next_id += 1
    return labels


def find_themes(reviews: list[Review], k: int | None = None, k_min: int = 4, k_max: int = 12,
                seed: int = 42) -> list[Theme]:
    if len(reviews) < 20:
        raise ValueError(f"불만 리뷰가 {len(reviews)}개뿐이라 묶을 수 없습니다. 최소 20개, 가능하면 200개 이상 모아 주세요.")
    texts = [r.text for r in reviews]
    vec = TfidfVectorizer(analyzer=_analyzer, min_df=2, max_df=0.5, sublinear_tf=True)
    tfidf = vec.fit_transform(texts)
    if tfidf.shape[1] < 5:
        raise ValueError("리뷰에서 뽑힌 단어가 너무 적습니다. 리뷰를 더 모아 주세요.")
    dims = max(2, min(100, tfidf.shape[1] - 1, len(texts) - 1))
    X = Normalizer(copy=False).fit_transform(TruncatedSVD(dims, random_state=seed).fit_transform(tfidf))

    k = k or _pick_k(X, len(texts), k_min, k_max, seed)
    labels = KMeans(n_clusters=k, n_init=10, random_state=seed).fit_predict(X)
    for _ in range(3):                       # 큰 잡동사니 주제가 남지 않을 때까지 최대 3번
        new = _split_big(X, labels, len(texts), seed)
        if (new == labels).all():
            break
        labels = new
    k = int(labels.max()) + 1

    vocab = np.array(vec.get_feature_names_out())
    overall = np.asarray(tfidf.mean(axis=0)).ravel()
    apps = Counter(r.app for r in reviews)
    last = max(date.fromisoformat(r.date) for r in reviews)
    cut = last - timedelta(days=RECENT_DAYS)
    is_recent = np.array([date.fromisoformat(r.date) > cut for r in reviews])
    n_recent, n_old = int(is_recent.sum()), int((~is_recent).sum())

    themes: list[Theme] = []
    for c in range(k):
        idx = np.where(labels == c)[0]
        if len(idx) == 0:
            continue
        # 이 주제에서 유난히 많이 나오는 단어 (주제 평균 - 전체 평균)
        lift = np.asarray(tfidf[idx].mean(axis=0)).ravel() - overall
        kw = [vocab[i] for i in np.argsort(-lift)[:10] if lift[i] > 0]
        # 대표 리뷰: 중심에 가깝고 너무 짧지 않은 것
        d = np.linalg.norm(X[idx] - X[idx].mean(axis=0), axis=1)
        order = [idx[i] for i in np.argsort(d)]
        samples, seen = [], set()
        for i in order:
            key = re.sub(r"\W", "", reviews[i].text)[:40]      # 거의 같은 리뷰는 한 번만
            if key in seen or not 15 <= len(reviews[i].text) <= 400:
                continue
            seen.add(key)
            samples.append(reviews[i])
            if len(samples) == 6:
                break
        samples = samples or [reviews[i] for i in order[:6]]
        members = [reviews[i] for i in idx]

        growth = None
        if n_recent >= 20 and n_old >= 20:
            r_share = (is_recent[idx].sum() + 1) / (n_recent + 1)
            o_share = ((~is_recent[idx]).sum() + 1) / (n_old + 1)
            growth = float(r_share / o_share)

        by_app = {a: sum(1 for m in members if m.app == a) / cnt for a, cnt in apps.items()}
        themes.append(Theme(
            id=c, keywords=kw, reviews=members, samples=samples,
            extra={"recent_cut": cut.isoformat(), "n_recent": n_recent, "n_old": n_old, "n_total": len(reviews),
                   "apps": dict(apps)},
            share=len(idx) / len(reviews),
            avg_likes=float(np.mean([m.likes for m in members])),
            growth=growth, by_app=by_app,
            cross_app=sum(1 for v in by_app.values() if v >= 0.08),
        ))

    score_themes(themes)
    return themes


def score_themes(themes: list[Theme]) -> None:
    multi = len(themes[0].extra["apps"]) > 1 if themes else False
    for t in themes:
        g = min(max(t.growth, 0.5), 2.0) if t.growth else 1.0
        t.score = (t.share * 100) * (1 + math.log1p(t.avg_likes) / 3) * g * \
                  ((1 + 0.25 * (t.cross_app - 1)) if multi and t.cross_app > 1 else 1.0)
        if t.extra.get("fixable") is False:
            t.score *= 0.5           # 기존 앱의 버그·정책 문제는 새 회사가 끼어들 틈이 적다
    themes.sort(key=lambda t: -t.score)


def merge_themes(themes: list[Theme], same_as: dict[int, int]) -> list[Theme]:
    """same_as = {흡수될 주제 id: 남길 주제 id}. 합친 뒤 수치를 다시 계산한다."""
    by_id = {t.id: t for t in themes}

    def root(i):
        seen = set()
        while i in same_as and same_as[i] in by_id and i not in seen:
            seen.add(i)
            i = same_as[i]
        return i

    groups: dict[int, list[Theme]] = {}
    for t in themes:
        groups.setdefault(root(t.id), []).append(t)
    out = []
    for rid, parts in groups.items():
        keep = by_id[rid]
        if len(parts) == 1:
            out.append(keep)
            continue
        ex = keep.extra
        members = [r for p in parts for r in p.reviews]
        cut = date.fromisoformat(ex["recent_cut"])
        rec = sum(1 for r in members if date.fromisoformat(r.date) > cut)
        growth = None
        if ex["n_recent"] >= 20 and ex["n_old"] >= 20:
            growth = float(((rec + 1) / (ex["n_recent"] + 1)) / ((len(members) - rec + 1) / (ex["n_old"] + 1)))
        by_app = {a: sum(1 for m in members if m.app == a) / cnt for a, cnt in ex["apps"].items()}
        kw = list(dict.fromkeys(w for p in sorted(parts, key=lambda p: -len(p.reviews)) for w in p.keywords))[:10]
        samples = [s for p in sorted(parts, key=lambda p: -len(p.reviews)) for s in p.samples[:3]][:6]
        out.append(Theme(id=rid, keywords=kw, reviews=members, samples=samples,
                         share=len(members) / ex["n_total"], avg_likes=float(np.mean([m.likes for m in members])),
                         growth=growth, by_app=by_app, cross_app=sum(1 for v in by_app.values() if v >= 0.08),
                         label=keep.label, pain=keep.pain, opportunity=keep.opportunity, extra=dict(ex)))
    score_themes(out)
    return out
