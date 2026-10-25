"""파이프라인 테스트. 아래 리뷰는 테스트용으로 만든 가짜 문장이며 실제 데이터가 아니다."""
import random
from datetime import date, datetime, timedelta

from miner import sources
from miner.analyze import find_themes
from miner.report import write_csv, write_html
from miner.sources import Review
from miner.textproc import tokenize

TEMPLATES = {
    "sync": ["은행 연동이 자꾸 끊겨요 {x}", "카드 연동 오류 때문에 내역이 안 들어와요 {x}", "계좌 연동이 매번 풀려서 다시 로그인해야 함 {x}",
             "은행 연동 끊김 진짜 짜증나요 {x}"],
    "ads": ["광고가 너무 많아서 못 쓰겠어요 {x}", "광고 보기 싫으면 유료 결제하라네요 {x}", "화면 절반이 광고 배너 {x}",
            "전면 광고 때문에 기록하다 끊겨요 {x}"],
    "slow": ["앱 실행이 너무 느려요 로딩 {x}", "로딩이 길고 자꾸 멈춰요 {x}", "업데이트 후 속도가 느려짐 버벅임 {x}",
             "켜는 데만 10초 걸려요 로딩 {x}"],
    "category": ["카테고리 분류가 엉망이에요 {x}", "지출 카테고리 자동 분류가 다 틀림 {x}", "카테고리를 직접 수정해도 저장이 안 돼요 {x}",
                 "분류 기준을 바꿀 수가 없어요 카테고리 {x}"],
}
TAILS = ["", "ㅠㅠ", "고쳐주세요", "별 하나도 아까움", "삭제합니다", "실망", "다른 앱 갈게요"]


def fake_reviews(n_per=40, seed=0):
    rnd = random.Random(seed)
    out, truth = [], []
    today = date(2026, 10, 24)
    for theme, temps in TEMPLATES.items():
        for i in range(n_per):
            app = "가계부A" if i % 2 else "가계부B"
            days = rnd.randint(0, 60) if theme == "sync" and i % 3 else rnd.randint(0, 300)
            out.append(Review("csv", app, rnd.choice(temps).format(x=rnd.choice(TAILS)), rnd.choice([1, 2]),
                              (today - timedelta(days=days)).isoformat(), rnd.randint(0, 30)))
            truth.append(theme)
    return out, truth


def test_tokenize_keeps_meaning():
    toks = tokenize("은행 연동이 자꾸 끊겨서 너무 불편해요")
    assert "은행" in toks and "연동" in toks and "끊기다" in toks
    assert "너무" not in toks


def test_themes_recover_planted_groups(tmp_path):
    reviews, truth = fake_reviews()
    themes = find_themes(reviews, k=4)
    assert len(themes) == 4
    # 각 주제가 대부분 한 가지 심어둔 그룹으로 이루어져야 한다
    lookup = {id(r): t for r, t in zip(reviews, truth)}
    purities = []
    for t in themes:
        counts = {}
        for r in t.reviews:
            counts[lookup[id(r)]] = counts.get(lookup[id(r)], 0) + 1
        purities.append(max(counts.values()) / len(t.reviews))
    assert sum(purities) / len(purities) > 0.8, purities
    # 최근에 몰리게 심어둔 '연동' 불만은 증가율이 1보다 커야 한다
    sync = max(themes, key=lambda t: sum(1 for r in t.reviews if "연동" in r.text))
    assert sync.growth and sync.growth > 1.2
    assert sync.cross_app == 2

    out = tmp_path / "r.html"
    meta = {"query": "테스트", "n_neg": len(reviews), "n_all": len(reviews), "max_rating": 2,
            "date_from": "2026-01-01", "date_to": "2026-10-24", "apps": {"가계부A": 80, "가계부B": 80}}
    write_html(str(out), themes, [], meta)
    write_csv(str(tmp_path / "r.csv"), themes)
    html = out.read_text(encoding="utf-8")
    assert "창업 기회 점수 순위" in html and "가계부A" in html


def test_auto_k_runs():
    reviews, _ = fake_reviews(n_per=60, seed=1)
    themes = find_themes(reviews)
    assert 4 <= len(themes) <= 12


