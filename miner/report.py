"""분석 결과를 한 장짜리 HTML 보고서와 CSV로 저장한다."""
from __future__ import annotations

import csv
from datetime import datetime
from html import escape

from .analyze import Theme

CSS = """
:root{--bg:#f6f7f9;--paper:#fff;--ink:#141a22;--muted:#5d6675;--line:#e1e5eb;--accent:#e4572e;--soft:#fdeee8;--good:#1f8a5b;--bar:#2a78d6}
@media (prefers-color-scheme:dark){:root{--bg:#0e1116;--paper:#161b22;--ink:#e6ebf2;--muted:#97a1b0;--line:#2a313c;--accent:#ff7a52;--soft:#2b1a14;--good:#4cc38a;--bar:#3987e5}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;word-break:keep-all}
.wrap{max-width:900px;margin:0 auto;padding:0 16px}header{padding:36px 0 18px}h1{font-size:30px;margin:0;letter-spacing:-.5px}
.meta{color:var(--muted);font-size:14px}.card{background:var(--paper);border:1px solid var(--line);border-radius:16px;padding:18px 20px;margin:16px 0}
h2{font-size:21px;margin:34px 0 8px}h3{font-size:19px;margin:0 0 4px}.rank{color:var(--accent);font-weight:800;margin-right:6px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.chip{font-size:13px;padding:2px 10px;border-radius:999px;background:var(--bg);border:1px solid var(--line)}
.chip.hot{background:var(--soft);border-color:var(--accent);color:var(--accent);font-weight:700}.chip.good{color:var(--good);border-color:var(--good)}
.kw{color:var(--muted);font-size:14px}blockquote{margin:8px 0;padding:8px 12px;border-left:3px solid var(--line);background:var(--bg);border-radius:0 8px 8px 0;font-size:14.5px}
blockquote small{color:var(--muted)}.app-row{display:grid;grid-template-columns:140px 1fr 46px;gap:8px;align-items:center;font-size:13.5px;margin:3px 0}
.app-bar{height:10px;background:var(--bg);border-radius:6px;overflow:hidden}.app-bar span{display:block;height:100%;background:var(--bar)}
.opp{background:var(--soft);border-radius:10px;padding:10px 14px;margin-top:10px;font-size:15px}.hyp h3{font-size:17px}
.hyp dl{display:grid;grid-template-columns:96px 1fr;gap:4px 12px;margin:8px 0 0;font-size:15px}.hyp dt{color:var(--muted)}.hyp dd{margin:0}
svg text{font-family:inherit}.note{color:var(--muted);font-size:13.5px}footer{color:var(--muted);font-size:13px;padding:30px 0 50px}
details summary{cursor:pointer;color:var(--muted);font-size:14px;margin-top:8px}
.rk{display:grid;grid-template-columns:1fr 46px;gap:2px 10px;margin:10px 0}.rk-name{grid-column:1/3;font-size:15px;font-weight:600}
.rk-bar{height:12px;background:var(--bg);border-radius:6px;overflow:hidden}.rk-bar span{display:block;height:100%;background:var(--accent);border-radius:6px}.rk-val{font-size:13px;font-weight:700;text-align:right;line-height:12px}
@media(max-width:560px){.app-row{grid-template-columns:96px 1fr 40px}.hyp dl{grid-template-columns:1fr}}
"""


def _ranking(themes: list[Theme]) -> str:
    top = themes[:10]
    m = max(t.score for t in top) or 1
    rows = "".join(
        f'<div class="rk"><span class="rk-name">{i}. {escape(t.title)}</span>'
        f'<span class="rk-bar"><span style="width:{max(2, t.score / m * 100):.0f}%"></span></span>'
        f'<span class="rk-val">{t.score:.1f}</span></div>' for i, t in enumerate(top, 1))
    return f'<div role="list" aria-label="창업 기회 점수 순위">{rows}</div>'


def _growth_chip(g: float | None) -> str:
    if g is None:
        return '<span class="chip">추세: 데이터 부족</span>'
    if g >= 1.3:
        return f'<span class="chip hot">▲ 최근 늘어남 ×{g:.1f}</span>'
    if g <= 0.77:
        return f'<span class="chip good">▼ 최근 줄어듦 ×{g:.1f}</span>'
    return f'<span class="chip">→ 비슷함 ×{g:.1f}</span>'


