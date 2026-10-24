"""(선택) Claude로 각 불만 주제에 이름을 붙이고 창업 가설을 뽑는다.

ANTHROPIC_API_KEY(또는 `ant auth login`)가 있을 때만 쓴다. 없으면 키워드만으로 보고서를 만든다.
"""
from __future__ import annotations

import json

from .analyze import Theme

MODEL = "claude-opus-5-5"

SCHEMA = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "label": {"type": "string", "description": "불만을 12자 안팎으로 요약한 이름"},
                    "pain": {"type": "string", "description": "사용자가 실제로 겪는 문제를 한두 문장으로"},
                    "who": {"type": "string", "description": "이 불만을 가장 크게 겪는 사람"},
                    "opportunity": {"type": "string", "description": "이 불만을 해결하는 제품·서비스 가설 한두 문장"},
                    "is_fixable_by_newcomer": {"type": "boolean",
                                               "description": "신규 창업자가 풀 수 있는 문제인가 (기존 앱의 단순 버그·정책이면 false)"},
                    "same_as": {"type": "integer",
                                "description": "다른 주제와 사실상 같은 불만이면 그 주제의 id, 아니면 -1"},
                },
                "required": ["id", "label", "pain", "who", "opportunity", "is_fixable_by_newcomer", "same_as"],
                "additionalProperties": False,
            },
        },
        "top_hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "evidence": {"type": "string", "description": "근거가 되는 주제와 숫자"},
                    "cheapest_test": {"type": "string", "description": "만들기 전에 수요를 확인할 가장 싼 방법"},
                    "risk": {"type": "string", "description": "이 가설이 틀릴 수 있는 가장 큰 이유"},
                },
                "required": ["hypothesis", "evidence", "cheapest_test", "risk"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["themes", "top_hypotheses"],
    "additionalProperties": False,
}

PROMPT = """당신은 초기 창업자를 돕는 리서처입니다. 아래는 앱 리뷰 중 별점이 낮은 불만 리뷰를 자동으로 묶은 주제들입니다.
각 주제마다 키워드, 수치, 대표 리뷰가 있습니다.

할 일:
1. 주제마다 짧은 이름(label), 실제 문제(pain), 가장 크게 겪는 사람(who), 해결 제품 가설(opportunity)을 쓰세요.
2. 자동 묶기는 같은 불만을 두 주제로 나누기도 합니다. 근본 원인이 같은 주제끼리는 same_as에 남길 주제의 id를 적으세요(아니면 -1).
3. 기존 앱의 단순 버그나 고객센터 문제처럼, 새 회사가 끼어들 틈이 없는 불만은 is_fixable_by_newcomer를 false로 하세요.
4. 전체를 보고 창업 가설 최대 3개를 고르세요. 근거 숫자, 만들기 전에 수요를 확인하는 가장 싼 방법, 가장 큰 위험을 함께 쓰세요.

규칙: 리뷰에 없는 내용을 지어내지 마세요. 숫자는 아래 제공된 것만 쓰세요. 한국어로, 쉬운 말로 쓰세요.

{body}"""


def _body(themes: list[Theme]) -> str:
    parts = []
    for t in themes:
        growth = f"{t.growth:.2f}배" if t.growth else "알 수 없음"
        apps = ", ".join(f"{a} {v:.0%}" for a, v in sorted(t.by_app.items(), key=lambda x: -x[1]))
        quotes = "\n".join(f"  - (★{s.rating}, 공감 {s.likes}) {s.text[:300]}" for s in t.samples)
        parts.append(
            f"[주제 id={t.id}]\n키워드: {', '.join(t.keywords)}\n"
            f"비중: 전체 불만의 {t.share:.1%} ({len(t.reviews)}개), 평균 공감 {t.avg_likes:.1f}, 최근 90일 증가율 {growth}\n"
            f"앱별 비중: {apps}\n대표 리뷰:\n{quotes}"
        )
    return "\n\n".join(parts)


def label_themes(themes: list[Theme]) -> tuple[list[dict], dict[int, int]]:
    """주제에 이름을 붙이고 (창업 가설 목록, 합칠 주제 {흡수될 id: 남길 id})를 돌려준다. 실패하면 빈 값."""
    import anthropic

    client = anthropic.Anthropic()
    try:
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": PROMPT.format(body=_body(themes))}],
        )
    except anthropic.AuthenticationError:
        print("  ! Claude API 키가 없거나 잘못되어 이름 붙이기를 건너뜁니다.")
        return [], {}
    except anthropic.RateLimitError:
        print("  ! Claude API 사용 한도에 걸려 이름 붙이기를 건너뜁니다. 잠시 뒤 다시 실행하세요.")
        return [], {}
    except anthropic.APIStatusError as e:
        print(f"  ! Claude API 오류({e.status_code})로 이름 붙이기를 건너뜁니다.")
        return [], {}
    except anthropic.APIConnectionError:
        print("  ! Claude API에 연결하지 못해 이름 붙이기를 건너뜁니다.")
        return [], {}

    if resp.stop_reason == "refusal":
        print("  ! Claude가 요청을 처리하지 않아 이름 붙이기를 건너뜁니다.")
        return [], {}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        print("  ! Claude 응답을 읽지 못해 이름 붙이기를 건너뜁니다.")
        return [], {}

    by_id = {t.id: t for t in themes}
    same_as = {}
    for item in data["themes"]:
        t = by_id.get(item["id"])
        if not t:
            continue
        t.label, t.pain, t.opportunity = item["label"], item["pain"], item["opportunity"]
        t.extra["who"] = item["who"]
        t.extra["fixable"] = item["is_fixable_by_newcomer"]
        if item["same_as"] in by_id and item["same_as"] != t.id:
            same_as[t.id] = item["same_as"]
    return data["top_hypotheses"], same_as
