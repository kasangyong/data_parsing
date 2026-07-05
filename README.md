# pdfsearch — PDF 멀티모달 검색 엔진

PDF에서 **텍스트 · 이미지 · 표 · 목차 · 링크 · 주석**을 추출하고,
의미 기반(시맨틱)으로 검색하며, 문서 요약까지 제공하는 로컬 도구입니다.

**git처럼 동작합니다.** 아무 프로젝트 폴더에서 `pdfsearch init` 하면
그 폴더에 `.pdfsearch/` 가 생기고, 프로젝트마다 **완전히 독립된 DB**를 갖습니다.
임베딩 모델(약 1~2GB)은 전역으로 한 번만 다운로드해서 모든 프로젝트가 공유합니다.

- 100% 무료 로컬 모델 (sentence-transformers + CLIP) — API 키/비용 없음
- 자연어로 이미지 검색 가능 ("막대 그래프", "지도 사진" 등)
- 한국어 포함 다국어 지원
- **지식 그래프** — 문서 간 연결(유사도 + 공유 키워드)을 웹 UI에서 시각화

> 🗺️ **로드맵**: 지식 그래프를 온톨로지 기반(엔티티/관계/클래스 신뢰도)으로
> 고도화하는 설계·자료조사 문서가 [`ROADMAP-knowledge-graph.md`](ROADMAP-knowledge-graph.md)에 있습니다.
> 온톨로지 설계, 클래스 신뢰도(confidence) 3계층 구조, **평가 하네스(harness)** 설계가 핵심입니다.
> **아직 미구현** — 구현 시 하네스(P1)를 추출(P2)보다 먼저 만들어야 합니다.

---

## 1. 설치 (컴퓨터마다 최초 1회)

새 컴퓨터에서 처음 설정할 때 아래 순서대로 하면 됩니다.

### 1-1. 이 저장소 가져오기

```bash
# 아무 위치에나 클론/복사 (예: 개발 폴더)
git clone <저장소주소> pdfsearch-tool
cd pdfsearch-tool

# git을 안 쓰면 이 폴더(parsing/)를 통째로 복사해도 됩니다.
# 단, storage/ 와 models/ 폴더는 복사할 필요 없습니다 (데이터/캐시).
```

### 1-2. 가상환경 + 설치

```bash
python -m venv venv

# Windows (Git Bash)
source venv/Scripts/activate
# Windows (PowerShell/CMD):  venv\Scripts\activate
# macOS/Linux:               source venv/bin/activate

pip install -e .
```

`pip install -e .` 한 줄이면 의존성 설치 + `pdfsearch` 명령어 등록이 끝납니다.
(`-e` 는 editable 설치 — 코드를 수정하면 재설치 없이 바로 반영됩니다.)

### 1-3. 임베딩 모델 다운로드 (전역, 최초 1회)

```bash
pdfsearch models
```

- 약 1~2GB, 네트워크에 따라 수 분 소요
- 저장 위치: `~/.pdfsearch/models` (홈 폴더) — **모든 프로젝트가 공유**하므로
  프로젝트를 아무리 많이 만들어도 다시 받을 필요 없습니다.

