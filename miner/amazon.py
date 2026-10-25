"""아마존 공개 리뷰 데이터(Amazon Reviews 2023, UC 샌디에이고 McAuley 연구실)로 '불만 많은 상품 종류'를 찾는다.

데이터: https://amazon-reviews-2023.github.io/  (연구용 공개, 2023년 9월까지의 리뷰)
리뷰 파일 필드: rating, title, text, asin, parent_asin, timestamp(ms), helpful_vote, verified_purchase
상품 파일 필드: parent_asin, title, main_category, average_rating, rating_number, price, store, categories
"""
from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
import shutil
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from .sources import Review
from .textproc import _en_stop

MIRRORS = [
    "https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023/resolve/main/raw/{kind}_categories/{name}.jsonl",
    "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/{kind}_categories/{name}.jsonl.gz",
]

# 상품 종류를 가리키지 않는 흔한 말 (상품명 두 단어 조합에서 뺀다)
TITLE_STOP = {
    "pack", "count", "set", "piece", "pcs", "women", "men", "woman", "man", "girl", "girls", "boy", "kid", "kids",
    "size", "color", "black", "white", "pink", "red", "blue", "gold", "silver", "natural", "organic", "new",
    "free", "best", "premium", "professional", "gift", "travel", "mini", "large", "small", "long", "oz", "fl",
    "ml", "inch", "inches", "ounce", "ounces", "lb", "with", "for", "and", "the", "of", "in", "to", "by",
}

# 상품 자체가 아니라 배송·판매자 문제를 가리키는 말
SELLER_WORDS = {
    "shipping", "shipped", "delivery", "delivered", "arrived", "arrive", "package", "packaging", "box",
    "seller", "refund", "return", "returned", "late", "damaged", "broken", "missing", "order", "ordered",
    "배송", "포장", "택배", "판매자", "환불", "반품", "교환", "파손",
}