def test_too_few_reviews():
    import pytest
    with pytest.raises(ValueError):
        find_themes(fake_reviews(n_per=3)[0])


def test_appstore_parser(monkeypatch):
    feed = {"feed": {"entry": [
        {"im:name": {"label": "테스트앱"}},
        {"im:rating": {"label": "1"}, "title": {"label": "최악"}, "content": {"label": "광고가 너무 많아요"},
         "updated": {"label": "2026-10-20T10:00:00-07:00"}, "im:voteSum": {"label": "3"}},
    ]}}
    calls = []

    def fake_get(url):
        calls.append(url)
        return feed if len(calls) == 1 else {"feed": {}}

    monkeypatch.setattr(sources, "_get_json", fake_get)
    got = sources.fetch_appstore("123", pages=3)
    assert len(got) == 1 and got[0].rating == 1 and got[0].likes == 3 and got[0].date == "2026-10-20"
    assert "광고" in got[0].text


def test_csv_loader(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("text,rating,date\n배송이 너무 늦어요,1,2026-10-01\n,2,2026-10-02\n좋아요,5,2026-10-03\n", encoding="utf-8")
    got = sources.load_csv(str(p), app="쇼핑몰")
    assert len(got) == 2 and got[0].app == "쇼핑몰"


def test_merge_themes_recomputes():
    from miner.analyze import merge_themes
    reviews, _ = fake_reviews()
    themes = find_themes(reviews, k=6)
    a, b = themes[0], themes[1]
    total = len(a.reviews) + len(b.reviews)
    merged = merge_themes(themes, {b.id: a.id})
    assert len(merged) == 5
    m = next(t for t in merged if t.id == a.id)
    assert len(m.reviews) == total
    assert abs(sum(t.share for t in merged) - 1.0) < 1e-9


def test_llm_labels_and_merge(monkeypatch):
    """Claude 응답을 흉내 낸 가짜 객체로 연동 코드를 검사한다 (실제 API는 호출하지 않음)."""
    import json
    import types
    import anthropic
    from miner import llm

    reviews, _ = fake_reviews()
    themes = find_themes(reviews, k=4)
    ids = [t.id for t in themes]
    payload = {
        "themes": [{"id": i, "label": f"주제{i}", "pain": "p", "who": "w", "opportunity": "o",
                    "is_fixable_by_newcomer": i != ids[3], "same_as": ids[0] if i == ids[1] else -1} for i in ids],
        "top_hypotheses": [{"hypothesis": "h", "evidence": "e", "cheapest_test": "c", "risk": "r"}],
    }
    seen = {}

    class FakeMessages:
        def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(stop_reason="end_turn",
                                         content=[types.SimpleNamespace(type="text", text=json.dumps(payload))])

    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = types.SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    hyps, same_as = llm.label_themes(themes)
    assert seen["model"] == "claude-opus-5-5" and seen["fallbacks"] == "default"
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert len(hyps) == 1 and same_as == {ids[1]: ids[0]}
    assert themes[0].label == f"주제{ids[0]}"
    assert next(t for t in themes if t.id == ids[3]).extra["fixable"] is False


def _fake_amazon(tmp_path):
    """아마존 데이터 형식을 흉내 낸 가짜 파일 (테스트용, 실제 데이터 아님)."""
    import json
    rnd = random.Random(5)
    meta, revs = [], []
    kinds = {
        "hair oil": ["greasy and heavy", "smell is too strong perfume", "pump broke and leaked", "made my hair frizzy dry"],
        "eye cream": ["burned my eyes stinging", "no difference wrinkles", "tiny jar too expensive", "pilled under makeup"],
        "lip balm": ["love it", "great moisture", "nice", "works well"],
    }
    pid = 0
    for kind, complaints in kinds.items():
        for i in range(20):
            pid += 1
            p = f"P{pid}"
            title = f"Brand{i} {rnd.choice(['Argan', 'Rosemary', 'Vitamin', 'Jojoba', 'Coconut'])} {kind.title()} " \
                    f"{rnd.choice(['for Dry Skin', 'Travel Size', 'Daily Care', 'Unscented', '2 Pack'])}"
            meta.append({"parent_asin": p, "title": title, "price": 10 + i,
                         "store": f"Brand{i}"})
            for j in range(90):
                bad = kind != "lip balm" and j % 3 == 0
                text = rnd.choice(complaints) + " " + rnd.choice(["", "never again", "returned it", "disappointed"])
                if kind == "hair oil" and j % 9 == 0:
                    text = "package arrived damaged shipping took forever, seller refund"
                ts = int(datetime(2021 + j % 3, 1 + j % 12, 1).timestamp() * 1000)
                revs.append({"parent_asin": p, "rating": 1 if bad else 5, "title": "", "text": text,
                             "timestamp": ts, "helpful_vote": j % 7})
    d = tmp_path / "data"
    d.mkdir()
    (d / "Fake.jsonl").write_text("\n".join(json.dumps(r) for r in revs), encoding="utf-8")
    (d / "meta_Fake.jsonl").write_text("\n".join(json.dumps(m) for m in meta), encoding="utf-8")
    return d


def test_amazon_niches_and_reports(tmp_path):
    from datetime import datetime as _dt  # noqa: F401
    from miner.__main__ import main
    d = _fake_amazon(tmp_path)
    out = tmp_path / "out"
    rc = main(["amazon", "--category", "Fake", "--data-dir", str(d), "--min-reviews", "100", "--deep", "2",
               "--out-dir", str(out), "--no-llm"])
    assert rc == 0
    idx = (out / "index.html").read_text(encoding="utf-8")
    # 불만이 많은 두 종류가 순위에 있고, 불만 없는 lip balm은 없거나 아래에 있어야 한다
    assert "hair oil" in idx and "eye cream" in idx
    assert idx.find("hair oil") < idx.find("lip balm") if "lip balm" in idx else True
    assert (out / "hair-oil.html").exists()
    html = (out / "hair-oil.html").read_text(encoding="utf-8")
    assert "배송·판매자 문제" in html


def test_seller_theme_detection():
    from miner.amazon import is_seller_theme
    assert is_seller_theme(["shipping", "arrived damaged", "package", "smell"])
    assert not is_seller_theme(["greasy", "smell", "heavy", "pump"])


def test_apify_parse_variants():
    """수집 도구마다 다른 출력 형식을 읽는지 (가짜 결과, 실제 호출 없음)."""
    from miner.apify import parse_items
    items = [
        {"reviewTitle": "Dried out", "reviewDescription": "The liner tube was completely dried out.",
         "ratingScore": 1, "date": "Reviewed in the United States on March 3, 2025",
         "reviewReaction": "12 people found this helpful", "productTitle": "Magnetic Eyeliner Kit"},
        {"title": "Meh", "text": "Lashes fall off after an hour", "rating": "2.0 out of 5 stars",
         "reviewDate": "2026-01-15", "helpfulVotes": 3, "asin": "B0TEST"},
        {"text": "no rating here"},
    ]
    got = parse_items(items)
    assert len(got) == 2
    assert got[0].rating == 1 and got[0].date == "2025-03-03" and got[0].likes == 12
    assert got[0].text.startswith("Dried out.")
    assert got[1].rating == 2 and got[1].date == "2026-01-15" and got[1].app == "B0TEST"


def test_live_command_with_fake_apify(monkeypatch, tmp_path):
    import json
    from miner import apify
    from miner.__main__ import main
    reviews, _ = fake_reviews(n_per=30)
    items = [{"reviewDescription": r.text, "ratingScore": r.rating, "date": r.date, "productTitle": r.app}
             for r in reviews]
    calls = []

    def fake_run(actor, inp, token, max_wait=1800):
        calls.append(inp)
        return items

    monkeypatch.setattr(apify, "run_actor", fake_run)
    monkeypatch.setenv("APIFY_TOKEN", "test")
    out = tmp_path / "live"
    rc = main(["amazon-live", "--url", "https://www.amazon.com/dp/B0A, https://www.amazon.com/dp/B0B",
               "--check", "연동=연동", "--out-dir", str(out)])
    assert rc == 0 and (out / "report.html").exists()
    assert calls[0]["productUrls"] == [{"url": "https://www.amazon.com/dp/B0A"}, {"url": "https://www.amazon.com/dp/B0B"}]
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert s["checks"]["연동"]["hits"] > 0
