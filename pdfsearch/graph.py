"""
지식 그래프 빌더 — 프로젝트 DB의 문서들이 어떻게 연결되는지 계산한다.

동작 원리:
- 노드 = 문서 (documents 테이블)
- 엣지 = 문서 쌍의 의미 유사도 (FAISS에 저장된 청크 벡터의 평균 → 코사인 유사도)
  + 연결 근거로 두 문서가 공유하는 핵심 키워드(TF-IDF 기반)를 함께 제공
- 임베딩 벡터가 없는 문서(청크 미인덱싱)는 키워드 자카드 유사도로 폴백

★ 모델을 로드하지 않는다. FAISS 인덱스의 `reconstruct()` 로 저장된 벡터를
  꺼내 쓰기 때문에 모델 미다운로드 상태에서도 그래프를 만들 수 있다.
"""
import logging
import math
import re
from collections import Counter
from datetime import datetime

import numpy as np

from . import database as db
from . import search as search_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 키워드 추출 유틸
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[가-힣]{2,}|[A-Za-z][A-Za-z\-]{1,}|[0-9]{4}")

# 조사/어미 (긴 것부터 제거 시도)
_JOSA = (
    "으로부터", "에서부터", "이라는", "라는", "에서는", "으로는", "으로써", "으로서",
    "에게서", "에서", "에게", "께서", "부터", "까지", "으로", "이나", "이며", "하고",
    "와의", "과의", "들의", "들은", "들을", "들이", "은", "는", "이", "가", "을", "를",
    "의", "에", "와", "과", "도", "만", "로", "요",
)

_STOPWORDS = {
    # 한국어 일반어
    "있다", "있는", "있으며", "있습니다", "한다", "하는", "하며", "합니다", "했다",
    "된다", "되는", "됩니다", "되어", "대한", "대해", "위한", "위해", "통해", "따라",
    "경우", "그리고", "하지만", "또한", "또는", "등의", "이러한", "그러한", "것이",
    "것은", "것을", "것으로", "수있다", "같은", "같이", "때문", "관련", "기타",
    "이상", "이하", "미만", "초과", "각각", "모든", "해당", "다음", "아래", "위의",
    "내용", "사항", "부분", "정도", "우리", "여기", "저기", "지금", "오늘",
    # 영어 일반어
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were",
    "has", "have", "had", "not", "but", "can", "will", "its", "all", "use",
    "using", "used", "may", "one", "two", "three", "per", "etc", "into", "than",
    "then", "when", "where", "which", "while", "also", "each", "such", "these",
    "those", "more", "most", "some", "any", "other", "about", "over", "under",
    "page", "pages", "http", "https", "www", "com", "org", "figure", "table",
    "fig", "vol", "no", "pp",
}


def _normalize_token(token: str) -> str:
    """소문자화 + 한국어 조사 제거 (아주 가벼운 휴리스틱)."""
    t = token.lower()
    if re.match(r"^[가-힣]", t):
        for josa in _JOSA:
            if t.endswith(josa) and len(t) - len(josa) >= 2:
                return t[: -len(josa)]
    return t


def _tokenize(text: str) -> list[str]:
    tokens = []
    for raw in _TOKEN_RE.findall(text):
        t = _normalize_token(raw)
        if len(t) >= 2 and t not in _STOPWORDS:
            tokens.append(t)
    return tokens


# ---------------------------------------------------------------------------
# 지식 그래프 빌더
# ---------------------------------------------------------------------------

