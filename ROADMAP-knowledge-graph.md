# 지식 그래프 고도화 로드맵 — 온톨로지 · 클래스 신뢰도 · 하네스 설계

> **상태: 조사/설계 문서 (미구현)**
> 현재 구현된 지식 그래프(`pdfsearch/graph.py`)는 "문서 간 유사도" 수준이다.
> 이 문서는 그것을 **진짜 온톨로지 기반 지식 그래프**로 발전시키기 위한
> 자료조사, 설계 방향, 구현 계획을 정리한다.

---

## 0. 현재 상태와 목표의 간극

### 현재 (v1 — 구현 완료)

```
[문서 A] ──0.46(유사도)── [문서 B]
```

- 노드 = **문서**, 엣지 = **임베딩 코사인 유사도** + TF-IDF 공유 키워드
- "이 두 문서가 비슷하다"는 알 수 있지만 **"왜, 어떤 관계로"** 연결되는지는 모른다
- 키워드는 통계적 추출이라 개념(concept)이 아니라 그냥 단어다

### 목표 (v2~v3 — 이 문서의 범위)

```
[삼성전자:Company] ──생산──> [반도체:Product]
       │                        │
    본사위치                  사용됨
       ▼                        ▼
[수원:Location]          [스마트폰:Product]
```

- 노드 = **엔티티(개체)**: 사람, 조직, 장소, 제품, 개념, 사건 …
- 엣지 = **의미 있는 관계(관계 술어)**: 생산하다, 소속되다, 인용하다 …
- 각 노드/엣지에 **타입(클래스)과 신뢰도(confidence)** 부여
- 문서는 엔티티의 "출처(provenance)"가 된다

---

## 1. 온톨로지(Ontology) 설계

### 1.1 온톨로지란 무엇이고 왜 필요한가

온톨로지는 그래프에 넣을 수 있는 **노드 타입과 관계 타입의 스키마(설계도)**다.
온톨로지가 없으면:

- LLM/NER이 추출하는 엔티티 타입이 문서마다 제각각이 된다
  (`"회사"`, `"Company"`, `"기업"`, `"org"` 가 전부 다른 타입으로 쌓임)
- 관계도 무한히 발산한다 (`"만들다"`, `"제조하다"`, `"생산하다"` …)
- 결과적으로 **질의가 불가능한 쓰레기 그래프**가 된다

→ 온톨로지는 **추출의 가드레일**이다. GraphRAG, LangChain 모두
"허용된 타입 목록을 제한하는 것"이 품질의 핵심이라고 강조한다.

### 1.2 이 프로젝트를 위한 최소 온톨로지 (초안)

범용 문서(보고서/논문/기술문서)를 다루므로, 처음부터 도메인 특화하지 않고
**소규모 상위 온톨로지 + 프로젝트별 확장** 구조를 제안한다.

**엔티티 클래스 (8종으로 시작):**

| 클래스 | 예시 | 비고 |
|---|---|---|
| `Person` | 홍길동, John Smith | 저자, 인용된 인물 |
| `Organization` | 삼성전자, KFTC, 금융위원회 | 회사/기관/부서 |
| `Location` | 서울, 판교 | 지명 |
| `Product` | GPT-4, 갤럭시 S24 | 제품/시스템/서비스 |
| `Concept` | 머신러닝, 유동성 리스크 | 추상 개념/기술/방법론 |
| `Event` | 2024 컨퍼런스, 금융위기 | 사건/행사 |
| `Date` | 2024-03, 3분기 | 시간 표현 (정규화 필요) |
| `Metric` | 매출 3조원, 정확도 95% | 수치+단위 (표에서 특히 중요) |

**관계 클래스 (10종으로 시작):**

| 관계 | 도메인 → 레인지 | 예시 |
|---|---|---|
| `works_for` | Person → Organization | 홍길동 —소속— 삼성전자 |
| `located_in` | Org/Event → Location | 본사 —위치— 수원 |
| `produces` | Organization → Product | 삼성 —생산— 반도체 |
| `part_of` | Org → Org, Concept → Concept | 파운드리사업부 —일부— 삼성전자 |
| `uses` | Product/Org → Product/Concept | 검색엔진 —사용— FAISS |
| `related_to` | Concept ↔ Concept | 폴백용 범용 관계 |
| `mentions` | Document → Entity | 모든 엔티티의 출처 연결 (필수) |
| `defines` | Document → Concept | 용어 정의가 있는 경우 |
| `measured_as` | Concept → Metric | 매출 —측정값— 3조원 |
| `occurred_on` | Event → Date | 컨퍼런스 —일시— 2024-03 |

