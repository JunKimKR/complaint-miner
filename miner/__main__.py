"""불만 채굴기 실행 진입점.

사용 예:
  python -m miner search "가계부"
  python -m miner run --play com.example.app --appstore 1234567890 --name "가계부 앱들"
  python -m miner run --csv my_reviews.csv --name "설문 응답"
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

from . import sources
from .analyze import find_themes


def cmd_search(a):
    rows = []
    for fn, label in ((sources.search_play, "구글 플레이"), (sources.search_appstore, "앱스토어")):
        try:
            rows += fn(a.query, n=a.n)
        except Exception as e:
            print(f"! {label} 검색 실패: {e}", file=sys.stderr)
    if not rows:
        print("검색 결과가 없습니다.")
        return 1
    print(f"{'스토어':<9} {'앱 ID':<45} 이름")
    for r in rows:
        flag = "--play" if r["store"] == "play" else "--appstore"
        print(f"{flag:<9} {r['id']:<45} {r['title']}")
    print("\n위 ID를 run 명령에 넣으세요. 경쟁 앱 2~5개를 함께 넣으면 '공통 불만'까지 찾습니다.")
    return 0


def cmd_run(a):
    reviews = []
    for app_id in a.play:
        print(f"· 구글 플레이 {app_id} 리뷰 수집 중…")
        try:
            got = sources.fetch_play(app_id, count=a.count)
            reviews += got
            print(f"  {len(got):,}개")
        except Exception as e:
            print(f"  ! 실패: {e}")
    for app_id in a.appstore:
        print(f"· 앱스토어 {app_id} 리뷰 수집 중…")
        try:
            got = sources.fetch_appstore(app_id)
            reviews += got
            print(f"  {len(got):,}개 (앱스토어는 최근 리뷰 최대 500개까지만 공개됩니다)")
        except Exception as e:
            print(f"  ! 실패: {e}")
    for path in a.csv:
        got = sources.load_csv(path, text_col=a.text_col, rating_col=a.rating_col)
        reviews += got
        print(f"· CSV {path}: {len(got):,}개")

    if not reviews:
        print("모은 리뷰가 없습니다. 앱 ID나 네트워크를 확인하세요.")
        return 1
    neg = [r for r in reviews if r.rating <= a.max_rating]
    print(f"\n불만 리뷰(별점 {a.max_rating}점 이하): {len(neg):,}개 / 전체 {len(reviews):,}개")

    try:
        themes = find_themes(neg, k=a.k)
    except ValueError as e:
        print(f"! {e}")
        return 1
    print(f"주제 {len(themes)}개로 묶었습니다.")

    hypotheses = []
    if not a.no_llm and (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or a.llm):
        print("· Claude로 주제 이름과 창업 가설 정리 중…")
        from .analyze import merge_themes, score_themes
        from .llm import label_themes
        hypotheses, same_as = label_themes(themes)
        if same_as:
            before = len(themes)
            themes = merge_themes(themes, same_as)
            print(f"  같은 불만끼리 합쳐 주제 {before}개 → {len(themes)}개")
        else:
            score_themes(themes)
    else:
        print("· Claude API 키가 없어 키워드로만 이름을 붙입니다 (ANTHROPIC_API_KEY를 설정하면 가설까지 정리).")

    from .report import write_csv, write_html
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    dates = sorted(r.date for r in neg)
    meta = {"query": a.name or ", ".join(Counter(r.app for r in neg)), "n_neg": len(neg), "n_all": len(reviews),
            "max_rating": a.max_rating, "date_from": dates[0], "date_to": dates[-1],
            "apps": dict(Counter(r.app for r in neg).most_common())}
    write_html(a.out, themes, hypotheses, meta)
    csv_path = os.path.splitext(a.out)[0] + ".csv"
    write_csv(csv_path, themes)

    print("\n창업 기회 상위 주제:")
    for i, t in enumerate(themes[:5], 1):
        print(f"  {i}. {t.title}  (비중 {t.share:.0%}, 점수 {t.score:.1f})")
    print(f"\n보고서: {a.out}\n리뷰 원본(주제 표시): {csv_path}")
    return 0


def cmd_amazon(a):
    from . import amazon
    from .report import write_csv, write_html, write_niche_index
    print(f"· 아마존 '{a.category}' 데이터 준비")
    rev = amazon.download("review", a.category, a.data_dir)
    meta_path = amazon.download("meta", a.category, a.data_dir)
    products = amazon.load_products(meta_path, exclude=a.exclude)
    print(f"· 상품 {len(products):,}개 읽음. 상품 종류별 불만 비율 계산 중…")
    niches, meta = amazon.scan_niches(rev, products, min_reviews=a.min_reviews, top=a.niches)
    meta["category"] = a.category
    meta["exclude"] = a.exclude or []
    if not niches:
        print("! 조건에 맞는 상품 종류가 없습니다. --min-reviews를 낮춰 보세요.")
        return 1
    terms = a.term or [n.term for n in niches[:a.deep]]
    os.makedirs(a.out_dir, exist_ok=True)
    links = {}
    use_llm = not a.no_llm and bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    for term in terms:
        pids = meta["term_products"].get(term)
        if not pids:
            print(f"! '{term}'에 해당하는 상품이 없습니다.")
            continue
        reviews = amazon.niche_reviews(rev, products, pids)
        print(f"· '{term}': 불만 리뷰 {len(reviews):,}개 분석")
        try:
            themes = find_themes(reviews, k_max=10)
        except ValueError as e:
            print(f"  ! {e}")
            continue
        hyps = []
        if use_llm:
            from .analyze import merge_themes, score_themes
            from .llm import label_themes
            hyps, same_as = label_themes(themes)
            themes = merge_themes(themes, same_as) if same_as else themes
            score_themes(themes)
        slug = re.sub(r"[^a-z0-9]+", "-", term)
        dates = sorted(r.date for r in reviews)
        m = {"query": f"아마존 {a.category} · '{term}'", "n_neg": len(reviews), "n_all": next(n.reviews for n in niches if n.term == term) if any(n.term == term for n in niches) else len(reviews),
             "max_rating": 2, "date_from": dates[0], "date_to": dates[-1],
             "apps": dict(Counter(r.app for r in reviews).most_common(8))}
        write_html(os.path.join(a.out_dir, f"{slug}.html"), themes, hyps, m)
        write_csv(os.path.join(a.out_dir, f"{slug}.csv"), themes)
        links[term] = f"{slug}.html"
        for i, t in enumerate(themes[:3], 1):
            print(f"    {i}. {t.title} ({t.share:.0%})")
    write_niche_index(os.path.join(a.out_dir, "index.html"), niches, meta, links)
    print(f"\n상품 종류 순위: {os.path.join(a.out_dir, 'index.html')}")
    for i, n in enumerate(niches[:10], 1):
        print(f"  {i:>2}. {n.term:<22} 불만 {n.neg_share:5.1%}  리뷰 {n.reviews:>7,}  점수 {n.score:.1f}")
    return 0


def cmd_live(a):
    """Apify로 아마존 상품의 최신 리뷰를 모아 불만을 분석한다."""
    import json
    from . import apify
    from .report import write_csv, write_html
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("! APIFY_TOKEN이 없습니다. GitHub 저장소 Settings → Secrets → Actions에 APIFY_TOKEN을 넣어 주세요.")
        return 1
    urls = [u.strip() for x in a.url for u in re.split(r"[,\s]+", x) if u.strip()]
    if not urls:
        print("! 상품 주소(--url)를 넣어 주세요.")
        return 1
    print(f"· 아마존 상품 {len(urls)}개의 최신 리뷰를 Apify로 모으는 중…")
    reviews, raw, used = apify.fetch_amazon_reviews(urls, token, per_star=a.per_star, actor=a.actor)
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, "raw.json"), "w", encoding="utf-8") as f:
        json.dump({"input_used": {k: v for k, v in used.items() if k != "productUrls"}, "urls": urls,
                   "items": raw}, f, ensure_ascii=False, indent=1)
    print(f"  받은 결과 {len(raw):,}개, 읽은 리뷰 {len(reviews):,}개 (사용한 입력: {[k for k in used if k != 'productUrls']})")
    if not reviews:
        print("! 결과의 형식을 읽지 못했습니다. raw.json을 보고 필드 이름을 맞춰야 합니다.")
        return 1

    neg = [r for r in reviews if r.rating <= a.max_rating]
    summary = {"urls": urls, "n_reviews": len(reviews), "n_negative": len(neg), "per_product": {}, "checks": {}}
    for prod, cnt in Counter(r.app for r in reviews).items():
        n_neg = sum(1 for r in neg if r.app == prod)
        summary["per_product"][prod] = {"reviews": cnt, "negative": n_neg}
    for chk in a.check or []:
        label, _, pattern = chk.partition("=")
        hits = [r for r in neg if re.search(pattern, r.text.lower())]
        by_year = Counter(r.date[:4] for r in hits)
        all_year = Counter(r.date[:4] for r in neg)
        summary["checks"][label] = {"pattern": pattern, "hits": len(hits),
                                    "share": round(len(hits) / len(neg), 3) if neg else 0,
                                    "by_year": {y: [by_year[y], all_year[y]] for y in sorted(all_year)}}
        print(f"  '{label}': 불만 {len(neg)}개 중 {len(hits)}개 ({len(hits) / max(len(neg), 1):.0%})")
    with open(os.path.join(a.out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print(f"· 불만 리뷰(별점 {a.max_rating}점 이하) {len(neg):,}개 분석")
    try:
        themes = find_themes(neg, k_min=3, k_max=8)
    except ValueError as e:
        print(f"! {e}")
        return 0
    dates = sorted(r.date for r in neg)
    meta = {"query": a.name or "아마존 최신 리뷰", "n_neg": len(neg), "n_all": len(reviews), "max_rating": a.max_rating,
            "date_from": dates[0], "date_to": dates[-1], "apps": dict(Counter(r.app for r in neg).most_common(10))}
    write_html(os.path.join(a.out_dir, "report.html"), themes, [], meta)
    write_csv(os.path.join(a.out_dir, "reviews.csv"), themes)
    print(f"보고서: {os.path.join(a.out_dir, 'report.html')}")
    return 0


def cmd_trends(a):
    """지금 잘 팔리는데 불만이 많은 아마존 제품을 찾는다."""
    import json
    from . import apify, trends
    from .report import write_trends
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("! APIFY_TOKEN이 없습니다.")
        return 1
    cats = dict(trends.DEFAULT_CATEGORIES)
    if a.only:
        cats = {k: v for k, v in cats.items() if k in a.only}
    os.makedirs(a.out_dir, exist_ok=True)
    if a.bestsellers_json:
        raw_best = json.load(open(a.bestsellers_json, encoding="utf-8"))
        products = trends.parse_bestsellers(raw_best, cats)
        print(f"· 저장된 베스트셀러 {len(products)}개 사용")
    else:
        print(f"· 베스트셀러 수집: {', '.join(cats)}")
        products, raw_best = trends.fetch_bestsellers(cats, token, per_category=a.per_category)
        with open(os.path.join(a.out_dir, "bestsellers.json"), "w", encoding="utf-8") as f:
            json.dump(raw_best, f, ensure_ascii=False)
        print(f"  {len(products)}개")
    picked = trends.pick_candidates(products, n=a.n, per_category=a.per_cat_pick,
                                    min_reviews=a.min_reviews, max_stars=a.max_stars)
    print(f"· 잘 팔리는데 별점이 낮은 제품 {len(picked)}개:")
    for p in picked:
        print(f"   [{p.category}] #{p.position} ★{p.stars} ({p.reviews:,}) {p.name[:60]}")
    if not picked:
        print("! 조건에 맞는 제품이 없습니다. --max-stars를 올리거나 --min-reviews를 낮춰 보세요.")
        return 1

    reviews, raw_rev, _ = apify.fetch_amazon_reviews([p.url for p in picked], token, per_star=a.per_star)
    with open(os.path.join(a.out_dir, "reviews_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw_rev, f, ensure_ascii=False)
    trends.attach_reviews(picked, [r for r in reviews if r.rating <= 2])
    print(f"· 불만 리뷰 {sum(len(p.negatives) for p in picked):,}개 수집")

    results = []
    for p in picked:
        themes = []
        if len(p.negatives) >= 25:
            try:
                themes = find_themes(p.negatives, k_min=2, k_max=5)
            except ValueError:
                themes = []
        results.append((p, themes))
    write_trends(os.path.join(a.out_dir, "index.html"), results, len(products), list(cats))
    with open(os.path.join(a.out_dir, "trends.json"), "w", encoding="utf-8") as f:
        json.dump([{"category": p.category, "name": p.name, "url": p.url, "asin": p.asin, "position": p.position,
                    "stars": p.stars, "reviews": p.reviews, "price": p.price, "score": round(p.score, 2),
                    "themes": [{"title": t.title, "share": round(t.share, 3), "n": len(t.reviews),
                                "samples": [s.text[:300] for s in t.samples[:3]]} for t in th],
                    "negatives": [{"rating": r.rating, "date": r.date, "likes": r.likes, "text": r.text[:600]}
                                  for r in p.negatives]} for p, th in results], f, ensure_ascii=False, indent=1)
    print(f"보고서: {os.path.join(a.out_dir, 'index.html')}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="miner", description="불만 채굴기: 별점 낮은 리뷰에서 창업 기회를 찾는다")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="앱 이름으로 앱 ID 찾기")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=8)
    s.set_defaults(fn=cmd_search)

    r = sub.add_parser("run", help="리뷰를 모아 분석하고 보고서 만들기")
    r.add_argument("--play", action="append", default=[], help="구글 플레이 앱 ID (여러 번 가능)")
    r.add_argument("--appstore", action="append", default=[], help="앱스토어 앱 ID 숫자 (여러 번 가능)")
    r.add_argument("--csv", action="append", default=[], help="직접 모은 리뷰 CSV (여러 번 가능)")
    r.add_argument("--text-col", default="text")
    r.add_argument("--rating-col", default="rating")
    r.add_argument("--name", help="보고서 제목 (예: '가계부 앱 시장')")
    r.add_argument("--count", type=int, default=2000, help="구글 플레이에서 앱당 가져올 리뷰 수")
    r.add_argument("--max-rating", type=int, default=2, help="이 별점 이하만 불만으로 본다 (기본 2)")
    r.add_argument("-k", type=int, help="주제 개수 직접 지정 (기본: 자동)")
    r.add_argument("--out", default="reports/report.html")
    r.add_argument("--llm", action="store_true", help="키 환경변수 없이도 Claude 호출 시도 (ant auth login 사용 시)")
    r.add_argument("--no-llm", action="store_true", help="Claude를 쓰지 않기")
    r.set_defaults(fn=cmd_run)

    z = sub.add_parser("amazon", help="아마존 공개 리뷰로 불만 많은 상품 종류 찾기")
    z.add_argument("--category", default="All_Beauty",
                   help="카테고리 파일 이름 (예: All_Beauty, Health_and_Personal_Care, Baby_Products, Pet_Supplies)")
    z.add_argument("--data-dir", default="data")
    z.add_argument("--niches", type=int, default=20, help="순위에 보여줄 상품 종류 수")
    z.add_argument("--deep", type=int, default=5, help="불만 주제까지 분석할 상위 종류 수")
    z.add_argument("--term", action="append", help="이 상품 종류만 깊게 분석 (예: --term 'hair oil')")
    z.add_argument("--min-reviews", type=int, default=1500)
    z.add_argument("--exclude", action="append", help="상품명에 이 말이 있으면 제외 (예: --exclude 'human hair' --exclude wig)")
    z.add_argument("--out-dir", default="reports/amazon")
    z.add_argument("--no-llm", action="store_true")
    z.set_defaults(fn=cmd_amazon)

    lv = sub.add_parser("amazon-live", help="Apify로 아마존 상품의 최신 리뷰를 모아 분석 (APIFY_TOKEN 필요)")
    lv.add_argument("--url", action="append", default=[], help="아마존 상품 주소 (여러 번, 또는 쉼표로 여러 개)")
    lv.add_argument("--name", help="보고서 제목")
    lv.add_argument("--per-star", type=int, default=100, help="별점별 최대 리뷰 수 (도구 한도 100)")
    lv.add_argument("--max-rating", type=int, default=2)
    lv.add_argument("--check", action="append", help="'이름=정규식' 형태로 불만 속 특정 문제 비율 세기")
    lv.add_argument("--actor", default="junglee~amazon-reviews-scraper")
    lv.add_argument("--out-dir", default="reports/live")
    lv.set_defaults(fn=cmd_live)

    tr = sub.add_parser("trends", help="지금 잘 팔리는데 불만이 많은 아마존 제품 찾기 (APIFY_TOKEN 필요)")
    tr.add_argument("--only", action="append", help="이 분야만 (예: --only 스킨케어). 이름은 trends.DEFAULT_CATEGORIES")
    tr.add_argument("--per-category", type=int, default=50, help="분야별 베스트셀러 수 (최대 100)")
    tr.add_argument("--n", type=int, default=12, help="리뷰까지 볼 제품 수")
    tr.add_argument("--per-cat-pick", type=int, default=3, help="분야별 최대 선택 수")
    tr.add_argument("--min-reviews", type=int, default=300)
    tr.add_argument("--max-stars", type=float, default=4.4)
    tr.add_argument("--per-star", type=int, default=50, help="제품당 별점별 리뷰 수")
    tr.add_argument("--bestsellers-json", help="전에 저장한 bestsellers.json을 다시 쓰기 (수집 비용 절약)")
    tr.add_argument("--out-dir", default="reports/trends")
    tr.set_defaults(fn=cmd_trends)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
