# PDF 멀티모달 파싱 & 검색 엔진 프로젝트 계획서

## 1. 프로젝트 개요

PDF 문서에서 **텍스트, 이미지, 표**를 각각 추출하여 DB에 저장하고,
텍스트뿐만 아니라 **이미지와 표 내용까지 의미 기반으로 검색**할 수 있는 검색 엔진 웹사이트를 만든다.

검색 결과에서 PDF를 클릭하면 해당 PDF의 **통합 요약**(핵심 문장 + 표 미리보기 + 이미지 썸네일)을 보여준다.

### 핵심 차별점
- 일반 PDF 파싱은 텍스트만 추출 → 이미지/표 검색 불가
- 본 프로젝트는 **CLIP 임베딩**으로 "고양이 사진", "매출 그래프" 같은 자연어로 이미지 검색 가능
- 표는 구조를 유지한 채 저장하여 표 내용으로도 검색 가능

---

## 2. 기술 스택 (전부 무료 / 로컬 실행)

| 역할 | 기술 | 비고 |
|---|---|---|
| PDF 텍스트/이미지 추출 | PyMuPDF (fitz) | 빠르고 정확 |
| PDF 표 추출 | pdfplumber | 표 구조 인식에 강함 |
| 텍스트 임베딩 | sentence-transformers `paraphrase-multilingual-MiniLM-L12-v2` | 한국어 지원, 384차원 |
| 이미지 임베딩 | CLIP `clip-ViT-B-32` + `clip-ViT-B-32-multilingual-v1` | 한국어 텍스트 → 이미지 검색, 512차원 |
| 벡터 검색 | FAISS (faiss-cpu) | 코사인 유사도 (IndexFlatIP + 정규화) |
| 메타데이터 DB | SQLite | 파일 하나로 관리, 설치 불필요 |
| 웹 서버 | FastAPI + Uvicorn | REST API |
| 프론트엔드 | 순수 HTML/CSS/JS | 빌드 불필요 |
| 요약 | 추출 요약 (임베딩 기반 핵심 문장 선택) | 무거운 LLM 불필요 |

---

## 3. 아키텍처

```
                        ┌──────────────────────────────┐
 [PDF 업로드]  ───────▶ │        파싱 파이프라인          │
                        │  parser.py                   │
                        │  ├─ 텍스트 추출 → 청크 분할      │
                        │  ├─ 이미지 추출 → 파일 저장      │
                        │  └─ 표 추출   → JSON 구조 저장  │
                        └──────────┬───────────────────┘
                                   │
                        ┌──────────▼───────────────────┐
                        │        인덱싱                  │
                        │  embeddings.py (지연 로딩)      │
                        │  ├─ 텍스트 청크 → 텍스트 임베딩   │
                        │  ├─ 이미지     → CLIP 임베딩    │
                        │  └─ 표(텍스트화) → 텍스트 임베딩  │
                        └──────────┬───────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
       SQLite (메타데이터)    FAISS 텍스트 인덱스    FAISS 이미지 인덱스
       storage/db.sqlite    storage/text.index   storage/image.index
              │                    │                    │
              └────────────────────┼────────────────────┘
                                   │
                        ┌──────────▼───────────────────┐
                        │      검색 & 요약 API           │
                        │  search.py / summarizer.py   │
                        └──────────┬───────────────────┘
                                   │
                        ┌──────────▼───────────────────┐
                        │    웹 UI (static/)            │
                        │  검색창 → 결과 목록 → PDF 요약   │
                        └──────────────────────────────┘
```

---

## 4. 프로젝트 구조

```
parsing/
├── app/
│   ├── __init__.py
│   ├── config.py          # 모든 설정 중앙화 (경로, 모델명, 청크 크기 등)
│   ├── main.py            # FastAPI 앱 + API 엔드포인트
│   ├── parser.py          # PDF → 텍스트/이미지/표 추출
│   ├── database.py        # SQLite 스키마 & CRUD
│   ├── embeddings.py      # 모델 지연 로딩 + 임베딩 생성
│   ├── search.py          # FAISS 인덱스 관리 + 통합 검색
│   └── summarizer.py      # PDF 통합 요약 (핵심 문장 + 표 + 이미지)
├── static/
│   ├── index.html         # 검색 메인 페이지 (SPA)
│   ├── app.js
│   └── style.css
├── storage/               # (자동 생성, git 제외)
│   ├── pdfs/              # 업로드된 원본 PDF
│   ├── images/            # 추출된 이미지 파일
│   ├── db.sqlite          # 메타데이터 DB
│   ├── text.index         # FAISS 텍스트 인덱스
│   └── image.index        # FAISS 이미지 인덱스
├── download_models.py     # ★ 모델 다운로드 전용 스크립트 (선택 실행)
├── requirements.txt
├── .gitignore
├── plan.md                # 본 문서
└── README.md              # 설치/실행 가이드
```

---

## 5. DB 스키마 (SQLite)

```sql
-- 업로드된 PDF 문서
documents (
    id            INTEGER PK,
    filename      TEXT,          -- 원본 파일명
    file_hash     TEXT UNIQUE,   -- SHA-256 (중복 업로드 방지)
    page_count    INTEGER,
    created_at    TEXT
)

-- 텍스트 청크 (페이지별 분할)
text_chunks (
    id            INTEGER PK,
    document_id   INTEGER FK,
    page_number   INTEGER,
    chunk_index   INTEGER,
    content       TEXT,
    faiss_id      INTEGER        -- FAISS 텍스트 인덱스 내 ID
)

-- 추출된 이미지
images (
    id            INTEGER PK,
    document_id   INTEGER FK,
    page_number   INTEGER,
    image_path    TEXT,          -- storage/images/ 내 파일 경로
    width         INTEGER,
    height        INTEGER,
    faiss_id      INTEGER        -- FAISS 이미지 인덱스 내 ID
)

-- 추출된 표
tables (
    id            INTEGER PK,
    document_id   INTEGER FK,
    page_number   INTEGER,
    table_index   INTEGER,
    table_json    TEXT,          -- 2차원 배열 JSON (구조 보존)
    table_text    TEXT,          -- 검색용 텍스트화 버전
    faiss_id      INTEGER        -- FAISS 텍스트 인덱스 내 ID (표는 텍스트 인덱스 공유)
)
```