def write_html(path: str, themes: list[Theme], hypotheses: list[dict], meta: dict) -> None:
    multi = len(meta["apps"]) > 1
    h = [f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
         f'<title>불만 채굴 보고서</title><style>{CSS}</style></head><body><div class="wrap">',
         f'<header><h1>불만 채굴 보고서</h1><p class="meta">{escape(meta["query"])} · '
         f'불만 리뷰 {meta["n_neg"]:,}개 (전체 {meta["n_all"]:,}개 중 별점 {meta["max_rating"]}점 이하) · '
         f'{meta["date_from"]} ~ {meta["date_to"]} · {datetime.now():%Y-%m-%d %H:%M} 생성</p>'
         f'<p class="meta">대상: {escape(", ".join(f"{a} ({n:,}개)" for a, n in meta["apps"].items()))}</p></header>']

    h.append('<h2>창업 기회 점수 순위</h2><div class="card">' + _ranking(themes) +
             '<p class="note">점수 = 불만 비중 × 공감 수 가중 × 최근 증가율' + (' × 여러 앱 공통 여부' if multi else '') +
             '. 높을수록 "많은 사람이, 강하게, 점점 더" 겪는 불만입니다. 순위는 출발점일 뿐, 정답이 아닙니다.</p></div>')

    if hypotheses:
        h.append('<h2>창업 가설 (Claude 정리)</h2>')
        for i, hy in enumerate(hypotheses, 1):
            h.append(f'<div class="card hyp"><h3><span class="rank">가설 {i}</span>{escape(hy["hypothesis"])}</h3><dl>'
                     f'<dt>근거</dt><dd>{escape(hy["evidence"])}</dd>'
                     f'<dt>싸게 검증</dt><dd>{escape(hy["cheapest_test"])}</dd>'
                     f'<dt>가장 큰 위험</dt><dd>{escape(hy["risk"])}</dd></dl></div>')

    h.append('<h2>불만 주제 상세</h2>')
    for i, t in enumerate(themes, 1):
        chips = [f'<span class="chip">전체 불만의 {t.share:.1%} · {len(t.reviews):,}개</span>',
                 f'<span class="chip">평균 공감 {t.avg_likes:.1f}</span>', _growth_chip(t.growth)]
        if multi:
            chips.append(f'<span class="chip{" hot" if t.cross_app > 1 else ""}">{t.cross_app}개 앱 공통</span>')
        if t.extra.get("fixable") is False:
            chips.append('<span class="chip">기존 앱의 문제 (새 회사가 끼어들 틈 적음)</span>')
        h.append(f'<div class="card"><h3><span class="rank">#{i}</span>{escape(t.title)}</h3>'
                 f'<div class="chips">{"".join(chips)}</div>'
                 f'<p class="kw">키워드: {escape(", ".join(t.keywords))}</p>')
        if t.pain:
            h.append(f'<p><b>문제:</b> {escape(t.pain)}' + (f' <span class="kw">({escape(t.extra.get("who", ""))})</span>' if t.extra.get("who") else '') + '</p>')
        if multi:
            for a, v in sorted(t.by_app.items(), key=lambda x: -x[1]):
                h.append(f'<div class="app-row"><span>{escape(a[:18])}</span><div class="app-bar"><span style="width:{min(v * 100 * 2, 100):.0f}%"></span></div><span>{v:.0%}</span></div>')
        for s in t.samples[:3]:
            h.append(f'<blockquote>{escape(s.text[:400])}<br><small>★{s.rating} · 공감 {s.likes} · {escape(s.app)} · {s.date}</small></blockquote>')
        if len(t.samples) > 3:
            h.append('<details><summary>리뷰 더 보기</summary>' + "".join(
                f'<blockquote>{escape(s.text[:400])}<br><small>★{s.rating} · 공감 {s.likes} · {escape(s.app)} · {s.date}</small></blockquote>'
                for s in t.samples[3:]) + '</details>')
        if t.opportunity:
            h.append(f'<div class="opp"><b>기회 가설:</b> {escape(t.opportunity)}</div>')
        h.append('</div>')

    h.append('<footer><p>읽는 법과 한계</p><ul>'
             '<li>리뷰는 화난 사람이 더 많이 씁니다. 비중은 "불만을 말한 사람" 기준이지 전체 사용자 기준이 아닙니다.</li>'
             '<li>주제 묶기는 자동입니다. 비슷한 주제가 둘로 나뉘거나, 다른 주제가 섞일 수 있으니 대표 리뷰를 꼭 읽어 보세요.</li>'
             '<li>' + ('앱별 막대 길이는 비중의 2배로 그렸습니다(작은 차이를 보이게 하려고). 정확한 값은 오른쪽 숫자입니다.</li><li>' if multi else '') +
             '불만이 크다는 것과 사람들이 돈을 낼 거라는 건 다른 문제입니다. 가설은 반드시 따로 검증하세요.</li></ul></footer></div></body></html>')
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(h))


def write_csv(path: str, themes: list[Theme]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["theme_rank", "theme", "app", "source", "rating", "likes", "date", "text"])
        for i, t in enumerate(themes, 1):
            for r in t.reviews:
                w.writerow([i, t.title, r.app, r.source, r.rating, r.likes, r.date, r.text])
