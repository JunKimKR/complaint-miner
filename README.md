# 불만 채굴기 (complaint-miner)

별점 1~2점짜리 앱 리뷰를 모아 **주제별로 묶고**, "많은 사람이, 강하게, 점점 더" 겪는 불만부터 순위를 매겨 **창업 기회**를 찾는 프로그램입니다.

경쟁 앱 2~5개를 함께 넣으면 **여러 앱에 공통으로 나타나는 불만**(= 시장 전체의 빈틈)을 따로 표시합니다.

## 무엇을 해주나

1. **수집**: 구글 플레이(앱당 최대 수천 개), 앱스토어(최근 500개), 직접 모은 CSV(설문, 커뮤니티 글 등)
2. **묶기**: 한국어 형태소 분석(Kiwi)으로 단어를 뽑고, 비슷한 불만끼리 자동으로 묶음. 묶음 수도 자동으로 정함
3. **점수**: 불만 비중 × 공감 수 × 최근 90일 증가율 × 여러 앱 공통 여부
4. **(선택) Claude 정리**: Claude API 키가 있으면 각 주제에 이름을 붙이고, 같은 불만끼리 합치고, 기존 앱의 단순 버그는 점수를 깎고, **창업 가설 3개와 만들기 전에 싸게 검증하는 방법**까지 정리
5. **결과**: 휴대폰으로도 보기 좋은 HTML 보고서 + 주제가 표시된 리뷰 CSV

## 방법 1: 내 PC에서 실행

파이썬 3.10 이상이 필요합니다.

```bash
pip install -r requirements.txt

# 1) 앱 이름으로 ID 찾기
python -m miner search "가계부"

# 2) 경쟁 앱 여러 개를 함께 분석
python -m miner run --play com.회사.앱1 --play com.회사.앱2 --appstore 1234567890 --name "가계부 앱 시장"

# 3) 직접 모은 CSV 분석 (열 이름: text, rating, date, likes, app — text만 있어도 됨)
python -m miner run --csv 내데이터.csv --name "설문 응답"
```

`reports/report.html`을 브라우저로 열면 됩니다.

**Claude로 가설까지 정리하려면** 실행 전에 API 키를 넣으세요 (사용료가 따로 듭니다. Claude 구독과는 별개).

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
```

## 방법 2: PC 없이 GitHub에서 실행 (휴대폰 가능)

1. 이 저장소의 **Actions** 탭 → **불만 채굴** → **Run workflow**
2. 보고서 제목과 앱 ID를 넣고 실행 (5~10분)
3. 끝나면 저장소의 `reports/` 폴더에 보고서가 저장됩니다. 실행 결과 페이지의 **Artifacts**에서 내려받아도 됩니다.

Claude 정리까지 원하면 **Settings → Secrets and variables → Actions**에 `ANTHROPIC_API_KEY`를 추가하세요.

## 아마존 상품 분석 (온라인 판매 상품 아이디어)

아마존 공개 리뷰 데이터(Amazon Reviews 2023, 연구용 공개)로 **불만이 많은 상품 종류 순위**를 매기고, 상위 종류마다 불만 주제를 분석합니다. 배송·포장·판매자 불만은 상품 자체 문제가 아니므로 점수를 낮춥니다.

```bash
python -m miner amazon --category All_Beauty            # 화장품
python -m miner amazon --category Pet_Supplies --deep 3  # 반려동물
python -m miner amazon --category All_Beauty --term "hair oil"   # 특정 종류만 깊게
```

GitHub에서는 Actions → **아마존 상품 불만 채굴** → Run workflow. 결과는 `reports/amazon-카테고리-날짜/index.html`.
데이터는 2023년까지입니다. 큰 카테고리(Home_and_Kitchen 등)는 내려받는 데 오래 걸립니다.

## 옵션

| 옵션 | 뜻 |
|---|---|
| `--max-rating 2` | 이 별점 이하만 불만으로 봄 (기본 2) |
| `--count 2000` | 구글 플레이 앱당 가져올 리뷰 수 |
| `-k 8` | 주제 개수 직접 지정 (기본 자동) |
| `--no-llm` | 키가 있어도 Claude를 쓰지 않음 |
| `--out 경로.html` | 보고서 저장 위치 |

## 한계 (꼭 읽기)

- **리뷰는 화난 사람이 더 많이 씁니다.** 비중은 "불만을 말한 사람" 기준입니다.
- **불만이 크다고 돈을 내는 건 아닙니다.** 보고서의 가설은 출발점이고, 반드시 따로 검증하세요 (예: 사전 신청 페이지).
- 자동 묶기는 완벽하지 않습니다. 대표 리뷰를 직접 읽어 보세요.
- 구글 플레이 수집은 비공식 라이브러리를 씁니다. 스토어 구조가 바뀌면 잠시 안 될 수 있습니다. 대량·상업적 수집 전에는 각 스토어 약관을 확인하세요.
- 앱스토어는 공개된 최근 리뷰 500개까지만 가져올 수 있습니다.

## 테스트

```bash
python -m pytest -q tests
```

`examples/테스트용_가짜리뷰.csv`는 테스트를 위해 만든 **가짜 문장**입니다. 실제 시장 데이터가 아닙니다.