> **(선택) OCR**: 스캔본 PDF의 텍스트 인식이 필요하면 [Tesseract](https://github.com/UB-Mannheim/tesseract/wiki)를
> 별도 설치하세요 (한국어 데이터 `kor` 포함). 없어도 나머지 기능은 모두 동작합니다.

---

## 2. 사용법 — 프로젝트마다 독립 DB

**어느 폴더에서든** 아래처럼 사용합니다. git과 똑같은 방식입니다.

```bash
# 프로젝트 A
cd ~/develop/project-a
pdfsearch init                     # .pdfsearch/ 생성 → 이 폴더가 프로젝트가 됨
pdfsearch add 논문.pdf 매뉴얼.pdf   # PDF 추가 (파싱 + 인덱싱)
pdfsearch add docs/                # 폴더째 일괄 추가 (하위 폴더 포함)
pdfsearch search "매출 추이"        # 터미널에서 바로 검색
pdfsearch search "막대 그래프" -t image   # 이미지만 검색
pdfsearch list                     # 인덱싱된 문서 목록
pdfsearch status                   # 프로젝트/모델 상태 확인
pdfsearch serve                    # 웹 UI 실행 → http://127.0.0.1:8000

# 프로젝트 B — 완전히 독립된 별개의 DB
cd ~/develop/project-b
pdfsearch init
pdfsearch add report.pdf
pdfsearch serve --port 8001        # A와 동시에 띄우려면 포트만 다르게
```

### 명령어 요약

| 명령 | 설명 |
|---|---|
| `pdfsearch init` | 현재 폴더를 프로젝트로 초기화 (`.pdfsearch/` 생성) |
| `pdfsearch models` | 임베딩 모델 다운로드 (전역, 최초 1회) |
| `pdfsearch add <파일/폴더>...` | PDF 파싱 + 인덱싱 (중복은 자동 스킵) |
| `pdfsearch search "검색어" [-t all\|text\|image\|table\|annotation]` | 터미널 검색 |
| `pdfsearch list` | 문서 목록 |
| `pdfsearch status` | 프로젝트 경로 / 모델 준비 / OCR 가능 / 문서 수 |
| `pdfsearch serve [--port 8000] [--reload]` | 웹 UI (검색·업로드·요약) |

### 데이터가 저장되는 곳

```
project-a/
├── (당신의 프로젝트 파일들...)
└── .pdfsearch/          ← 이 프로젝트만의 데이터 (git의 .git과 같은 개념)
    ├── db.sqlite        #   문서/청크/이미지/표 메타데이터
    ├── pdfs/            #   업로드된 원본 PDF
    ├── images/          #   추출된 이미지
    ├── inbox/           #   (선택) 일괄 투입용 폴더
    ├── text.index       #   FAISS 텍스트 검색 인덱스
    └── image.index      #   FAISS 이미지 검색 인덱스

~/.pdfsearch/
└── models/              ← 임베딩 모델 (전역 공유, 1~2GB)
```

- 하위 폴더(`project-a/docs/` 등)에서 명령을 실행해도 git처럼 상위로 올라가며
  `.pdfsearch/`를 자동으로 찾아 연결됩니다.
- `init` 시 해당 폴더가 git 저장소면 `.gitignore`에 `.pdfsearch/`를 자동 추가합니다.
- 프로젝트를 지우고 싶으면 `.pdfsearch/` 폴더만 삭제하면 됩니다.

---

## 3. 다른 컴퓨터로 옮길 때 체크리스트

컴퓨터를 바꾸거나 새 PC에서 쓸 때는 **경로를 코드에서 고칠 필요가 전혀 없습니다.**
모든 경로는 실행 위치 기준으로 자동 결정되기 때문입니다. 아래만 하면 됩니다:

1. **저장소 가져오기** — `git clone` 또는 폴더 복사 (위치는 어디든 상관없음)
2. **가상환경 + 설치** — `python -m venv venv` → activate → `pip install -e .`
3. **모델 다운로드** — `pdfsearch models` (새 컴퓨터의 `~/.pdfsearch/models`에 저장)
4. **(선택) 기존 프로젝트 데이터 옮기기** — 프로젝트 폴더의 `.pdfsearch/` 폴더를
   통째로 복사하면 DB/인덱스/원본 PDF까지 그대로 이전됩니다. 다시 파싱할 필요 없음.
5. **(선택) OCR** — 스캔본 PDF를 다룬다면 Tesseract 설치

### 경로를 직접 지정하고 싶을 때 (환경변수)

기본 동작으로 충분하지만, 특수한 경우 환경변수로 바꿀 수 있습니다:

| 환경변수 | 기본값 | 용도 |
|---|---|---|
| `PDFSEARCH_DATA_DIR` | (자동 탐색) | 데이터 폴더를 강제 지정. 예: 외장하드에 DB를 두고 싶을 때 |
| `PDFSEARCH_MODELS_DIR` | `~/.pdfsearch/models` | 모델 캐시 위치 변경. 예: 용량 큰 드라이브로 |

```bash
# 예: 모델을 D 드라이브에 저장하고 싶을 때 (Git Bash)
export PDFSEARCH_MODELS_DIR="/d/ai-models/pdfsearch"
pdfsearch models
```

---

## 4. 웹 UI

`pdfsearch serve` 실행 후 http://127.0.0.1:8000 접속:

- **검색** — 텍스트/이미지/표/주석 통합 시맨틱 검색, 문서별 그룹핑
- **업로드** — 드래그앤드롭으로 PDF 추가
- **문서** — 목록/상세/삭제
- **요약** — 핵심 문장 추출 요약 + 표 미리보기 + 이미지 갤러리 + 목차/링크

## 5. 무엇이 추출되나

| 항목 | 방법 |
|---|---|
| 텍스트 | PyMuPDF + 노이즈 제거(머리글/바닥글/페이지번호), 유니코드 정규화, 분철 복원 |
| 이미지 | 임베디드 이미지 + 벡터 그래픽(차트) 렌더링, CLIP 임베딩으로 자연어 검색 |
| 표 | pdfplumber (병합 셀 전파, 다중 행 헤더 병합) |
| 목차/링크/주석 | PyMuPDF |
| 스캔본 | Tesseract OCR (설치된 경우) |

## 6. 개발자용 참고

```bash
# CLI 없이 직접 서버 실행 (현재 폴더 기준 프로젝트 탐색)
uvicorn pdfsearch.main:app --reload

# 레거시 스크립트 (호환용 래퍼 — pdfsearch models / add 와 동일)
python download_models.py
python ingest_folder.py
```

- 이 저장소 안에서 `init` 없이 실행하면 레거시 기본값인 `storage/` 폴더를 사용합니다
  (기존에 파싱해둔 데이터가 있다면 그대로 유지됨).
- 설정(청크 크기, OCR, 검색 파라미터 등)은 `pdfsearch/config.py` 에서 관리합니다.
