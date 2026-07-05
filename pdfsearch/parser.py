"""
PDF 파싱 모듈 (풀 스펙).

추출 요소:
1. 텍스트      : PyMuPDF, 페이지별 → 청크 분할. 스캔본이면 OCR(Tesseract)로 대체
2. 이미지      : PyMuPDF 임베디드 이미지 (아이콘 등 제외)
3. 벡터 그래픽  : 선/도형으로 그려진 차트·다이어그램 영역을 감지해 이미지로 렌더링
4. 표          : pdfplumber, 병합 셀 값 전파 + 다중 전략(lines→text) + 다중 행 헤더 보정
5. 메타데이터   : 제목/저자/주제/키워드/생성일
6. 목차(북마크) : 문서 구조
7. 하이퍼링크   : URL + 앵커 텍스트
8. 주석        : 형광펜/메모/스티커노트 내용

모든 페이지 처리는 개별 try/except → 한 페이지 실패가 전체를 망치지 않음.
"""
import hashlib
import io
import logging
import re
import shutil as _shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber
from PIL import Image

from .config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    IMAGE_DIR,
    MIN_IMAGE_SIZE,
    MIN_TABLE_ROWS,
    OCR_LANGS,
    OCR_RENDER_DPI,
    OCR_TEXT_THRESHOLD,
    VECTOR_MAX_AREA_RATIO,
    VECTOR_MAX_PER_PAGE,
    VECTOR_MIN_SIZE_PT,
    VECTOR_RENDER_ZOOM,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 결과 데이터 구조
# ---------------------------------------------------------------------------

@dataclass
class ParsedChunk:
    page_number: int          # 1-based
    chunk_index: int
    content: str
    source: str = "native"    # native | ocr


@dataclass
class ParsedImage:
    page_number: int
    image_path: str           # IMAGE_DIR 기준 상대 경로
    width: int
    height: int
    kind: str = "image"       # image | vector (벡터 그래픽 렌더링)


@dataclass
class ParsedTable:
    page_number: int
    table_index: int
    data: list[list[str]]     # 병합 셀 전파 완료된 2차원 배열
    text: str                 # 검색용 텍스트화 버전


@dataclass
class ParsedOutline:
    level: int
    title: str
    page_number: int


@dataclass
class ParsedLink:
    page_number: int
    url: str
    anchor_text: str


@dataclass
class ParsedAnnotation:
    page_number: int
    annot_type: str           # Highlight | Text | FreeText | ...
    content: str


@dataclass
class ParseResult:
    page_count: int = 0
    metadata: dict = field(default_factory=dict)
    chunks: list[ParsedChunk] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    outlines: list[ParsedOutline] = field(default_factory=list)
    links: list[ParsedLink] = field(default_factory=list)
    annotations: list[ParsedAnnotation] = field(default_factory=list)
    ocr_pages: list[int] = field(default_factory=list)   # OCR이 사용된 페이지
    ocr_available: bool = False
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 공통 유틸
# ---------------------------------------------------------------------------

def compute_file_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 텍스트 정규화 (유니코드 / 하이픈 줄바꿈 / 점선 리더 / 보이지 않는 문자)
# ---------------------------------------------------------------------------

# 보이지 않는 문자: zero-width space/joiner, BOM, soft hyphen 등
_INVISIBLE_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
# 목차 점선 리더: "제1장 서론 ........ 5"
_DOT_LEADER_RE = re.compile(r"[.·•‥⋯…]{4,}")
# 줄바꿈 하이픈: "infor-\nmation" → "information" (영문 단어 분철 복원)
_HYPHEN_BREAK_RE = re.compile(r"(?<=[A-Za-z])-\n(?=[a-z])")
# 문장 종결 판정 (줄 병합용)
_SENT_END_RE = re.compile(r"[.!?。:;)\]」』〉》다]['\"”’)]*\s*$")


def _normalize_text(text: str) -> str:
    """페이지 원문 정규화 — 청킹/임베딩 전 노이즈 제거."""
    # 1) 유니코드 정규화: 리가처(ﬁ→fi), 전각→반각 등
    text = unicodedata.normalize("NFKC", text)
    # 2) 보이지 않는 문자 제거
    text = _INVISIBLE_RE.sub("", text)
    # 3) 줄바꿈으로 분철된 영단어 복원
    text = _HYPHEN_BREAK_RE.sub("", text)
    # 4) 목차 점선 리더 → 공백
    text = _DOT_LEADER_RE.sub(" ", text)
    return text