**설계 원칙:**

1. **`mentions` 관계는 무조건 유지** — 모든 엔티티는 "어느 문서 몇 페이지에서 나왔는지"
   출처를 잃으면 안 된다 (현재 v1 그래프의 장점을 계승)
2. **관계를 못 정하면 `related_to`로 폴백** — 버리는 것보다 낫다. 단, 신뢰도를 낮게 기록
3. **프로젝트별 온톨로지 확장 파일** — `.pdfsearch/ontology.yaml` 로 프로젝트마다
   도메인 클래스를 추가할 수 있게 한다 (예: 금융 프로젝트 → `Regulation`, `FinancialInstrument`)

```yaml
# .pdfsearch/ontology.yaml (구상)
extends: base            # 위의 8종 상위 온톨로지 상속
entity_types:
  - name: Regulation     # 프로젝트 도메인 확장
    description: 법령, 규정, 가이드라인
relation_types:
  - name: regulates
    domain: Regulation
    range: [Organization, Product]
```

### 1.3 표준과의 호환

- 내부 저장은 SQLite(경량)로 하되, **RDF 트리플 (주어-술어-목적어) 형태로
  export 가능한 스키마**를 유지한다 → 나중에 Neo4j/RDFLib/Protégé와 호환
- 클래스 이름은 [schema.org](https://schema.org) 어휘와 최대한 맞춘다
  (Person, Organization, Place, Product, Event — 이미 대부분 일치)

---

## 2. 클래스 신뢰도 (Confidence) 설계

### 2.1 왜 신뢰도가 핵심인가

엔티티/관계 추출은 **반드시 틀린다.** NER 모델도, LLM도 환각을 일으킨다.
신뢰도 없이 그래프에 다 넣으면 오염된 그래프가 되고,
너무 엄격하게 자르면 빈 그래프가 된다. 따라서:

> **"모든 노드와 엣지는 신뢰도 점수를 가지고, UI/질의에서 임계값으로 필터링한다"**

가 기본 설계 원칙이다. (현재 v1의 "연결 민감도" 슬라이더의 확장판)

### 2.2 신뢰도의 3계층 구조

신뢰도는 하나의 숫자가 아니라 **추출 → 타입 → 통합** 3단계에서 발생한다:

```
최종 신뢰도 = f(추출 신뢰도, 클래스 신뢰도, 코퍼스 증거 신뢰도)
```

**① 추출 신뢰도 (extraction confidence)**
- "이 텍스트 스팬이 엔티티가 맞는가?"
- NER 모델(GLiNER 등)은 토큰별 확률을 직접 제공 → 그대로 사용
- LLM 추출의 경우 확률이 없으므로 대안 필요 (아래 2.3)

**② 클래스 신뢰도 (class/type confidence)**
- "이 엔티티가 `Organization`이 맞는가, `Person`은 아닌가?"
- 방법 A: NER 모델의 클래스별 소프트맥스 확률
- 방법 B: 엔티티 임베딩 vs 클래스 설명문 임베딩의 코사인 유사도
  (이미 보유한 `paraphrase-multilingual-MiniLM` 재활용 가능 — **추가 모델 불필요**)
- 방법 C: LLM에게 자기평가 요구 ("확신도를 0~1로 답하라") — 보정 필요 (신뢰 불가로 악명)

**③ 코퍼스 증거 신뢰도 (corpus-level evidence)**
- "이 사실이 몇 개 문서/청크에서 반복 등장하는가?"
- 동일 트리플이 여러 문서에서 독립적으로 추출되면 신뢰도 상승:
  `conf_corpus = 1 - Π(1 - conf_i)` (독립 증거의 노이즈-OR 결합)
- 이것이 **환각 필터링의 최강 수단** — 1개 문서에서만 나온 관계는 낮게 표시

### 2.3 LLM 추출 신뢰도 확보 기법 (자료조사 결과)

| 기법 | 방법 | 비용 | 신뢰성 |
|---|---|---|---|
| **Self-consistency** | 같은 청크를 temperature>0으로 N회 추출 → 등장 빈도 = 신뢰도 | N배 | 높음 (사실상 표준) |
| Verbalized confidence | "확신도도 함께 출력하라" | 1배 | 낮음 (과신 편향, 보정 필수) |
| logprob 기반 | 출력 토큰의 로그확률 평균 | 1배 | 중간 (로컬 LLM이면 접근 가능) |
| **추출-검증 2패스** | 1차 추출 → 2차로 "이 트리플이 원문에 근거하는가?" 검증 | 2배 | 높음 (GraphRAG 방식과 유사) |
| NLI 검증 | 원문 청크 ⊨ 트리플 문장화 를 NLI 모델로 검증 | 1배+소형모델 | 높음, 오프라인 가능 |

**권장 조합 (오프라인 원칙 유지):**
- 1차: GLiNER(로컬 NER) 확률 → 추출 신뢰도
- 2차: 임베딩 유사도 → 클래스 신뢰도 (기존 모델 재활용)
- 3차: 코퍼스 반복 증거 → 노이즈-OR 결합
- (선택) Ollama 로컬 LLM 사용 시: self-consistency 3회 + 검증 패스

### 2.4 신뢰도 보정 (Calibration)

- 모델이 뱉는 "0.9"가 실제로 90% 정답률인지는 **검증 데이터로 확인해야 한다**
- 골든셋(§3)에서 신뢰도 구간별 실제 정답률을 측정 → **reliability diagram** 작성
- 어긋나면 Platt scaling / isotonic regression으로 보정 함수 학습
- 지표: **ECE (Expected Calibration Error)** — 하네스에 포함할 것

### 2.5 저장 스키마 (구상)

```sql
CREATE TABLE entities (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,           -- 정규화된 대표명
    entity_type TEXT NOT NULL,           -- 온톨로지 클래스
    type_confidence REAL NOT NULL,       -- 클래스 신뢰도 0~1
    aliases     TEXT DEFAULT '[]'        -- 동의어 JSON ["삼성", "Samsung"]
);

CREATE TABLE entity_mentions (           -- 출처 추적 (provenance)
    id          INTEGER PRIMARY KEY,
    entity_id   INTEGER REFERENCES entities(id),
    document_id INTEGER REFERENCES documents(id),
    chunk_id    INTEGER REFERENCES text_chunks(id),
    span_text   TEXT,                    -- 원문에서의 표현
    extraction_confidence REAL           -- 이 멘션의 추출 신뢰도
);

CREATE TABLE relations (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER REFERENCES entities(id),
    relation_type TEXT NOT NULL,         -- 온톨로지 관계 클래스
    target_id   INTEGER REFERENCES entities(id),
    confidence  REAL NOT NULL,           -- 결합 신뢰도
    evidence_count INTEGER DEFAULT 1,    -- 지지 증거(문서) 수
    extractor   TEXT                     -- 'gliner' | 'llm' | 'rule' (감사 추적)
);
```

핵심: **엔티티 해소(Entity Resolution)** — "삼성전자" = "삼성" = "Samsung Electronics"를
같은 노드로 합치는 단계가 반드시 필요하다. 방법:
1. 정규화(소문자/공백) 후 완전 일치
2. 임베딩 유사도 > 0.9 + 같은 타입 → 병합 후보
3. 병합도 신뢰도를 가진다 (잘못 합치면 그래프 오염이 가장 크므로 보수적으로)

---

## 3. 하네스(Harness) 설계 — 가장 중요한 부분

### 3.1 하네스란

추출 파이프라인은 "돌아가면 끝"이 아니다. 모델/프롬프트/파라미터를 바꿀 때마다
**품질이 좋아졌는지 나빠졌는지 자동으로 측정하는 실험·평가 장치**가 하네스다.
LLM 기반 시스템은 비결정적이므로 하네스 없이는 개선이 불가능하다
(감으로 튜닝하다가 회귀를 못 잡는다).

### 3.2 하네스 구성 요소

```
harness/
├── golden/                      # ① 골든 데이터셋
│   ├── docs/                    #    소규모 대표 PDF 5~10개
│   └── annotations/             #    사람이 검수한 정답 (JSON)
│       ├── doc1.entities.json   #    [{span, type, canonical_name}]
│       └── doc1.relations.json  #    [{source, relation, target}]
├── run_eval.py                  # ② 평가 러너 (파이프라인 실행 → 정답과 비교)
├── metrics.py                   # ③ 지표 계산
├── report/                      # ④ 실행별 리포트 (JSON + 마크다운)
└── baselines.json               # ⑤ 기준 점수 (회귀 감지용)
```

**① 골든 데이터셋 만들기 (가장 노동집약적이지만 가장 중요)**
- 실제 사용할 도메인의 PDF 5~10개를 선정
- 초벌: 파이프라인이 자동 추출 → 사람이 UI에서 수정/확정 (앱에 검수 화면 추가 고려)
- 100% 완벽할 필요 없음 — "부분 골든셋"이라도 회귀 감지에는 충분

**② 측정 지표 (metrics.py)**

| 대상 | 지표 | 설명 |
|---|---|---|
| 엔티티 추출 | Precision / Recall / **F1** | 스팬 부분일치 허용(soft match) 버전도 병행 |
| 클래스 분류 | 클래스별 F1 + confusion matrix | 어떤 타입이 자주 헷갈리는지 |
| 관계 추출 | 트리플 F1 (주어,술어,목적어 완전일치 / 완화일치) | 가장 어려운 지표 |
| 엔티티 해소 | 병합 정확도 (B³, pairwise F1) | 잘못 병합 vs 못 병합 |
| 신뢰도 품질 | **ECE**, reliability diagram | §2.4 보정 검증 |
| 시스템 | 처리 시간/문서, 메모리 피크 | 로컬 실행이므로 중요 |
| 그래프 품질 | 고아 노드 비율, 평균 차수, 온톨로지 위반 수 | 스키마 준수 검사 |

**③ 실행 방식**

```bash
pdfsearch harness run              # 골든셋 전체 평가 → report/2026-07-05_1432.json
pdfsearch harness compare A B      # 두 실행 비교 (모델/프롬프트 A/B 테스트)
pdfsearch harness regress          # baselines.json 대비 회귀 여부 (CI에서 사용)
```

**④ 회귀 게이트 (regression gate)**
- 파이프라인 코드를 수정하면 하네스를 돌리고,
  `엔티티 F1 -2%p 이상 하락 시 실패` 같은 게이트를 둔다
- GitHub Actions에 연결 가능 (골든셋이 작으므로 CPU로도 수 분 내 완료)

**⑤ 온톨로지 준수 검사 (schema validation)**
- 추출된 모든 트리플에 대해: 관계의 domain/range가 온톨로지와 일치하는가?
  (`works_for(Product, Location)` 같은 위반은 자동 거부 + 카운트)
- 이 검사 자체가 **무료 품질 필터**다 — LLM 환각의 상당수를 걸러낸다

### 3.3 하네스가 지원해야 할 실험 축

1. 추출기 교체: GLiNER vs 로컬 LLM vs 규칙 혼합
2. 프롬프트 버전 (LLM 사용 시): 프롬프트를 파일로 버전 관리
3. 신뢰도 임계값 스윕: threshold 0.3~0.9 → P/R 곡선
4. 청크 크기/오버랩: 추출 품질에 큰 영향
5. self-consistency 횟수 N: 품질 vs 속도 트레이드오프

---

## 4. 기술 스택 자료조사 (심층)

> 온톨로지/지식그래프는 지금 "LLM의 환각을 구조로 잡는다"는 흐름 속에서
> 가장 뜨거운 분야다. 크게 4개 축으로 나눠 조사했다:
> **(A) 온톨로지 표준 스택 (B) LLM 구조화 추출 (C) GraphRAG 계열 (D) 저장/질의 엔진**

### 4.A 온톨로지 표준 스택 — "스키마를 코드처럼 다루는" 도구들

W3C 시맨틱 웹 표준은 학술용으로 보이지만, 실무 도구가 성숙해서
**"YAML로 스키마 정의 → 자동 검증 → 표준 포맷 export"** 파이프라인을 공짜로 얻을 수 있다.

| 기술 | 무엇인가 | 이 프로젝트에서의 활용 |
|---|---|---|
| **LinkML** (`pip install linkml`) | YAML로 스키마(클래스/슬롯/제약)를 정의하면 JSON-Schema, Pydantic 모델, OWL, SQL DDL을 **자동 생성** | §1.2의 `ontology.yaml` 구상과 정확히 일치. 온톨로지를 LinkML로 정의하면 Pydantic 검증 모델이 공짜로 나옴 → **P0의 1순위 후보** |
| **SHACL** + `pyshacl` | RDF 그래프가 스키마를 준수하는지 선언적으로 검증 ("Person의 works_for는 Organization이어야 함") | §3.2-⑤ 온톨로지 준수 검사를 직접 구현하지 않고 SHACL shape으로 선언 → 하네스에서 `pyshacl.validate()` 한 줄 |
| **RDFLib** (`pip install rdflib`) | 파이썬 표준 RDF 라이브러리. 트리플 저장/SPARQL 질의/직렬화(Turtle, JSON-LD) | `pdfsearch export --format rdf` 구현체. SQLite → RDFLib Graph 변환 후 어떤 표준 도구와도 연동 |
| **owlready2** (`pip install owlready2`) | OWL 온톨로지를 파이썬 클래스처럼 조작 + **HermiT/Pellet 추론기 내장** | 추론(§4.A-추론) 실험용. `sync_reasoner()` 한 줄로 암묵적 관계 유도 |
| **Protégé** (GUI, 무료) | 스탠퍼드의 온톨로지 편집기. 사실상 업계 표준 | base 온톨로지를 시각적으로 설계/검토할 때. LinkML ↔ OWL 변환으로 왕복 가능 |
| **SKOS** | 개념 계층(broader/narrower/related) 표준 어휘 | `Concept` 클래스 내부의 계층 표현 ("머신러닝 —narrower→ 딥러닝")에 관계를 새로 발명하지 말고 SKOS 차용 |
| **JSON-LD** | JSON에 시맨틱 컨텍스트를 얹는 포맷 | API 응답(`/api/graph`)을 JSON-LD로 확장하면 표준 도구가 바로 소비 가능. 기존 JSON과 호환됨 |

**추론(Reasoning) — 온톨로지의 숨은 강점:**
온톨로지에 `part_of`가 이행적(transitive)이라고 선언하면, 추론기가
`A part_of B, B part_of C ⇒ A part_of C`를 **자동 유도**한다. 추출하지 못한
엣지를 논리로 채우는 것. 경량 추론기 순위: **ELK(빠름, EL 프로파일) > HermiT(완전) > Pellet**.
파이썬에서는 owlready2로 접근. 단, 잘못된 트리플이 있으면 추론이 오염을 **증폭**시키므로
반드시 신뢰도 필터(§2) 이후에 적용할 것.

**엔티티 링킹 (Entity Linking) — 로컬 그래프를 세계 지식에 접지:**

| 도구 | 방식 | 비고 |
|---|---|---|
| **ReFinED** (Amazon) | 엔티티를 **Wikidata QID**에 연결 (예: 삼성전자 → Q20718) | 빠르고 정확. 영어 중심이지만 다국어 별칭 지원 |
| mGENRE (Meta) | 다국어 생성 기반 엔티티 링킹 | 한국어 포함 100+ 언어 |
| spaCy `entityLinker` | 경량 Wikidata 링킹 | 프로토타입용 |

QID로 접지하면: ① "삼성전자" = "Samsung Electronics" 병합이 **공짜** (엔티티 해소 P3의 지름길)
② Wikidata에서 산업/본사/CEO 등 사실을 가져와 그래프를 풍부화 가능 ③ 프로젝트 간 그래프 연결 가능.
→ **P3 단계에서 임베딩 병합보다 먼저 시도할 가치가 있음.**

**Palantir 스타일 "운영 온톨로지" — 왜 지금 온톨로지가 핫한가:**
Palantir Foundry가 증명한 관점: 온톨로지는 학술 스키마가 아니라
**"조직의 데이터(명사) + 행동(동사)을 하나의 의미 계층으로 묶는 운영 레이어"**다.
LLM 에이전트가 등장하면서 이 관점이 폭발했다 — 에이전트가 안전하게 행동하려면
"무엇이 존재하고(엔티티) 무엇을 할 수 있는지(행동)"의 명세가 필요하기 때문.
이 프로젝트에 주는 시사점: 온톨로지를 정적 스키마가 아니라
**검색/요약/질의 API가 참조하는 살아있는 계층**으로 설계할 것
(예: `Metric` 타입 노드는 표 검색과 연동, `Person` 노드는 저자 필터와 연동).

### 4.B LLM 구조화 추출 — "스키마를 강제하는" 최신 기법

LLM에게 "JSON으로 답해"라고 비는 시대는 끝났다. 스키마를 **구조적으로 강제**하는 도구들:

| 기술 | 방식 | 이 프로젝트 적용 |
|---|---|---|
| **OntoGPT / SPIRES** (Monarch Initiative) | **LinkML 스키마를 주면** LLM이 그 스키마에 맞는 인스턴스만 추출. 재귀적 스키마 순회 방식 | 온톨로지 기반 추출의 학술 레퍼런스 구현. LinkML 채택 시(P0) 그대로 실험 가능. Ollama 백엔드 지원 |
| **Outlines / guided decoding** | 로컬 LLM의 디코딩 자체를 JSON-Schema/정규식으로 **제약** — 잘못된 출력이 물리적으로 불가능 | Ollama/vLLM + outlines 조합으로 P7에서 "스키마 위반 0%" 보장. llama.cpp의 GBNF 문법도 동일 효과 |
| **Instructor** (`pip install instructor`) | Pydantic 모델을 주면 LLM 응답을 검증+자동 재시도 | LinkML → Pydantic 자동 생성과 연결하면 온톨로지 → 추출 코드가 전부 자동 파생됨 |
| **BAML** | 프롬프트+스키마를 전용 DSL로 버전 관리 | 하네스의 "프롬프트 버전 관리"(§3.3-2) 요구와 부합. 도입 부담이 있어 선택사항 |
| **Triplex** (SciPhi) | 트리플 추출 전용으로 파인튜닝된 소형 모델 (Phi-3 기반, ~4GB) — GPT-4 대비 98% 비용 절감 주장 | Ollama에서 실행 가능. **P7의 유력 후보** (범용 LLM보다 관계 추출 특화) |

**권장 추출 스택 (P7):**
```
LinkML 온톨로지 (P0)
  → linkml에서 Pydantic 모델 자동 생성
  → Ollama(Triplex 또는 qwen2.5) + Outlines(JSON-Schema 강제 디코딩)
  → 스키마 위반이 구조적으로 불가능한 트리플 추출
  → self-consistency 3회 → 신뢰도 부여 (§2.3)
  → pyshacl 검증 → 통과분만 DB 저장
```

### 4.C LangChain / GraphRAG 계열 생태계 분석

**LangChain `LLMGraphTransformer`**
- 문서 → LLM → `GraphDocument(nodes, relationships)` 자동 변환
- `allowed_nodes`, `allowed_relationships` 파라미터로 온톨로지 제약 가능
- 장점: 구현이 몇 줄, Neo4j 연동 즉시 가능
- 단점: **신뢰도 개념이 없음** (점수 미제공), LLM API 의존(비용/오프라인 위배),
  한국어 품질은 사용하는 LLM에 전적으로 의존
- **결론: 개념(스키마 제약 추출)만 차용하고 직접 구현 권장**

**Microsoft GraphRAG**
- 청크 → 엔티티/관계 추출 → 커뮤니티 탐지(Leiden) → 커뮤니티별 LLM 요약
- "글로벌 질문"(문서 전체를 아우르는 질문)에 강함
- 단점: LLM 호출량이 막대 (문서당 수백 회) → 로컬 우선 원칙과 충돌
- **차용할 것: 커뮤니티 탐지로 그래프를 주제 클러스터로 묶는 아이디어**
  (Leiden/Louvain은 `networkx`/`igraph`로 LLM 없이 가능)

**LlamaIndex `KnowledgeGraphIndex` / `PropertyGraphIndex`**
- 트리플 추출 + 그래프 질의 통합, `SchemaLLMPathExtractor`로 온톨로지 강제
- GraphRAG보다 가볍지만 역시 LLM 의존

**차세대 경량 GraphRAG (2024~) — MS GraphRAG의 비용 문제를 공격하는 후속작들:**

| 프로젝트 | 핵심 아이디어 | 이 프로젝트와의 관련성 |
|---|---|---|
| **LightRAG** (HKU) | 엔티티+관계의 **이중 레벨 검색** (저수준: 엔티티 이웃, 고수준: 테마). GraphRAG 대비 API 호출 수십 배 절감, **증분 업데이트** 지원 | 아키텍처가 우리 구조(SQLite+FAISS)와 가장 유사. `pdfsearch add` 때마다 그래프 증분 갱신하는 설계의 레퍼런스 |
| **nano-graphrag** | GraphRAG를 ~1,100줄로 재구현한 교육용/해킹용 구현체 | 커뮤니티 요약 파이프라인을 이해하고 커스터마이징할 때 최고의 교재 |
| **Fast GraphRAG** (CircleMind) | PageRank 기반 그래프 탐색으로 검색 정확도/속도 개선 | 그래프 질의(P6 이후) 시 "관련 서브그래프 추출" 알고리즘 참고 |
| **Graphiti** (Zep) | **시간 축을 가진 지식그래프** — 사실마다 유효기간(valid_at/invalid_at) 기록, 모순되는 새 사실이 오면 옛 사실을 무효화 | 문서 버전이 갱신되는 프로젝트(보고서 개정판 등)에서 중요. `relations`에 `valid_from/valid_to` 컬럼 추가를 미리 고려 |
| **Cognee** | "AI 메모리 엔진" — 문서→그래프+벡터 파이프라인을 5줄 API로 | 전체 파이프라인 설계 비교 대상 |

**공통 교훈:** 최신 구현들은 전부 ① 그래프+벡터의 **하이브리드 검색** ② **증분 업데이트**
③ LLM 호출 최소화, 세 가지에 수렴하고 있다. 우리는 이미 벡터 검색(FAISS)이 있으므로
그래프 레이어만 추가하면 하이브리드가 완성된다.

### 4.D 오프라인 추출기 후보 (이 프로젝트의 원칙에 부합)

| 도구 | 역할 | 한국어 | 크기 | 신뢰도 제공 |
|---|---|---|---|---|
| **GLiNER** (`urchade/gliner_multi-v2.1`) | 제로샷 NER — 임의 클래스명을 주면 추출 | ◎ (다국어) | ~1GB | ◎ (확률) |
| spaCy + ko 모델 | 고전 NER | △ | ~50MB | ○ |
| **Ollama** (qwen2.5, llama3.1 등) | 관계 추출 + 요약 | ◎ | 4~8GB | △ (logprob) |
| ReLiK / mREBEL | 관계 추출 전용 모델 | △ (영어 중심) | ~2GB | ○ |
| Leiden (igraph) | 커뮤니티 탐지 | - | 소형 | - |

**권장 아키텍처 (오프라인 우선, 단계적):**

```
1단계: GLiNER로 엔티티+클래스+신뢰도 추출 (LLM 불필요!)
2단계: 규칙 + 공출현(co-occurrence) 기반 related_to 관계 (신뢰도 = PMI 정규화)
3단계: (선택) Ollama 로컬 LLM으로 관계 정제 — 온톨로지 제약 프롬프트 + self-consistency
4단계: Leiden 커뮤니티 탐지 → 주제 클러스터 → 클러스터 라벨링
```

→ 1~2단계만으로도 "명명된 엔티티 그래프"가 나오고, LLM은 옵션으로 남는다.
   기존 `pdfsearch models`처럼 `pdfsearch models --kg` 로 GLiNER 추가 다운로드.

### 4.E 그래프 저장/질의 엔진 — "임베디드" 신흥 강자들

"폴더마다 독립 DB" 원칙 때문에 서버형(Neo4j)은 배제하지만,
**SQLite처럼 파일 하나로 동작하는 임베디드 그래프 DB**가 최근 급성장했다:

| 엔진 | 유형 | 질의 언어 | 평가 |
|---|---|---|---|
| **Kùzu** (`pip install kuzu`) | 임베디드 프로퍼티 그래프 (그래프계의 DuckDB) | **Cypher** | `.pdfsearch/graph.kuzu` 파일 하나로 동작. 벡터 인덱스+전문검색 내장. 노드 수백만 규모까지. **그래프 질의가 필요해지는 시점(P6+)의 1순위** |
| **Oxigraph** (`pip install pyoxigraph`) | 임베디드 RDF 트리플스토어 | **SPARQL** | 표준 시맨틱웹 스택을 서버 없이. SHACL/추론과의 궁합은 최고지만 프로퍼티 그래프보다 개발 편의성 낮음 |
| SQLite (현행) | 관계형 | SQL + 재귀 CTE | 2-hop 이내 질의는 재귀 CTE로 충분. **P2~P5까지는 현행 유지** |
| networkx | 인메모리 | Python API | 커뮤니티 탐지(Leiden via `igraph`)·중심성 계산용 분석 레이어. 저장소가 아니라 계산 도구 |

**결정 기준:** "특정 엔티티에서 3-hop 이상 탐색" 또는 "경로 질의"가 필요해지는 순간
SQLite 재귀 CTE가 고통스러워진다 → 그때 Kùzu로 그래프 부분만 이관
(SQLite는 문서/청크 메타데이터용으로 유지, 이중 저장 구조).
`pdfsearch export --format cypher|rdf|jsonld` 는 어느 경우든 지원.

---

## 5. 단계별 구현 계획 (제안)

| 단계 | 내용 | 핵심 기술 (§4 참조) | 산출물 | 난이도 |
|---|---|---|---|---|
| **P0** | 온톨로지 확정 + 스키마/로더 | **LinkML** (YAML→Pydantic/OWL 자동 생성), Protégé 검토 | base 온톨로지, 검증기 | ★ |
| **P1** | 하네스 뼈대 + 골든셋 5문서 구축 | pyshacl (스키마 준수 검사), 자체 metrics | `harness/`, `pdfsearch harness run` | ★★ |
| **P2** | 엔티티 추출 + entities/mentions 테이블 | **GLiNER** multi-v2.1 (제로샷 NER + 확률) | 엔티티 그래프 (신뢰도 포함) | ★★ |
| **P3** | 엔티티 해소 (별칭 병합) | ① **ReFinED/mGENRE** (Wikidata QID 접지) ② 임베딩 유사도 폴백 | 중복 없는 노드 | ★★★ |
| **P4** | 공출현 관계 + 온톨로지 위반 필터 | PMI 통계 + SHACL 검증 | related_to 엣지 | ★★ |
| **P5** | 신뢰도 보정 (ECE 측정 → 보정 함수) | scikit-learn isotonic regression | calibration 리포트 | ★★ |
| **P6** | UI 확장: 엔티티 그래프 뷰 + 신뢰도 슬라이더 + 검수 화면 | graph.js v2, (질의 필요 시 **Kùzu** 검토) | 엔티티 그래프 UI | ★★ |
| **P7** | (선택) 로컬 LLM 관계 추출 | **Ollama + Triplex/qwen2.5 + Outlines** (스키마 강제 디코딩) + self-consistency | 타입 있는 관계 | ★★★ |
| **P8** | (선택) 커뮤니티 탐지 → 주제 클러스터 뷰 | **Leiden** (igraph), LightRAG식 이중 레벨 검색 | 클러스터 시각화 | ★★ |
| **P9** | (선택) 시간 축 지식그래프 | Graphiti 개념 차용 (`valid_from/valid_to`) | 문서 개정 추적 | ★★★ |

**의존 순서 주의: P1(하네스)을 P2(추출)보다 먼저 한다.**
측정 장치 없이 추출부터 만들면 품질 개선이 불가능하다 — 이것이 이 문서의 핵심 주장이다.

---

## 6. 리스크와 미해결 질문

- **한국어 관계 추출 품질**: 오프라인 관계 추출 모델은 영어 중심.
  한국어는 로컬 LLM(Ollama) 없이는 `related_to` 수준에 머물 가능성 → P7의 가치가 큼
- **골든셋 구축 비용**: 문서당 1~2시간의 검수 노동. 초벌 자동화 + 검수 UI로 완화
- **엔티티 해소의 파괴성**: 잘못 병합하면 되돌리기 어려움 → 병합 이력 테이블 + undo 필요
- **모델 크기**: GLiNER ~1GB, Ollama 4GB+ → `models --kg` 옵션으로 선택 설치
- **기존 v1 그래프와의 관계**: 문서 유사도 그래프(v1)와 엔티티 그래프(v2)는
  **둘 다 유지** (용도가 다름: v1 = 문서 탐색, v2 = 지식 질의). UI에서 토글

---

## 7. 참고 자료

**온톨로지 표준 스택**
- LinkML (YAML 스키마 → Pydantic/OWL/JSON-Schema): https://linkml.io
- SHACL 검증 / pySHACL: https://github.com/RDFLib/pySHACL
- RDFLib: https://rdflib.readthedocs.io
- owlready2 (OWL + 추론기): https://owlready2.readthedocs.io
- Protégé 온톨로지 편집기: https://protege.stanford.edu
- schema.org 어휘: https://schema.org / SKOS: https://www.w3.org/2004/02/skos/

**LLM 구조화 추출**
- OntoGPT / SPIRES (LinkML 스키마 기반 추출): https://github.com/monarch-initiative/ontogpt
- Outlines (스키마 강제 디코딩): https://github.com/dottxt-ai/outlines
- Instructor (Pydantic 검증 추출): https://github.com/jxnl/instructor
- Triplex (트리플 추출 특화 소형 모델): https://huggingface.co/SciPhi/Triplex
- GLiNER (제로샷 NER): https://github.com/urchade/GLiNER

**GraphRAG 계열**
- Microsoft GraphRAG: https://microsoft.github.io/graphrag/
- LightRAG (이중 레벨 검색 + 증분 업데이트): https://github.com/HKUDS/LightRAG
- nano-graphrag (1,100줄 재구현): https://github.com/gusye1234/nano-graphrag
- Fast GraphRAG (PageRank 탐색): https://github.com/circlemind-ai/fast-graphrag
- Graphiti (시간 축 지식그래프): https://github.com/getzep/graphiti
- LangChain LLMGraphTransformer: https://python.langchain.com/docs/how_to/graph_constructing/
- LlamaIndex PropertyGraphIndex: https://docs.llamaindex.ai/en/stable/module_guides/indexing/lpg_index_guide/

**엔티티 링킹 / 그래프 엔진**
- ReFinED (Wikidata 링킹): https://github.com/amazon-science/ReFinED
- mGENRE (다국어 엔티티 링킹): https://github.com/facebookresearch/GENRE
- Kùzu 임베디드 그래프 DB: https://kuzudb.com
- Oxigraph 임베디드 RDF 스토어: https://github.com/oxigraph/oxigraph

**이론/논문**
- Self-consistency (Wang et al. 2022), LLM Calibration 서베이
- Leiden 커뮤니티 탐지 (Traag et al. 2019)
- SPIRES 논문 (Caufield et al. 2023) — 온톨로지 기반 zero-shot 추출
