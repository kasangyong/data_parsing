"""
프로젝트 전역 설정.

★ 경로 결정 방식 (git과 동일한 철학):
1. 환경변수 PDFSEARCH_DATA_DIR 가 있으면 그 폴더를 데이터 디렉터리로 사용
2. 현재 작업 폴더부터 상위로 올라가며 `.pdfsearch/` 폴더를 탐색 (git처럼)
3. 둘 다 없으면 → 이 저장소 안의 `storage/` (레거시/개발용 기본값)

즉, 아무 프로젝트 폴더에서 `pdfsearch init` 을 하면 그 폴더에 `.pdfsearch/` 가
생기고, 이후 그 폴더(또는 하위 폴더)에서 실행하는 모든 명령은 해당 프로젝트의
독립된 DB/인덱스/파일을 사용한다.

★ 임베딩 모델은 전역 공유 (~/.pdfsearch/models) — 프로젝트마다 다시 받지 않는다.
   (환경변수 PDFSEARCH_MODELS_DIR 로 변경 가능)
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 데이터 디렉터리 탐색
# ---------------------------------------------------------------------------
DATA_DIR_NAME = ".pdfsearch"

PACKAGE_DIR = Path(__file__).resolve().parent           # pdfsearch 패키지 폴더
REPO_DIR = PACKAGE_DIR.parent                           # 저장소 루트 (개발용)
STATIC_DIR = PACKAGE_DIR / "static"                     # 웹 UI 정적 파일


def find_project_data_dir(start: Path | None = None) -> Path | None:
    """현재 폴더부터 상위로 올라가며 `.pdfsearch/` 를 찾는다 (git 방식).

    단순히 `.pdfsearch` 디렉터리가 존재하는지만 보면, 홈 폴더 아래
    (전역 모델 캐시 등으로 인해 생성된) `.pdfsearch` 를 프로젝트로 오인할 수
    있으므로, 실제로 `init` 된 프로젝트임을 나타내는 `db.sqlite` 존재 여부까지
    확인한다.
    """
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        d = candidate / DATA_DIR_NAME
        if d.is_dir() and (d / "db.sqlite").exists():
            return d
    return None


def _resolve_data_dir() -> tuple[Path, bool]:
    """(데이터 디렉터리, 실제 프로젝트 여부) 반환."""
    env = os.environ.get("PDFSEARCH_DATA_DIR")
    if env:
        return Path(env).resolve(), True
    found = find_project_data_dir()
    if found is not None:
        return found, True
    # 레거시/개발용 기본값: 저장소 안의 storage/
    return REPO_DIR / "storage", False


DATA_DIR, IS_PROJECT = _resolve_data_dir()              # 현재 프로젝트 데이터 루트
PROJECT_ROOT = DATA_DIR.parent                          # 사용자의 프로젝트 폴더

PDF_DIR = DATA_DIR / "pdfs"                             # 업로드된 원본 PDF
IMAGE_DIR = DATA_DIR / "images"                         # 추출된 이미지
DB_PATH = DATA_DIR / "db.sqlite"                        # SQLite DB
TEXT_INDEX_PATH = DATA_DIR / "text.index"               # FAISS 텍스트 인덱스
IMAGE_INDEX_PATH = DATA_DIR / "image.index"             # FAISS 이미지 인덱스
INBOX_DIR = DATA_DIR / "inbox"                          # 일괄 파싱용 PDF 투입 폴더


# ---------------------------------------------------------------------------
# 모델 디렉터리 (전역 공유)
# ---------------------------------------------------------------------------

def _resolve_models_dir() -> Path:
    env = os.environ.get("PDFSEARCH_MODELS_DIR")
    if env:
        return Path(env).resolve()
    # 레거시: 저장소 안 models/ 에 이미 받아둔 모델이 있으면 그대로 사용
    legacy = REPO_DIR / "models"
    if (legacy / "hub").exists():
        return legacy
    # 기본: 홈 폴더 전역 캐시 (모든 프로젝트가 공유)
    return Path.home() / ".pdfsearch" / "models"


MODELS_DIR = _resolve_models_dir()

# 모델 캐시 위치 고정 (임포트 시점에 환경변수 설정)
os.environ.setdefault("HF_HOME", str(MODELS_DIR))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(MODELS_DIR))

# 번들 tessdata (Program Files 쓰기 권한 없이도 한국어 OCR 가능하도록).
# 사용자가 이미 TESSDATA_PREFIX를 설정했다면 그대로 존중 (setdefault).
_TESSDATA_DIR = REPO_DIR / "tessdata"
if (_TESSDATA_DIR / "eng.traineddata").exists():
    os.environ.setdefault("TESSDATA_PREFIX", str(_TESSDATA_DIR))

# 필요한 디렉터리 자동 생성
for _dir in (DATA_DIR, PDF_DIR, IMAGE_DIR, INBOX_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 모델 설정
# ---------------------------------------------------------------------------
# 텍스트 임베딩 (한국어 포함 다국어, 384차원)
TEXT_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TEXT_EMBED_DIM = 384

# 이미지 임베딩: CLIP 이미지 인코더 (512차원)
CLIP_IMAGE_MODEL_NAME = "sentence-transformers/clip-ViT-B-32"
# 이미지 검색용 텍스트 인코더 (다국어 → CLIP 공간, 512차원)
CLIP_TEXT_MODEL_NAME = "sentence-transformers/clip-ViT-B-32-multilingual-v1"
IMAGE_EMBED_DIM = 512

# ---------------------------------------------------------------------------
# 파싱 파라미터
# ---------------------------------------------------------------------------
CHUNK_SIZE = 500            # 텍스트 청크 최대 길이 (문자)
CHUNK_OVERLAP = 50          # 청크 간 오버랩 (문자)
MIN_IMAGE_SIZE = 50         # 이 크기(px) 미만 이미지는 아이콘으로 간주하고 제외
MIN_TABLE_ROWS = 2          # 최소 행 수 (1행짜리는 표로 보지 않음)

# ---------------------------------------------------------------------------
# OCR 설정 (스캔본 PDF)
# ---------------------------------------------------------------------------
OCR_LANGS = "kor+eng"       # Tesseract 언어 (한국어+영어)
OCR_TEXT_THRESHOLD = 30     # 페이지 추출 텍스트가 이 문자 수 미만이면 스캔본으로 판단 → OCR 시도
OCR_RENDER_DPI = 200        # OCR용 페이지 렌더링 해상도

# ---------------------------------------------------------------------------
# 벡터 그래픽 (선/도형으로 그려진 차트) 추출 설정
# ---------------------------------------------------------------------------
VECTOR_MIN_SIZE_PT = 60     # 벡터 영역 최소 크기 (pt) — 이보다 작으면 밑줄/장식으로 간주
VECTOR_MAX_AREA_RATIO = 0.85  # 페이지 면적 대비 최대 비율 (전체 배경 제외)
VECTOR_MAX_PER_PAGE = 5     # 페이지당 최대 벡터 영역 수
VECTOR_RENDER_ZOOM = 2.0    # 벡터 영역 렌더링 배율

# ---------------------------------------------------------------------------
# 검색/요약 파라미터
# ---------------------------------------------------------------------------
SEARCH_TOP_K = 30           # FAISS에서 가져올 후보 수
RESULTS_PER_QUERY = 20      # 최종 반환 결과 수 (문서 그룹 기준)
SUMMARY_SENTENCES = 5       # 요약 시 핵심 문장 수
