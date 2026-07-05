"""
지식 그래프 추출기 (P1.5 + P2).

3계층 추출 전략 (ROADMAP §1.5.3):
1. 구조 기반 (rule)   : 메타데이터/목차/표/링크 → 고신뢰 엔티티·관계 (모델 불필요)
2. 정규식 기반 (regex): 날짜/수치(Metric) 패턴 → 중간 신뢰도 (모델 불필요)
3. GLiNER (선택)      : 제로샷 NER — `pdfsearch models --kg` 로 설치한 경우에만

각 추출 결과는 (엔티티명, 타입, 신뢰도, 출처) 형태의 Candidate로 통일된다.
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ontology import Ontology

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 추출 결과 타입
# ---------------------------------------------------------------------------

@dataclass
class EntityCandidate:
    name: str                    # 정규화된 대표명
    entity_type: str             # 온톨로지 클래스
    confidence: float            # 추출 신뢰도 (0~1)
    span_text: str               # 원문 표현
    page_number: int = 0
    chunk_id: int | None = None
    extractor: str = "rule"      # rule | regex | gliner


@dataclass
class RelationCandidate:
    source: tuple[str, str]      # (name, type)
    relation: str
    target: tuple[str, str]      # (name, type)
    confidence: float
    extractor: str = "rule"


@dataclass
class ExtractionResult:
    entities: list[EntityCandidate] = field(default_factory=list)
    relations: list[RelationCandidate] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 정규화 유틸
# ---------------------------------------------------------------------------

def normalize_name(raw: str) -> str:
    """엔티티 대표명 정규화 (공백 정리, 양끝 구두점 제거)."""
    s = re.sub(r"\s+", " ", raw).strip()
    s = s.strip("\"'()[]{}<>·,.:;")
    return s


_DATE_RE = re.compile(
    r"(?:(?:19|20)\d{2}[.\-/년]\s?(?:0?[1-9]|1[0-2])(?:[.\-/월]\s?"
    r"(?:0?[1-9]|[12]\d|3[01])일?)?)|(?:(?:19|20)\d{2}년)|(?:\d\s?분기)"
)

_METRIC_RE = re.compile(
    r"\d[\d,.]*\s?(?:조|억|만|천)?\s?"
    r"(?:원|달러|명|개|건|%|퍼센트|p|bp|배|시간|분|초|톤|kg|km|MW|GW|TB|GB)"
)


def _norm_date(s: str) -> str:
    """날짜 문자열 정규화 (구분자 통일)."""
    return re.sub(r"[.\-/]", ".", re.sub(r"\s", "", s)).rstrip(".")


# ===========================================================================
# 1. 구조 기반 추출 (P1.5) — 파싱 산출물 → 온톨로지 직결
# ===========================================================================

class StructureExtractor:
    """
    파서가 만든 구조(메타데이터/목차/표/링크)에서 엔티티·관계를 뽑는다.
    LLM/NER 0회 호출, 저자가 만든 구조라 신뢰도가 높다 (ROADMAP §1.5.1).
    """

    # 신뢰도 상수 (구조별 근거 강도)
    CONF_METADATA = 0.95    # PDF 메타데이터 (기계 기록)
    CONF_OUTLINE = 0.90     # 목차 (저자가 직접 만든 계층)
    CONF_TABLE = 0.75       # 표 헤더/셀 (구조는 확실, 의미 해석에 여지)
    CONF_LINK = 0.85        # 하이퍼링크

    def extract(self, document: dict, outlines: list[dict],
                tables: list[dict], links: list[dict]) -> ExtractionResult:
        res = ExtractionResult()
        doc_name = document["filename"]
        doc_entity = (doc_name, "Document")

        # 문서 자신도 엔티티다
        res.entities.append(EntityCandidate(
            name=doc_name, entity_type="Document", confidence=1.0,
            span_text=doc_name, extractor="rule"))

        self._from_metadata(document, doc_entity, res)
        self._from_outlines(outlines, doc_entity, res)
        self._from_tables(tables, doc_entity, res)
        self._from_links(links, doc_entity, res)
        return res

    # ----- 메타데이터: 저자 → Person, 생성일 → Date -----

    def _from_metadata(self, document: dict, doc_entity, res) -> None:
        import json
        try:
            meta = json.loads(document.get("metadata_json") or "{}")
        except Exception:
            meta = {}

        author = normalize_name(str(meta.get("author") or ""))
        # 의미 없는 저자 값 제외 (소프트웨어 이름 등은 그대로 두되 빈 값만 거름)
        if author and len(author) >= 2:
            res.entities.append(EntityCandidate(
                name=author, entity_type="Person",
                confidence=self.CONF_METADATA, span_text=author,
                extractor="rule"))
            res.relations.append(RelationCandidate(
                source=doc_entity, relation="authored_by",
                target=(author, "Person"),
                confidence=self.CONF_METADATA, extractor="rule"))

        created = str(meta.get("creation_date") or meta.get("creationDate") or "")
        m = _DATE_RE.search(created)
        if m:
            date_name = _norm_date(m.group())
            res.entities.append(EntityCandidate(
                name=date_name, entity_type="Date",
                confidence=self.CONF_METADATA, span_text=created,
                extractor="rule"))
            res.relations.append(RelationCandidate(
                source=doc_entity, relation="created_on",
                target=(date_name, "Date"),
                confidence=self.CONF_METADATA, extractor="rule"))

    # ----- 목차: 제목 → Concept, 들여쓰기 → part_of 계층 -----

    def _from_outlines(self, outlines: list[dict], doc_entity, res) -> None:
        stack: list[tuple[int, str]] = []  # (level, concept_name)
        for o in outlines:
            title = normalize_name(re.sub(r"^[\d.\s장절편부록]+", "", o["title"]))
            if len(title) < 2:
                continue
            level = int(o.get("level") or 1)

            res.entities.append(EntityCandidate(
                name=title, entity_type="Concept",
                confidence=self.CONF_OUTLINE, span_text=o["title"],
                page_number=o.get("page_number", 0), extractor="rule"))
            res.relations.append(RelationCandidate(
                source=doc_entity, relation="mentions",
                target=(title, "Concept"),
                confidence=self.CONF_OUTLINE, extractor="rule"))

            # 상위 목차와 part_of 연결
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                parent = stack[-1][1]
                res.relations.append(RelationCandidate(
                    source=(title, "Concept"), relation="part_of",
                    target=(parent, "Concept"),
                    confidence=self.CONF_OUTLINE, extractor="rule"))
            stack.append((level, title))

    # ----- 표: 헤더 → Concept, 수치 셀 → Metric, 같은 행 → measured_as -----

    def _from_tables(self, tables: list[dict], doc_entity, res) -> None:
        import json
        for t in tables:
            try:
                data = json.loads(t["table_json"])
            except Exception:
                continue
            if not data or len(data) < 2:
                continue
            page = t.get("page_number", 0)
            headers = [normalize_name(str(h or "")) for h in data[0]]

            for row in data[1:]:
                cells = [str(c or "").strip() for c in row]
                if not cells:
                    continue
                # 첫 열 = 행 라벨(개념 후보)
                row_label = normalize_name(cells[0])
                label_ok = 2 <= len(row_label) <= 40 and not _METRIC_RE.fullmatch(row_label)
                if label_ok:
                    res.entities.append(EntityCandidate(
                        name=row_label, entity_type="Concept",
                        confidence=self.CONF_TABLE, span_text=cells[0],
                        page_number=page, extractor="rule"))

                for j, cell in enumerate(cells[1:], start=1):
                    m = _METRIC_RE.search(cell)
                    if not m:
                        continue
                    header = headers[j] if j < len(headers) else ""
                    metric_name = m.group().strip()
                    if header:
                        metric_name = f"{header} {metric_name}"
                    res.entities.append(EntityCandidate(
                        name=metric_name, entity_type="Metric",
                        confidence=self.CONF_TABLE, span_text=cell,
                        page_number=page, extractor="rule"))
                    if label_ok:
                        res.relations.append(RelationCandidate(
                            source=(row_label, "Concept"),
                            relation="measured_as",
                            target=(metric_name, "Metric"),
                            confidence=self.CONF_TABLE, extractor="rule"))

    # ----- 링크: 다른 수집 문서를 가리키면 references -----

    def _from_links(self, links: list[dict], doc_entity, res) -> None:
        from .database import list_documents
        known = {d["filename"].lower(): d["filename"] for d in list_documents()}
        for l in links:
            url = str(l.get("url") or "")
            fname = Path(url.split("?")[0].split("#")[0]).name.lower()
            if fname and fname in known:
                res.relations.append(RelationCandidate(
                    source=doc_entity, relation="references",
                    target=(known[fname], "Document"),
                    confidence=self.CONF_LINK, extractor="rule"))


# ===========================================================================
# 2. 정규식 기반 추출 — 본문에서 Date/Metric
# ===========================================================================

class RegexExtractor:
    CONF_DATE = 0.80
    CONF_METRIC = 0.65

    MAX_PER_CHUNK = 5   # 청크당 과다 추출 방지

    def extract_chunk(self, chunk: dict) -> list[EntityCandidate]:
        text = chunk["content"]
        out: list[EntityCandidate] = []

        for m in list(_DATE_RE.finditer(text))[: self.MAX_PER_CHUNK]:
            out.append(EntityCandidate(
                name=_norm_date(m.group()), entity_type="Date",
                confidence=self.CONF_DATE, span_text=m.group(),
                page_number=chunk["page_number"], chunk_id=chunk["id"],
                extractor="regex"))

        for m in list(_METRIC_RE.finditer(text))[: self.MAX_PER_CHUNK]:
            name = m.group().strip()
            if len(name) < 2:
                continue
            out.append(EntityCandidate(
                name=name, entity_type="Metric",
                confidence=self.CONF_METRIC, span_text=name,
                page_number=chunk["page_number"], chunk_id=chunk["id"],
                extractor="regex"))
        return out


# ===========================================================================
# 3. GLiNER 추출 (선택 — 설치/다운로드된 경우에만)
# ===========================================================================

# GLiNER 라벨 → 온톨로지 클래스 매핑 (한국어 라벨이 다국어 모델에서 더 잘 동작)
GLINER_LABELS: dict[str, str] = {
    "사람": "Person",
    "조직/회사/기관": "Organization",
    "장소/지역": "Location",
    "제품/서비스/시스템": "Product",
    "기술/개념/방법론": "Concept",
    "사건/행사": "Event",
}

GLINER_MODEL_NAME = "urchade/gliner_multi-v2.1"

_gliner_model = None
_gliner_failed = False


def is_gliner_available() -> bool:
    """gliner 패키지가 설치되어 있는지 (모델 로드는 하지 않음)."""
    try:
        import gliner  # noqa: F401
        return True
    except ImportError:
        return False


def _get_gliner():
    """GLiNER 모델 지연 로딩 (미설치/실패 시 None)."""
    global _gliner_model, _gliner_failed
    if _gliner_model is not None or _gliner_failed:
        return _gliner_model
    try:
        from gliner import GLiNER
        from .config import MODELS_DIR
        import os
        os.environ.setdefault("HF_HOME", str(MODELS_DIR))
        logger.info("GLiNER 모델 로딩: %s", GLINER_MODEL_NAME)
        _gliner_model = GLiNER.from_pretrained(
            GLINER_MODEL_NAME, cache_dir=str(MODELS_DIR / "hub"))
    except Exception as e:
        logger.warning("GLiNER 사용 불가: %s", e)
        _gliner_failed = True
    return _gliner_model


class GlinerExtractor:
    """제로샷 NER — 온톨로지 클래스명을 라벨로 넘겨 추출 확률까지 얻는다."""

    THRESHOLD = 0.4   # GLiNER 자체 임계값 (낮게 잡고 신뢰도로 필터)

    def available(self) -> bool:
        return _get_gliner() is not None

    def extract_chunk(self, chunk: dict) -> list[EntityCandidate]:
        model = _get_gliner()
        if model is None:
            return []
        text = chunk["content"][:1500]  # GLiNER 입력 길이 제한 대비
        try:
            preds = model.predict_entities(
                text, list(GLINER_LABELS.keys()), threshold=self.THRESHOLD)
        except Exception as e:
            logger.warning("GLiNER 추출 실패 (chunk %s): %s", chunk["id"], e)
            return []

        out: list[EntityCandidate] = []
        for p in preds:
            name = normalize_name(p["text"])
            etype = GLINER_LABELS.get(p["label"])
            if not etype or len(name) < 2 or len(name) > 60:
                continue
            out.append(EntityCandidate(
                name=name, entity_type=etype,
                confidence=round(float(p["score"]), 4), span_text=p["text"],
                page_number=chunk["page_number"], chunk_id=chunk["id"],
                extractor="gliner"))
        return out


# ===========================================================================
# 클래스 신뢰도 (임베딩 유사도 — 기존 텍스트 모델 재활용, ROADMAP §2.2-②)
# ===========================================================================

def class_confidence(names: list[str], entity_types: list[str],
                     ontology: Ontology) -> list[float]:
    """
    엔티티명 임베딩 vs 클래스 설명문 임베딩의 코사인 유사도.
    임베딩 모델이 없으면 중립값 0.5.
    """
    try:
        from .embeddings import embed_texts, models_ready
        if not models_ready()["text_model"] or not names:
            return [0.5] * len(names)
        import numpy as np
        type_list = list(ontology.entity_types.keys())
        type_vecs = embed_texts(
            [f"{t}: {ontology.entity_types[t]}" for t in type_list])
        name_vecs = embed_texts(names)
        sims = name_vecs @ type_vecs.T          # (N, T) 코사인 유사도
        out = []
        for i, et in enumerate(entity_types):
            if et in type_list:
                j = type_list.index(et)
                # 해당 타입 유사도를 [0,1]로 매핑 + softmax 없이 순위 보정
                raw = float(sims[i, j])
                best = float(np.max(sims[i]))
                score = 0.5 + 0.5 * raw          # 코사인 [-1,1] → [0,1]
                if raw >= best - 1e-6:           # 가장 유사한 타입이면 보너스
                    score = min(1.0, score + 0.1)
                out.append(round(max(0.0, min(1.0, score)), 4))
            else:
                out.append(0.5)
        return out
    except Exception as e:
        logger.warning("클래스 신뢰도 계산 실패(중립값 사용): %s", e)
        return [0.5] * len(names)
