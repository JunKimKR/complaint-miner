"""리뷰 수집: 구글 플레이, 앱스토어, CSV 파일."""
from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass
class Review:
    source: str          # "play" | "appstore" | "csv"
    app: str             # 앱 이름(사람이 읽는 이름)
    text: str
    rating: int          # 1~5
    date: str            # ISO 날짜 "2026-10-24"
    likes: int = 0       # 공감 수(구글 플레이 '도움됨'), 없으면 0

    def to_dict(self):
        return asdict(self)


# ── 구글 플레이 ──────────────────────────────────────────────

def search_play(query: str, lang: str = "ko", country: str = "kr", n: int = 10):
    from google_play_scraper import search
    hits = search(query, lang=lang, country=country, n_hits=n)
    return [{"store": "play", "id": h["appId"], "title": h["title"], "score": h.get("score")} for h in hits]


def fetch_play(app_id: str, count: int = 2000, lang: str = "ko", country: str = "kr") -> list[Review]:
    """최신순으로 리뷰 count개를 가져온다(별점 필터는 나중에)."""
    from google_play_scraper import Sort, reviews, app as app_info
    title = app_info(app_id, lang=lang, country=country).get("title", app_id)
    out: list[Review] = []
    token = None
    while len(out) < count:
        batch, token = reviews(app_id, lang=lang, country=country, sort=Sort.NEWEST,
                               count=min(200, count - len(out)), continuation_token=token)
        if not batch:
            break
        for r in batch:
            if not r.get("content"):
                continue
            out.append(Review("play", title, r["content"].strip(), int(r["score"]),
                              r["at"].date().isoformat(), int(r.get("thumbsUpCount") or 0)))
        if token is None or token.token is None:
            break
        time.sleep(0.5)
    return out


# ── 애플 앱스토어 (공개 RSS, 최대 500개) ─────────────────────

def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "complaint-miner/0.1"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def search_appstore(query: str, country: str = "kr", n: int = 10):
    q = urllib.parse.quote(query)
    data = _get_json(f"https://itunes.apple.com/search?term={q}&country={country}&entity=software&limit={n}")
    return [{"store": "appstore", "id": str(a["trackId"]), "title": a["trackName"],
             "score": a.get("averageUserRating")} for a in data.get("results", [])]


def fetch_appstore(app_id: str, country: str = "kr", pages: int = 10) -> list[Review]:
    out: list[Review] = []
    title = app_id
    for page in range(1, pages + 1):
        url = f"https://itunes.apple.com/{country}/rss/customerreviews/page={page}/id={app_id}/sortby=mostrecent/json"
        try:
            feed = _get_json(url).get("feed", {})
        except Exception:
            break
        entries = feed.get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        got = 0
        for e in entries:
            if "im:rating" not in e:          # 첫 항목이 앱 정보인 경우
                title = e.get("im:name", {}).get("label", title)
                continue
            text = (e.get("title", {}).get("label", "") + " " + e.get("content", {}).get("label", "")).strip()
            date = e.get("updated", {}).get("label", "")[:10]
            out.append(Review("appstore", title, text, int(e["im:rating"]["label"]), date,
                              int(e.get("im:voteSum", {}).get("label", 0) or 0)))
            got += 1
        if got == 0:
            break
        time.sleep(0.5)
    for r in out:
        r.app = title
    return out


# ── CSV (직접 모은 리뷰, 설문, 커뮤니티 글 등) ────────────────

def load_csv(path: str, app: str | None = None, text_col: str = "text", rating_col: str = "rating",
             date_col: str = "date", likes_col: str = "likes", app_col: str = "app") -> list[Review]:
    """열 이름이 다르면 인자로 바꿔 지정. 별점이 없는 데이터(커뮤니티 글 등)는 1점으로 간주한다."""
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            text = (row.get(text_col) or "").strip()
            if not text:
                continue
            rating = int(float(row[rating_col])) if row.get(rating_col) else 1
            date = (row.get(date_col) or datetime.now(timezone.utc).date().isoformat())[:10]
            likes = int(float(row[likes_col])) if row.get(likes_col) else 0
            out.append(Review("csv", app or row.get(app_col) or path, text, rating, date, likes))
    return out