def _join_wrapped_lines(text: str) -> str:
    """
    PDF 레이아웃 때문에 문장 중간에서 끊긴 줄을 병합한다.

    문장 종결 부호(마침표/물음표/한국어 '다' 등)로 끝나지 않은 줄은
    다음 줄과 이어붙인다. 문단 경계(빈 줄)는 보존.
    → 청크 품질과 요약기의 문장 분리 정확도가 올라간다.
    """
    paragraphs = text.split("\n\n")
    merged_paras = []
    for para in paragraphs:
        buf = ""
        for line in para.splitlines():
            s = line.strip()
            if not s:
                continue
            if not buf:
                buf = s
            elif _SENT_END_RE.search(buf):
                buf += "\n" + s          # 문장이 끝난 뒤의 개행은 유지
            else:
                buf += " " + s           # 문장 중간 개행은 공백으로 병합
        if buf:
            merged_paras.append(buf)
    return "\n\n".join(merged_paras)


# ---------------------------------------------------------------------------
# 보일러플레이트 제거 (페이지 번호 / 반복 머리글·바닥글)
# ---------------------------------------------------------------------------

# 페이지 번호 패턴: "12", "- 12 -", "— 12 —", "12 / 34", "Page 12", "12쪽", "p.12" 등
_PAGE_NUM_RE = re.compile(
    r"^\s*(?:[-–—•·]?\s*)?"
    r"(?:page|p\.?|pg\.?)?\s*"
    r"\d{1,4}\s*(?:/\s*\d{1,4})?"
    r"\s*(?:쪽|페이지)?"
    r"(?:\s*[-–—•·]?\s*)?$",
    re.IGNORECASE,
)


def _is_page_number_line(line: str) -> bool:
    line = line.strip()
    return bool(line) and len(line) <= 20 and bool(_PAGE_NUM_RE.match(line))


def _normalize_boilerplate(line: str) -> str:
    """머리글/바닥글 비교용 정규화 — 페이지 번호 등 숫자는 '#'으로 치환."""
    return re.sub(r"\d+", "#", line.strip().lower())


def _detect_boilerplate_lines(page_texts: list[str]) -> set[str]:
    """
    여러 페이지에서 반복되는 머리글/바닥글 라인을 감지한다.

    각 페이지의 상단 3줄 + 하단 3줄만 후보로 보고,
    전체 페이지의 30% 이상(최소 3페이지)에서 등장하면 보일러플레이트로 판단.
    (숫자는 정규화하므로 "보고서 12페이지" 같은 변형도 잡힘)
    """
    if len(page_texts) < 3:
        return set()

    from collections import Counter
    counter: Counter[str] = Counter()

    for text in page_texts:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        candidates = set()
        for l in lines[:3] + lines[-3:]:
            if 0 < len(l) <= 80:          # 너무 긴 줄은 본문
                candidates.add(_normalize_boilerplate(l))
        for c in candidates:
            counter[c] += 1

    threshold = max(3, int(len(page_texts) * 0.3))
    return {line for line, cnt in counter.items() if cnt >= threshold}