def download(kind: str, name: str, dest_dir: str) -> str:
    """kind = 'review' | 'meta'. 이미 있으면 다시 받지 않는다. 받은 파일 경로를 돌려준다."""
    os.makedirs(dest_dir, exist_ok=True)
    fname = name if kind == "review" else f"meta_{name}"
    path = os.path.join(dest_dir, fname + ".jsonl")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    errors = []
    for tpl in MIRRORS:
        url = tpl.format(kind=kind, name=fname)
        try:
            print(f"  내려받는 중: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "complaint-miner/0.2"})
            with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f:
                src = gzip.GzipFile(fileobj=r) if url.endswith(".gz") else r
                shutil.copyfileobj(src, f, length=1 << 20)
            os.replace(path + ".part", path)
            print(f"  완료: {os.path.getsize(path) / 1e6:,.0f}MB")
            return path
        except Exception as e:                      # 다음 미러로
            errors.append(f"{url}: {e}")
            if os.path.exists(path + ".part"):
                os.remove(path + ".part")
    raise RuntimeError("아마존 데이터를 받지 못했습니다.\n" + "\n".join(errors))


def _iter_jsonl(path: str):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


@dataclass
class Niche:
    term: str
    products: int
    reviews: int
    negatives: int
    neg_share: float
    neg_helpful: float          # 불만 리뷰의 평균 '도움됨' 수
    growth: float | None        # 최근 2년 불만 비중 ÷ 그 이전
    avg_price: float | None
    score: float = 0.0
    similar: list | None = None     # 같은 묶음으로 합친 비슷한 이름들


def _singular(w: str) -> str:
    if len(w) > 4 and w.endswith(("shes", "ches", "xes", "sses")):
        return w[:-2]                      # eyelashes→eyelash, brushes→brush
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"                # accessories→accessory
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _title_terms(title: str, store: str = "") -> set[str]:
    words = [w for w in re.findall(r"[a-z]+", title.lower()) if len(w) > 2]
    stop = _en_stop() | TITLE_STOP | set(re.findall(r"[a-z]+", store.lower()))   # 브랜드명은 상품 종류가 아니다
    words = [_singular(w) for w in words]
    return {f"{a} {b}" for a, b in zip(words, words[1:]) if a not in stop and b not in stop}


def load_products(meta_path: str, exclude: list[str] | None = None) -> dict[str, dict]:
    """exclude: 상품명에 이 말이 들어간 상품은 뺀다 (예: ['human hair', 'wig'])."""
    ex = [e.lower() for e in (exclude or [])]
    out = {}
    for m in _iter_jsonl(meta_path):
        pid = m.get("parent_asin")
        if not pid or not m.get("title"):
            continue
        if ex and any(e in m["title"].lower() for e in ex):
            continue
        price = m.get("price")
        try:
            price = float(price) if price not in (None, "", "None") else None
        except (TypeError, ValueError):
            price = None
        out[pid] = {"title": m["title"], "price": price, "store": m.get("store") or "",
                    "terms": _title_terms(m["title"], m.get("store") or "")}
    return out


def scan_niches(review_path: str, products: dict[str, dict], min_products: int = 15,
                min_reviews: int = 1500, top: int = 20) -> tuple[list[Niche], dict]:
    """상품명 두 단어 조합(예: 'hair oil', 'eye cream')을 상품 종류로 보고, 종류별 불만 비율을 센다."""
    term_products: dict[str, set] = defaultdict(set)
    for pid, p in products.items():
        for t in p["terms"]:
            term_products[t].add(pid)
    terms = {t for t, ps in term_products.items() if len(ps) >= min_products}
    pid_terms = defaultdict(list)
    for t in terms:
        for pid in term_products[t]:
            pid_terms[pid].append(t)

    last_ts = 0
    stats = defaultdict(lambda: [0, 0, 0, 0, 0, 0])   # reviews, neg, neg_helpful, neg_recent, all_recent, (unused)
    rows = []
    for r in _iter_jsonl(review_path):
        last_ts = max(last_ts, r.get("timestamp") or 0)
        rows.append((r.get("parent_asin"), r.get("rating"), r.get("helpful_vote") or 0, r.get("timestamp") or 0))
    cut = last_ts - 2 * 365 * 24 * 3600 * 1000
    for pid, rating, helpful, ts in rows:
        for t in pid_terms.get(pid, ()):
            s = stats[t]
            s[0] += 1
            recent = ts > cut
            s[4] += recent
            if rating is not None and rating <= 2:
                s[1] += 1
                s[2] += helpful
                s[3] += recent

    # 카테고리 전체의 불만 비율도 시간이 지나며 변한다. 니치의 추세는 그 평균과 비교해야 의미가 있다.
    tot_rec = sum(1 for _, _, _, ts in rows if ts > cut)
    tot_neg_rec = sum(1 for _, rt, _, ts in rows if ts > cut and rt is not None and rt <= 2)
    tot_neg = sum(1 for _, rt, _, _ in rows if rt is not None and rt <= 2)
    base_growth = ((tot_neg_rec + 1) / (tot_rec + 1)) / ((tot_neg - tot_neg_rec + 1) / (len(rows) - tot_rec + 1))

    niches = []
    for t, (n, neg, nh, neg_rec, all_rec, _) in stats.items():
        if n < min_reviews or neg < 50:
            continue
        old_all, old_neg = n - all_rec, neg - neg_rec
        growth = None
        if all_rec >= 200 and old_all >= 200:
            growth = ((neg_rec + 1) / (all_rec + 1)) / ((old_neg + 1) / (old_all + 1)) / base_growth
        prices = [products[p]["price"] for p in term_products[t] if products[p]["price"]]
        niches.append(Niche(t, len(term_products[t]), n, neg, neg / n, nh / neg if neg else 0.0, growth,
                            sum(prices) / len(prices) if prices else None))
    for x in niches:
        g = min(max(x.growth, 0.5), 2.0) if x.growth else 1.0
        # 큰 시장(리뷰 수) × 높은 불만 비율 × 공감 × 늘어나는 추세
        x.score = x.neg_share * 100 * math.log10(x.reviews) * (1 + math.log1p(x.neg_helpful) / 4) * g
    niches.sort(key=lambda x: -x.score)
    # 단어가 얼마나 흔한지: 'hair', 'oil', 'cream'처럼 상품 종류를 뜻하는 말은 많은 상품명에 나온다
    word_df = Counter(w for p in products.values() for t in p["terms"] for w in set(t.split()))

    def generic(term: str) -> int:
        return min(word_df[w] for w in term.split())

    def overlap(a: str, b: str) -> float:
        pa, pb = term_products[a], term_products[b]
        return len(pa & pb) / min(len(pa), len(pb))

    # 거의 같은 상품 묶음은 하나로: 점수가 비슷하면(90% 이상) 더 '상품 종류다운' 이름을 고른다
    picked: list[Niche] = []
    used: set[str] = set()
    for x in niches:
        if x.term in used:
            continue
        group = [y for y in niches if y.term not in used and
                 (overlap(x.term, y.term) >= 0.6 or
                  (set(x.term.split()) & set(y.term.split()) and overlap(x.term, y.term) >= 0.3))]
        best = max((y for y in group if y.score >= 0.9 * x.score), key=lambda y: generic(y.term))
        used.update(y.term for y in group)
        best.similar = [y.term for y in group if y.term != best.term][:4]
        picked.append(best)
        if len(picked) == top:
            break
    meta = {"base_growth": base_growth, "last_date": datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).date().isoformat(),
            "n_reviews": len(rows), "n_products": len(products), "term_products": term_products}
    return picked, meta


def niche_reviews(review_path: str, products: dict[str, dict], pids: set, max_rating: int = 2,
                  limit: int = 4000) -> list[Review]:
    """한 니치(상품 묶음)의 불만 리뷰를 Review로 돌려준다. 너무 많으면 최근 것부터 limit개."""
    out = []
    for r in _iter_jsonl(review_path):
        pid = r.get("parent_asin")
        if pid not in pids or r.get("rating") is None or r["rating"] > max_rating:
            continue
        text = ((r.get("title") or "") + ". " + (r.get("text") or "")).strip(". ").strip()
        if len(text) < 15:
            continue
        ts = r.get("timestamp") or 0
        date = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
        store = products[pid]["store"] or products[pid]["title"][:30]
        out.append(Review("amazon", store, text, int(r["rating"]), date, int(r.get("helpful_vote") or 0)))
    out.sort(key=lambda r: r.date, reverse=True)
    return out[:limit]


def is_seller_theme(keywords: list[str]) -> bool:
    """주제 키워드 상위 5개 중 2개 이상이 배송·판매자 말이면 '상품 자체 문제가 아님'으로 본다."""
    top = [w for k in keywords[:5] for w in k.split()]
    return sum(1 for w in top if w.removeprefix("not_") in SELLER_WORDS) >= 2
