"""Apify로 아마존 최신 리뷰를 모은다 (APIFY_TOKEN 필요).

기본 수집 도구: junglee/amazon-reviews-scraper
- 입력: productUrls(주소 목록), maxReviews(별점별 최대 100), filterByRatings
- 수집 도구의 출력 필드 이름은 도구마다 달라서, 여러 후보 이름 중 있는 것을 쓴다.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime

from .sources import Review

API = "https://api.apify.com/v2"
DEFAULT_ACTOR = "junglee~amazon-reviews-scraper"

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def _call(method: str, url: str, token: str, body: dict | None = None, timeout: int = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def run_actor(actor: str, run_input: dict, token: str, max_wait: int = 1800) -> list[dict]:
    """수집 도구를 실행하고 끝날 때까지 기다린 뒤 결과 목록을 돌려준다."""
    run = _call("POST", f"{API}/acts/{actor}/runs", token, run_input)["data"]
    run_id = run["id"]
    started = time.time()
    while run["status"] in ("READY", "RUNNING"):
        if time.time() - started > max_wait:
            raise TimeoutError(f"Apify 실행이 {max_wait}초 안에 끝나지 않았습니다 (run {run_id})")
        time.sleep(10)
        run = _call("GET", f"{API}/actor-runs/{run_id}", token)["data"]
    if run["status"] != "SUCCEEDED":
        raise RuntimeError(f"Apify 실행 실패: {run['status']} (run {run_id})")
    items = _call("GET", f"{API}/datasets/{run['defaultDatasetId']}/items?format=json&clean=true", token,
                  timeout=120)
    return items if isinstance(items, list) else []


# ── 출력 해석: 도구마다 필드 이름이 달라서 후보를 차례로 본다 ──

def _first(item: dict, *keys):
    for k in keys:
        v = item.get(k)
        if v not in (None, "", []):
            return v
    return None


def _rating(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    m = re.search(r"(\d(?:\.\d)?)", str(v))
    return int(round(float(m.group(1)))) if m else None


def _date(v) -> str:
    if not v:
        return datetime.utcnow().date().isoformat()
    s = str(v)
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)          # "March 3, 2025"
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s)          # "3 March 2025"
    if m and m.group(2).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return datetime.utcnow().date().isoformat()


def _helpful(v) -> int:
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).lower()
    if s.startswith("one person"):
        return 1
    m = re.search(r"([\d,]+)", s)
    return int(m.group(1).replace(",", "")) if m else 0


def parse_items(items: list[dict]) -> list[Review]:
    out = []
    for it in items:
        if it.get("error"):                    # 리뷰가 없는 상품은 오류 항목만 온다
            continue
        text = _first(it, "reviewDescription", "text", "reviewText", "body", "content", "review", "description")
        title = _first(it, "reviewTitle", "title", "headline")
        rating = _rating(_first(it, "ratingScore", "rating", "stars", "reviewRating", "score"))
        if not text or rating is None:
            continue
        product = _first(it, "productAsin", "asin", "productOriginalAsin", "productTitle", "product_title",
                         "productName", "input", "productUrl", "url") or "아마존 상품"
        if isinstance(product, dict):
            product = product.get("url") or "아마존 상품"
        full = (f"{title}. {text}" if title and title not in text else str(text)).strip()
        out.append(Review("amazon-live", str(product)[:60], full, rating,
                          _date(_first(it, "date", "reviewDate", "reviewedIn", "dateText", "publishedAt")),
                          _helpful(_first(it, "reviewReaction", "helpfulVotes", "helpful", "helpfulCount",
                                          "reactionText"))))
    return out


def fetch_amazon_reviews(urls: list[str], token: str, per_star: int = 100,
                         actor: str = DEFAULT_ACTOR) -> tuple[list[Review], list[dict], dict]:
    """최신 리뷰를 모은다. 입력 형식이 도구 버전마다 조금 달라서 후보를 차례로 시도한다.
    돌려주는 값: (리뷰, 원본 결과, 실제로 통한 입력)"""
    products = [{"url": u} for u in urls]
    candidates = [
        {"productUrls": products, "maxReviews": per_star, "filterByRatings": ["oneStar", "twoStar"]},
        {"productUrls": products, "maxReviews": per_star, "filterByRatings": ["allStars"]},
        {"productUrls": products, "maxReviews": per_star, "filterByRatings": "allStars"},
        {"productUrls": products, "maxReviews": per_star},
    ]
    errors = []
    for inp in candidates:
        try:
            items = run_actor(actor, inp, token)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            errors.append(f"{e.code}: {detail}")
            if e.code in (400, 422):          # 입력 형식이 안 맞음 → 다음 후보
                continue
            raise
        reviews = parse_items(items)
        if reviews:
            return reviews, items, inp
        errors.append(f"결과 {len(items)}개 중 읽을 수 있는 리뷰가 없음 (입력: {list(inp)})")
        if items:                              # 결과는 왔는데 형식을 못 읽음 → 원본을 남겨서 고칠 수 있게
            return [], items, inp
    raise RuntimeError("Apify로 리뷰를 모으지 못했습니다.\n" + "\n".join(errors))