def _strip_boilerplate(text: str, boilerplate: set[str]) -> str:
    """페이지 번호 라인 + 반복 머리글/바닥글 라인을 제거."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if _is_page_number_line(stripped):
            continue
        if _normalize_boilerplate(stripped) in boilerplate:
            continue
        kept.append(line)
    return "\n".join(kept)


def _split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE,
                       overlap: int = CHUNK_OVERLAP) -> list[str]:
    """문단 → 문장 순으로 자연스러운 경계에서 청크 분할."""
    text = _clean_text(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text] if len(text) >= 10 else []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) + 1 <= chunk_size:
            current = f"{current}\n{para}".strip()
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(para) > chunk_size:
            sentences = re.split(r"(?<=[.!?。])\s+", para)
            buf = ""
            for sent in sentences:
                if len(buf) + len(sent) + 1 <= chunk_size:
                    buf = f"{buf} {sent}".strip()
                else:
                    if buf:
                        chunks.append(buf)
                    while len(sent) > chunk_size:
                        chunks.append(sent[:chunk_size])
                        sent = sent[chunk_size - overlap:]
                    buf = sent
            if buf:
                current = buf
        else:
            current = para

    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) >= 10]


# ---------------------------------------------------------------------------
# OCR (Tesseract) — 미설치 시에도 안전하게 동작
# ---------------------------------------------------------------------------

_ocr_checked = False
_ocr_ok = False


def is_ocr_available() -> bool:
    """Tesseract + pytesseract 사용 가능 여부 (1회만 검사)."""
    global _ocr_checked, _ocr_ok
    if _ocr_checked:
        return _ocr_ok
    _ocr_checked = True
    try:
        import pytesseract
        # Windows 기본 설치 경로 자동 탐지
        if not _shutil.which("tesseract"):
            for candidate in (
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ):
                if Path(candidate).exists():
                    pytesseract.pytesseract.tesseract_cmd = candidate
                    break
        pytesseract.get_tesseract_version()
        _ocr_ok = True
    except Exception:
        _ocr_ok = False
        logger.info("Tesseract OCR 미설치 — 스캔본 텍스트 추출은 건너뜁니다.")
    return _ocr_ok


def _get_ocr_lang() -> str:
    """설치된 언어팩에 맞춰 언어 문자열 결정 (kor 미설치 시 eng 폴백)."""
    try:
        import pytesseract
        available = set(pytesseract.get_languages(config=""))
        wanted = [l for l in OCR_LANGS.split("+") if l in available]
        return "+".join(wanted) if wanted else "eng"
    except Exception:
        return "eng"


def _ocr_page(page: "fitz.Page") -> str:
    """페이지를 이미지로 렌더링하여 OCR 텍스트 추출."""
    import pytesseract
    zoom = OCR_RENDER_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang=_get_ocr_lang())


# ---------------------------------------------------------------------------
# 표: 병합 셀 전파 + 다중 전략 + 다중 행 헤더
# ---------------------------------------------------------------------------

def _fill_merged_cells(data: list[list]) -> list[list[str]]:
    """
    병합 셀 값 전파 (★ 병합 셀 정확도 핵심).

    pdfplumber는 병합 셀의 값을 '병합 시작 셀'에만 넣고
    나머지 병합 범위는 None으로 반환한다.
    - None (병합 범위) → 값 전파
    - ""   (진짜 빈 셀) → 그대로 유지

    전파 규칙:
    1. 가로 병합: 같은 행에서 왼쪽 값 전파 (colspan)
    2. 세로 병합: 같은 열에서 위쪽 값 전파 (rowspan)
    """
    if not data:
        return []

    n_cols = max(len(row) for row in data)
    # 행 길이 통일 (열 개수 불일치 보정)
    grid: list[list] = [
        list(row) + [None] * (n_cols - len(row)) for row in data
    ]

    # 1) 가로 전파 (왼쪽 → 오른쪽)
    for row in grid:
        for c in range(1, n_cols):
            if row[c] is None and row[c - 1] is not None:
                row[c] = row[c - 1]

    # 2) 세로 전파 (위 → 아래)
    for c in range(n_cols):
        for r in range(1, len(grid)):
            if grid[r][c] is None and grid[r - 1][c] is not None:
                grid[r][c] = grid[r - 1][c]

    # 문자열화 (남은 None은 빈 문자열)
    return [
        [str(cell).strip() if cell is not None else "" for cell in row]
        for row in grid
    ]


def _detect_header_rows(data: list[list[str]]) -> int:
    """
    다중 행 헤더 감지: 첫 행에 중복 값(가로 병합 흔적)이 있고
    둘째 행이 서로 다른 값으로 채워져 있으면 2행 헤더로 판단.
    """
    if len(data) < 3:
        return 1
    row0, row1 = data[0], data[1]
    non_empty0 = [c for c in row0 if c]
    # 첫 행에 같은 값이 연속으로 반복되면 (가로 병합) 다중 헤더 가능성
    has_repeat = len(non_empty0) != len(set(non_empty0))
    row1_filled = sum(1 for c in row1 if c) >= max(1, len(row1) // 2)
    # 둘째 행이 숫자 위주면 데이터 행 → 단일 헤더
    row1_numeric = sum(
        1 for c in row1 if c and re.fullmatch(r"[\d,.%\-+~\s]+", c)
    ) > len(row1) // 2
    if has_repeat and row1_filled and not row1_numeric:
        return 2
    return 1


def _merge_header_rows(data: list[list[str]], n_header: int) -> list[list[str]]:
    """다중 행 헤더를 1행으로 병합. 예: '2024' + '매출' → '2024 매출'"""
    if n_header <= 1 or len(data) <= n_header:
        return data
    n_cols = len(data[0])
    merged_header = []
    for c in range(n_cols):
        parts = []
        for r in range(n_header):
            val = data[r][c].strip() if c < len(data[r]) else ""
            if val and val not in parts:   # 중복 방지 (병합 전파된 동일 값)
                parts.append(val)
        merged_header.append(" ".join(parts))
    return [merged_header] + data[n_header:]


def _table_to_text(data: list[list[str]]) -> str:
    """표를 '헤더: 값' 형태의 검색용 텍스트로 변환."""
    if not data:
        return ""
    header = [str(h or "").strip() for h in data[0]]
    lines = [" | ".join(h for h in header if h)]
    for row in data[1:]:
        cells = []
        for i, cell in enumerate(row):
            cell_text = str(cell or "").strip()
            if not cell_text:
                continue
            if i < len(header) and header[i] and header[i] != cell_text:
                cells.append(f"{header[i]}: {cell_text}")
            else:
                cells.append(cell_text)
        if cells:
            lines.append(", ".join(cells))
    return "\n".join(lines)


def _is_meaningful_table(data: list[list[str]]) -> bool:
    if len(data) < MIN_TABLE_ROWS:
        return False
    non_empty = sum(1 for row in data for cell in row if cell)
    total = sum(len(row) for row in data)
    return total > 0 and non_empty / total >= 0.25 and non_empty >= 4


# 표 추출 전략: lines(테두리 있는 표) → text(테두리 없는 표) 순서로 시도
_TABLE_STRATEGIES = [
    {"vertical_strategy": "lines", "horizontal_strategy": "lines",
     "snap_tolerance": 4, "join_tolerance": 4},
    {"vertical_strategy": "text", "horizontal_strategy": "text",
     "snap_tolerance": 4, "intersection_tolerance": 6},
]


def _extract_tables_from_page(page) -> list[list[list]]:
    """다중 전략으로 표 추출. lines 전략 성공 시 그 결과만 사용."""
    for settings in _TABLE_STRATEGIES:
        try:
            raw_tables = page.extract_tables(table_settings=settings)
        except Exception:
            continue
        valid = [t for t in (raw_tables or []) if t and len(t) >= MIN_TABLE_ROWS]
        if valid:
            return valid
    return []


def _process_raw_table(raw: list[list]) -> tuple[list[list[str]], str] | None:
    """원시 표 → 병합 셀 전파 → 헤더 보정 → (data, text). 무의미하면 None."""
    data = _fill_merged_cells(raw)
    if not _is_meaningful_table(data):
        return None
    n_header = _detect_header_rows(data)
    data = _merge_header_rows(data, n_header)
    text = _table_to_text(data)
    if not text.strip():
        return None
    return data, text


# ---------------------------------------------------------------------------
# 벡터 그래픽 (선/도형 차트) 감지 및 렌더링
# ---------------------------------------------------------------------------

def _merge_rects(rects: list[fitz.Rect], margin: float = 15.0) -> list[fitz.Rect]:
    """겹치거나 인접한 사각형들을 병합해 그래픽 영역 클러스터 생성."""
    merged = [fitz.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        result: list[fitz.Rect] = []
        for r in merged:
            expanded = fitz.Rect(r.x0 - margin, r.y0 - margin,
                                 r.x1 + margin, r.y1 + margin)
            hit = None
            for i, existing in enumerate(result):
                if expanded.intersects(existing):
                    hit = i
                    break
            if hit is not None:
                result[hit] |= r   # union
                changed = True
            else:
                result.append(fitz.Rect(r))
        merged = result
    return merged


def _extract_vector_regions(page: "fitz.Page") -> list[fitz.Rect]:
    """벡터 드로잉(차트/다이어그램)이 밀집된 영역을 찾는다."""
    try:
        drawings = page.get_drawings()
    except Exception:
        return []
    if len(drawings) < 5:   # 드로잉이 몇 개 없으면 장식(밑줄 등)일 가능성
        return []

    rects = []
    for d in drawings:
        r = d.get("rect")
        if r and r.width > 1 and r.height > 1:
            rects.append(r)
    if not rects:
        return []

    regions = _merge_rects(rects)
    page_area = abs(page.rect) or 1.0

    valid = []
    for r in regions:
        # 너무 작은 영역(밑줄/괘선) 제외
        if r.width < VECTOR_MIN_SIZE_PT or r.height < VECTOR_MIN_SIZE_PT:
            continue
        # 페이지 전체를 덮는 배경 프레임 제외
        if abs(r) / page_area > VECTOR_MAX_AREA_RATIO:
            continue
        # 페이지 밖으로 나간 부분 클리핑
        r = r & page.rect
        if r.is_empty:
            continue
        valid.append(r)

    # 영역 크기순 상위 N개
    valid.sort(key=lambda r: abs(r), reverse=True)
    return valid[:VECTOR_MAX_PER_PAGE]


# ---------------------------------------------------------------------------
# 메인 파싱 함수
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: str | Path, document_key: str) -> ParseResult:
    """
    PDF를 파싱하여 모든 요소(텍스트/이미지/벡터그래픽/표/메타데이터/목차/링크/주석)를 추출.

    Args:
        pdf_path: PDF 파일 경로
        document_key: 이미지 저장 폴더명으로 쓸 고유 키 (파일 해시 앞 12자리)
    """
    pdf_path = Path(pdf_path)
    result = ParseResult()
    result.ocr_available = is_ocr_available()

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        result.errors.append(f"PDF 열기 실패: {e}")
        return result

    result.page_count = len(doc)
    image_dir = IMAGE_DIR / document_key
    image_dir.mkdir(parents=True, exist_ok=True)

    # ---------- 메타데이터 ----------
    try:
        meta = doc.metadata or {}
        result.metadata = {
            k: v for k, v in {
                "title": meta.get("title", ""),
                "author": meta.get("author", ""),
                "subject": meta.get("subject", ""),
                "keywords": meta.get("keywords", ""),
                "creator": meta.get("creator", ""),
                "creation_date": meta.get("creationDate", ""),
            }.items() if v
        }
    except Exception as e:
        result.errors.append(f"메타데이터 추출 실패: {e}")

    # ---------- 목차 (북마크) ----------
    try:
        for level, title, page_no in (doc.get_toc(simple=True) or []):
            title = (title or "").strip()
            if title:
                result.outlines.append(
                    ParsedOutline(level=level, title=title,
                                  page_number=max(page_no, 1))
                )
    except Exception as e:
        result.errors.append(f"목차 추출 실패: {e}")

    # ---------- 페이지별: 텍스트/OCR, 이미지, 벡터그래픽, 링크, 주석 ----------
    # 텍스트는 (1) 전 페이지 수집 → (2) 반복 머리글/바닥글 감지 → (3) 제거 후 청킹
    page_texts: list[tuple[int, str, str]] = []   # (page_no, text, source)

    for page_idx in range(len(doc)):
        page_no = page_idx + 1
        try:
            page = doc[page_idx]
        except Exception as e:
            result.errors.append(f"p{page_no} 페이지 로드 실패: {e}")
            continue

        # --- 텍스트 수집 (스캔본이면 OCR) ---
        try:
            raw_text = page.get_text("text")
            source = "native"
            if (len(raw_text.strip()) < OCR_TEXT_THRESHOLD
                    and result.ocr_available):
                try:
                    ocr_text = _ocr_page(page)
                    if len(ocr_text.strip()) > len(raw_text.strip()):
                        raw_text = ocr_text
                        source = "ocr"
                        result.ocr_pages.append(page_no)
                except Exception as e:
                    result.errors.append(f"p{page_no} OCR 실패: {e}")
            page_texts.append((page_no, _normalize_text(raw_text), source))
        except Exception as e:
            result.errors.append(f"p{page_no} 텍스트 추출 실패: {e}")

        # --- 임베디드 이미지 ---
        try:
            seen_xrefs: set[int] = set()
            for img_idx, img_info in enumerate(page.get_images(full=True)):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    extracted = doc.extract_image(xref)
                    pil = Image.open(io.BytesIO(extracted["image"]))
                    w, h = pil.size
                    if w < MIN_IMAGE_SIZE or h < MIN_IMAGE_SIZE:
                        continue
                    if pil.mode not in ("RGB", "L"):
                        pil = pil.convert("RGB")
                    filename = f"p{page_no}_img{img_idx}.png"
                    pil.save(image_dir / filename, format="PNG")
                    result.images.append(
                        ParsedImage(page_number=page_no,
                                    image_path=f"{document_key}/{filename}",
                                    width=w, height=h, kind="image")
                    )
                except Exception as e:
                    result.errors.append(f"p{page_no} 이미지 {img_idx} 추출 실패: {e}")
        except Exception as e:
            result.errors.append(f"p{page_no} 이미지 목록 조회 실패: {e}")

        # --- 벡터 그래픽 (차트/다이어그램) ---
        try:
            for v_idx, region in enumerate(_extract_vector_regions(page)):
                try:
                    mat = fitz.Matrix(VECTOR_RENDER_ZOOM, VECTOR_RENDER_ZOOM)
                    pix = page.get_pixmap(matrix=mat, clip=region)
                    if pix.width < MIN_IMAGE_SIZE or pix.height < MIN_IMAGE_SIZE:
                        continue
                    filename = f"p{page_no}_vec{v_idx}.png"
                    pix.save(str(image_dir / filename))
                    result.images.append(
                        ParsedImage(page_number=page_no,
                                    image_path=f"{document_key}/{filename}",
                                    width=pix.width, height=pix.height,
                                    kind="vector")
                    )
                except Exception as e:
                    result.errors.append(f"p{page_no} 벡터영역 {v_idx} 렌더링 실패: {e}")
        except Exception as e:
            result.errors.append(f"p{page_no} 벡터 그래픽 감지 실패: {e}")

        # --- 하이퍼링크 ---
        try:
            for link in page.get_links():
                uri = link.get("uri", "")
                if not uri:
                    continue
                anchor = ""
                try:
                    rect = link.get("from")
                    if rect:
                        anchor = page.get_textbox(rect).strip()[:200]
                except Exception:
                    pass
                result.links.append(
                    ParsedLink(page_number=page_no, url=uri, anchor_text=anchor)
                )
        except Exception as e:
            result.errors.append(f"p{page_no} 링크 추출 실패: {e}")

        # --- 주석 (형광펜/메모 등) ---
        try:
            for annot in (page.annots() or []):
                try:
                    a_type = annot.type[1] if annot.type else "Unknown"
                    content = (annot.info.get("content") or "").strip()
                    # 형광펜/밑줄은 content가 없으면 하이라이트된 본문 텍스트 추출
                    if not content and a_type in ("Highlight", "Underline",
                                                  "Squiggly", "StrikeOut"):
                        try:
                            content = page.get_textbox(annot.rect).strip()
                        except Exception:
                            content = ""
                    if content:
                        result.annotations.append(
                            ParsedAnnotation(page_number=page_no,
                                             annot_type=a_type,
                                             content=content[:1000])
                        )
                except Exception:
                    continue
        except Exception as e:
            result.errors.append(f"p{page_no} 주석 추출 실패: {e}")

    doc.close()

    # ---------- 텍스트 정제 + 청킹 (보일러플레이트 제거 후) ----------
    try:
        boilerplate = _detect_boilerplate_lines([t for _, t, _ in page_texts])
    except Exception as e:
        boilerplate = set()
        result.errors.append(f"머리글/바닥글 감지 실패: {e}")

    for page_no, raw_text, source in page_texts:
        try:
            cleaned = _strip_boilerplate(raw_text, boilerplate)
            cleaned = _join_wrapped_lines(cleaned)
            for chunk_idx, chunk in enumerate(_split_into_chunks(cleaned)):
                result.chunks.append(
                    ParsedChunk(page_number=page_no, chunk_index=chunk_idx,
                                content=chunk, source=source)
                )
        except Exception as e:
            result.errors.append(f"p{page_no} 텍스트 청킹 실패: {e}")

    # ---------- 표 (pdfplumber, 병합 셀 처리) ----------
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                page_no = page_idx + 1
                try:
                    raw_tables = _extract_tables_from_page(page)
                except Exception as e:
                    result.errors.append(f"p{page_no} 표 추출 실패: {e}")
                    continue
                for t_idx, raw in enumerate(raw_tables):
                    try:
                        processed = _process_raw_table(raw)
                        if processed is None:
                            continue
                        data, text = processed
                        result.tables.append(
                            ParsedTable(page_number=page_no, table_index=t_idx,
                                        data=data, text=text)
                        )
                    except Exception as e:
                        result.errors.append(f"p{page_no} 표 {t_idx} 처리 실패: {e}")
    except Exception as e:
        result.errors.append(f"pdfplumber 표 추출 단계 실패: {e}")

    return result