### FAISS ↔ DB 매핑 전략
- 텍스트 인덱스: `faiss_id` → (`kind`: chunk/table, `row_id`) 매핑 테이블(`faiss_text_map`)로 관리
- 이미지 인덱스: `faiss_id` → `images.id` 매핑 테이블(`faiss_image_map`)로 관리
- 문서 삭제 시 FAISS는 삭제 대신 매핑 테이블에서 제거 → 검색 시 매핑 없는 ID는 무시 (안전한 소프트 삭제)

---

## 6. API 설계

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/api/upload` | PDF 업로드 → 파싱 → 인덱싱 (중복 시 409) |
| GET | `/api/search?q=...&type=all\|text\|image\|table` | 통합 검색, 유사도 순 결과 |
| GET | `/api/documents` | 업로드된 PDF 목록 |
| GET | `/api/documents/{id}` | PDF 상세 (요소 통계) |
| GET | `/api/documents/{id}/summary` | 통합 요약 (핵심 문장 + 표 + 이미지) |
| DELETE | `/api/documents/{id}` | 문서 삭제 |
| GET | `/api/status` | 모델 다운로드/로딩 상태 확인 |
| GET | `/images/...` | 추출된 이미지 정적 서빙 |
| GET | `/pdfs/...` | 원본 PDF 정적 서빙 |

### 검색 결과 형식
```json
{
  "query": "매출 그래프",
  "results": [
    {
      "document_id": 1,
      "filename": "report.pdf",
      "score": 0.83,
      "match_type": "image",       // text | image | table
      "page_number": 5,
      "preview": "...",             // 텍스트 스니펫 or 이미지 URL or 표 미리보기
      "matches": [ ... ]            // 해당 문서 내 상위 매칭 요소들
    }
  ]
}
```
- 같은 문서의 여러 요소가 매칭되면 **문서 단위로 그룹핑**, 최고 점수 순 정렬

---

## 7. 핵심 설계 결정

### 7-1. 모델 지연 로딩 (★ 중요)
- 코드 임포트 시점에는 **모델을 절대 로드하지 않음**
- `download_models.py` 를 실행해야 모델이 다운로드됨 (약 1~2GB)
- 모델 미다운로드 상태:
  - 서버는 정상 기동
  - `/api/status` 에서 모델 준비 여부 확인 가능
  - 업로드/검색 시도 시 명확한 안내 메시지 반환 (503)
- 모델 다운로드 후: **코드 수정 없이** 서버 재시작만으로 전체 기능 동작
- `HF_HOME`을 프로젝트 내 `models/` 폴더로 고정 → 모델 위치 명확, 삭제 쉬움

### 7-2. 텍스트 청킹
- 페이지 단위로 추출 후, 문단 기준 분할 (약 500자, 오버랩 50자)
- 페이지 번호 보존 → 검색 결과에서 "몇 페이지 매칭" 표시

### 7-3. 이미지 필터링
- 너무 작은 이미지(아이콘, 불릿 등, 50px 미만) 제외
- 손상된 이미지 스트림은 건너뛰고 로그만 남김 (전체 파싱 실패 방지)

### 7-4. 표 처리
- `table_json`: 원본 구조 보존 (요약 화면에서 HTML 표로 렌더링)
- `table_text`: "헤더: 값" 형태로 텍스트화 → 텍스트 임베딩으로 검색

### 7-5. 요약 (summarizer)
- 문서 전체 텍스트 청크의 임베딩 평균 = 문서 중심 벡터
- 중심 벡터와 가장 유사한 상위 N개 문장 = 핵심 문장 (추출 요약)
- 요약 화면 구성: 핵심 문장 + 페이지별 표 미리보기 + 이미지 갤러리

### 7-6. 예외 처리 원칙
- 페이지 단위 try/except → 한 페이지 실패해도 나머지 계속 처리
- 업로드 파싱 결과 리포트 (텍스트 N청크, 이미지 N개, 표 N개 추출됨)
- 중복 업로드는 파일 해시로 차단

---

## 8. 구현 순서

1. [x] 계획 수립 (본 문서)
2. [ ] 프로젝트 초기 설정: `requirements.txt`, `.gitignore`, `config.py`
3. [ ] `database.py`: SQLite 스키마 & CRUD
4. [ ] `parser.py`: PDF 텍스트/이미지/표 추출
5. [ ] `embeddings.py`: 지연 로딩 임베딩 모듈 + `download_models.py`
6. [ ] `search.py`: FAISS 인덱스 관리 + 통합 검색
7. [ ] `summarizer.py`: 통합 요약
8. [ ] `main.py`: FastAPI API 조립
9. [ ] `static/`: 웹 UI (검색 + 요약 화면)
10. [ ] `README.md`: 설치/실행 가이드
11. [ ] 전체 동작 점검

---

## 9. 실행 방법 (완성 후)

```bash
# 1. 라이브러리 설치
pip install -r requirements.txt

# 2. 모델 다운로드 (원할 때 1회, 약 1~2GB)
python download_models.py

# 3. 서버 실행
uvicorn app.main:app --reload

# 4. 브라우저에서 http://localhost:8000 접속
```