class KnowledgeGraphBuilder:
    """
    프로젝트 DB → 지식 그래프(JSON) 생성기.

    Args:
        similarity_threshold: 이 값 미만의 유사도는 엣지로 만들지 않음
        max_edges_per_node:   노드당 최대 엣지 수 (그래프 과밀 방지)
        top_keywords:         문서별로 추출할 핵심 키워드 수
        shared_keywords:      엣지에 표시할 공유 키워드 수
        max_chunks_per_doc:   문서 벡터 계산에 사용할 최대 청크 수
    """

    def __init__(
        self,
        similarity_threshold: float = 0.35,
        max_edges_per_node: int = 4,
        top_keywords: int = 10,
        shared_keywords: int = 5,
        max_chunks_per_doc: int = 200,
    ):
        self.similarity_threshold = similarity_threshold
        self.max_edges_per_node = max_edges_per_node
        self.top_keywords = top_keywords
        self.shared_keywords = shared_keywords
        self.max_chunks_per_doc = max_chunks_per_doc

    # ---------------- 공개 API ----------------

    def build(self) -> dict:
        """그래프 전체를 생성해 dict(JSON 직렬화 가능)로 반환한다."""
        docs = db.list_documents()
        if not docs:
            return {"nodes": [], "edges": [], "generated_at": self._now()}

        doc_ids = [d["id"] for d in docs]
        vectors = self._document_vectors(doc_ids)
        keywords, kw_scores = self._document_keywords(doc_ids)

        nodes = [
            {
                "id": d["id"],
                "label": d["filename"],
                "page_count": d["page_count"],
                "chunk_count": d.get("chunk_count", 0),
                "image_count": d.get("image_count", 0),
                "table_count": d.get("table_count", 0),
                "keywords": keywords.get(d["id"], []),
                "has_vector": d["id"] in vectors,
            }
            for d in docs
        ]
        edges = self._build_edges(doc_ids, vectors, kw_scores)

        return {"nodes": nodes, "edges": edges, "generated_at": self._now()}

    # ---------------- 내부 구현 ----------------

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _document_vectors(self, doc_ids: list[int]) -> dict[int, np.ndarray]:
        """문서별 대표 벡터 = 청크 벡터(FAISS reconstruct)의 평균 (L2 정규화)."""
        index = search_engine.get_text_index()
        vectors: dict[int, np.ndarray] = {}
        if index.ntotal == 0:
            return vectors

        for doc_id in doc_ids:
            chunks = db.get_chunks_by_document(doc_id)
            faiss_ids = [
                c["faiss_id"] for c in chunks
                if c["faiss_id"] is not None
            ][: self.max_chunks_per_doc]

            arr = []
            for fid in faiss_ids:
                fid = int(fid)
                if 0 <= fid < index.ntotal:
                    try:
                        arr.append(index.reconstruct(fid))
                    except Exception:  # noqa: BLE001 — 개별 벡터 실패는 무시
                        pass
            if not arr:
                continue

            vec = np.mean(np.asarray(arr, dtype=np.float32), axis=0)
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vectors[doc_id] = vec / norm
        return vectors

    def _document_keywords(
        self, doc_ids: list[int]
    ) -> tuple[dict[int, list[str]], dict[int, dict[str, float]]]:
        """문서별 핵심 키워드 (TF-IDF). → (키워드 목록, 키워드 점수 맵)"""
        doc_counters: dict[int, Counter] = {}
        document_freq: Counter = Counter()

        for doc_id in doc_ids:
            chunks = db.get_chunks_by_document(doc_id)
            text = " ".join(c["content"] for c in chunks)
            counter = Counter(_tokenize(text))
            doc_counters[doc_id] = counter
            for term in counter:
                document_freq[term] += 1

        n_docs = max(len(doc_ids), 1)
        keywords: dict[int, list[str]] = {}
        kw_scores: dict[int, dict[str, float]] = {}

        for doc_id, counter in doc_counters.items():
            total = sum(counter.values()) or 1
            scored: dict[str, float] = {}
            for term, count in counter.items():
                tf = count / total
                idf = math.log((n_docs + 1) / (document_freq[term] + 0.5)) + 1.0
                scored[term] = tf * idf
            top = sorted(scored.items(), key=lambda x: x[1], reverse=True)
            # 공유 키워드 탐지용으로 여유 있게 저장, 표시용은 top_keywords개
            kw_scores[doc_id] = dict(top[: self.top_keywords * 3])
            keywords[doc_id] = [t for t, _ in top[: self.top_keywords]]
        return keywords, kw_scores

    def _build_edges(
        self,
        doc_ids: list[int],
        vectors: dict[int, np.ndarray],
        kw_scores: dict[int, dict[str, float]],
    ) -> list[dict]:
        """문서 쌍별 유사도 → 엣지 목록 (노드당 max_edges_per_node개 제한)."""
        candidates: list[dict] = []

        for i, a in enumerate(doc_ids):
            for b in doc_ids[i + 1:]:
                if a in vectors and b in vectors:
                    sim = float(np.dot(vectors[a], vectors[b]))
                    basis = "embedding"
                else:
                    # 벡터가 없으면 키워드 자카드 유사도로 폴백
                    set_a, set_b = set(kw_scores.get(a, {})), set(kw_scores.get(b, {}))
                    union = set_a | set_b
                    sim = len(set_a & set_b) / len(union) if union else 0.0
                    basis = "keyword"

                if sim < self.similarity_threshold:
                    continue

                shared = sorted(
                    set(kw_scores.get(a, {})) & set(kw_scores.get(b, {})),
                    key=lambda t: kw_scores[a].get(t, 0) + kw_scores[b].get(t, 0),
                    reverse=True,
                )[: self.shared_keywords]

                candidates.append({
                    "source": a,
                    "target": b,
                    "weight": round(min(max(sim, 0.0), 1.0), 4),
                    "basis": basis,
                    "keywords": shared,
                })

        # 강한 연결부터 채택하되 노드당 엣지 수 제한 (과밀 방지)
        candidates.sort(key=lambda e: e["weight"], reverse=True)
        degree: Counter = Counter()
        edges: list[dict] = []
        for e in candidates:
            if (degree[e["source"]] >= self.max_edges_per_node
                    and degree[e["target"]] >= self.max_edges_per_node):
                continue
            edges.append(e)
            degree[e["source"]] += 1
            degree[e["target"]] += 1
        return edges


def build_graph() -> dict:
    """기본 설정으로 지식 그래프 생성 (편의 함수)."""
    return KnowledgeGraphBuilder().build()
