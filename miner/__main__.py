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

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
