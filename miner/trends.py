"""지금 잘 팔리는데 불만이 많은 아마존 제품을 찾는다 (APIFY_TOKEN 필요).

1. 여러 분야의 아마존 베스트셀러 목록을 모은다 (junglee/amazon-bestsellers)
2. '잘 팔리는데 별점이 낮은' 제품을 고른다
3. 그 제품들의 1~2점 리뷰를 모은다 (junglee/amazon-reviews-scraper)
4. 제품마다 불만 주제를 묶는다
"""
from __future__ import annotations

import math
import re
import urllib.error
from dataclasses import dataclass, field

from . import apify
from .sources import Review

BESTSELLER_ACTOR = "junglee~amazon-bestsellers"

DEFAULT_CATEGORIES = {
    "뷰티 전체": "https://www.amazon.com/Best-Sellers-Beauty-Personal-Care/zgbs/beauty",
    "스킨케어": "https://www.amazon.com/Best-Sellers-Beauty-Personal-Care-Skin-Care-Products/zgbs/beauty/11060451",
    "헤어케어": "https://www.amazon.com/Best-Sellers-Beauty-Personal-Care-Hair-Care-Products/zgbs/beauty/11057241",
    "메이크업": "https://www.amazon.com/Best-Sellers-Beauty-Personal-Care-Makeup/zgbs/beauty/11058281",
    "생활·건강": "https://www.amazon.com/Best-Sellers-Health-Household/zgbs/hpc",
    "반려동물": "https://www.amazon.com/Best-Sellers-Pet-Supplies/zgbs/pet-supplies",
    "주방·생활": "https://www.amazon.com/Best-Sellers-Home-Kitchen/zgbs/home-garden",
    "유아": "https://www.amazon.com/Best-Sellers-Baby/zgbs/baby-products",
}


@dataclass
class Product:
    category: str
    name: str
    url: str
    asin: str
    position: int
    stars: float | None
    reviews: int
    price: float | None
    score: float = 0.0
    negatives: list[Review] = field(default_factory=list)


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return _num(v.get("value") or v.get("amount"))
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", str(v))
    return float(m.group(1).replace(",", "")) if m else None


def _asin(url: str, item: dict) -> str:
    a = item.get("asin") or item.get("ASIN")
    if a:
        return str(a)
    m = re.search(r"/dp/([A-Z0-9]{10})", url or "")
    return m.group(1) if m else ""


def parse_bestsellers(items: list[dict], cat_of: dict[str, str]) -> list[Product]:
    out = []
    for i, it in enumerate(items):
        name = apify._first(it, "name", "title", "productName", "productTitle")
        url = apify._first(it, "url", "productUrl", "link")
        if not name or not url:
            continue
        if not str(url).startswith("http"):
            url = "https://www.amazon.com" + str(url)
        src = apify._first(it, "categoryUrl", "startUrl", "inputUrl", "input") or ""
        if isinstance(src, dict):
            src = src.get("url", "")
        cat = next((c for c, u in cat_of.items() if src and (src.rstrip("/") in u or u.rstrip("/") in src)), None) \
            or str(apify._first(it, "categoryName", "category") or "기타")
        pos = _num(apify._first(it, "position", "rank", "bestsellerRank", "rankPosition")) or (i % 100) + 1
        out.append(Product(cat, str(name)[:140], str(url).split("?")[0], _asin(str(url), it), int(pos),
                           _num(apify._first(it, "stars", "rating", "averageRating", "productRating")),
                           int(_num(apify._first(it, "reviewsCount", "numberOfReviews", "ratingsCount",
                                                 "reviewCount", "totalReviews")) or 0),
                           _num(apify._first(it, "price", "priceValue", "currentPrice"))))
    return out


def fetch_bestsellers(cat_urls: dict[str, str], token: str, per_category: int = 50) -> tuple[list[Product], list[dict]]:
    urls = list(cat_urls.values())
    candidates = [
        {"categoryUrls": urls, "maxItemsPerStartUrl": per_category, "depthOfCrawl": 1},
        {"categoryUrls": [{"url": u} for u in urls], "maxItemsPerStartUrl": per_category, "depthOfCrawl": 1},
    ]
    errors = []
    for inp in candidates:
        try:
            items = apify.run_actor(BESTSELLER_ACTOR, inp, token)
        except urllib.error.HTTPError as e:
            errors.append(f"{e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
            if e.code in (400, 422):
                continue
            raise
        return parse_bestsellers(items, cat_urls), items
    raise RuntimeError("베스트셀러를 모으지 못했습니다.\n" + "\n".join(errors))


def pick_candidates(products: list[Product], n: int = 12, per_category: int = 3, min_reviews: int = 300,
                    max_stars: float = 4.4) -> list[Product]:
    """잘 팔리는데(순위 높음, 리뷰 많음) 별점이 낮은 제품을 고른다."""
    seen = set()
    pool = []
    for p in products:
        if p.asin in seen or p.stars is None or p.reviews < min_reviews or p.stars > max_stars:
            continue
        seen.add(p.asin)
        demand = (101 - min(p.position, 100)) / 100 * math.log10(p.reviews)
        p.score = demand * (4.8 - p.stars)
        pool.append(p)
    pool.sort(key=lambda p: -p.score)
    picked, per = [], {}
    for p in pool:
        if per.get(p.category, 0) >= per_category:
            continue
        picked.append(p)
        per[p.category] = per.get(p.category, 0) + 1
        if len(picked) == n:
            break
    return picked


def attach_reviews(products: list[Product], reviews: list[Review]) -> None:
    """수집한 리뷰를 ASIN으로 제품에 나눠 붙이고, 리뷰의 '앱' 이름을 제품 이름으로 바꾼다."""
    by_asin = {p.asin: p for p in products if p.asin}
    for r in reviews:
        p = by_asin.get(r.app)
        if p is not None:
            r.app = p.name[:60]
            p.negatives.append(r)
