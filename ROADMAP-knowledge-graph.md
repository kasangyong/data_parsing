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

## 4. 기술 스택 자료조사

### 4.1 LangChain / GraphRAG 생태계 분석

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

### 4.2 오프라인 추출기 후보 (이 프로젝트의 원칙에 부합)

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

### 4.3 그래프 저장/질의

- **현행 유지: SQLite** — 수천 문서 규모까지 충분, 이식성(폴더 복사 = 이전) 유지
- 그래프 질의가 필요해지면: `networkx`를 메모리 레이어로 (SQLite → 로드)
- Neo4j 도입은 **하지 않는다** (서버 의존성이 "폴더마다 독립 DB" 원칙과 충돌).
  단, `pdfsearch export --format cypher|rdf` 로 내보내기는 지원 고려

---

## 5. 단계별 구현 계획 (제안)

| 단계 | 내용 | 산출물 | 난이도 |
|---|---|---|---|
| **P0** | 온톨로지 확정 + `ontology.yaml` 스키마/로더 | base 온톨로지, 검증기 | ★ |
| **P1** | 하네스 뼈대 + 골든셋 5문서 구축 | `harness/`, `pdfsearch harness run` | ★★ |
| **P2** | GLiNER 엔티티 추출 + entities/mentions 테이블 | 엔티티 그래프 (신뢰도 포함) | ★★ |
| **P3** | 엔티티 해소 (별칭 병합) | 중복 없는 노드 | ★★★ |
| **P4** | 공출현 관계 + 온톨로지 위반 필터 | related_to 엣지 | ★★ |
| **P5** | 신뢰도 보정 (ECE 측정 → 보정 함수) | calibration 리포트 | ★★ |
| **P6** | UI 확장: 엔티티 그래프 뷰 + 신뢰도 슬라이더 + 검수 화면 | graph.js v2 | ★★ |
| **P7** | (선택) Ollama 관계 추출 + self-consistency | 타입 있는 관계 | ★★★ |
| **P8** | (선택) Leiden 커뮤니티 → 주제 클러스터 뷰 | 클러스터 시각화 | ★★ |

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

- Microsoft GraphRAG: https://microsoft.github.io/graphrag/ — 커뮤니티 요약 개념
- LangChain LLMGraphTransformer: https://python.langchain.com/docs/how_to/graph_constructing/
- LlamaIndex PropertyGraphIndex: https://docs.llamaindex.ai/en/stable/module_guides/indexing/lpg_index_guide/
- GLiNER (제로샷 NER): https://github.com/urchade/GLiNER
- schema.org 어휘: https://schema.org
- Neo4j 지식그래프 구축 가이드(개념 참고): https://neo4j.com/developer/graph-data-science/
- Self-consistency (Wang et al. 2022), Calibration of LLMs 관련 서베이
- Leiden 커뮤니티 탐지: Traag et al. 2019
