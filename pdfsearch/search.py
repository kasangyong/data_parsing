"""
FAISS 인덱스 관리 + 통합 검색.

- 텍스트 인덱스 (384차원): 텍스트 청크 + 표(텍스트화) 공유
- 이미지 인덱스 (512차원): CLIP 이미지 임베딩
- IndexFlatIP + L2 정규화 벡터 → 내적 = 코사인 유사도
- faiss_id = 인덱스 내 순번 (ntotal 기준 증가), DB 매핑 테이블로 실제 요소와 연결
- 문서 삭제 시 벡터는 남지만 매핑이 사라짐 → 검색 시 자동 무시 (소프트 삭제)
"""
import json
import logging
import threading
from pathlib import Path

import faiss
import numpy as np

from . import database as db
from .config import (
    IMAGE_EMBED_DIM,
    IMAGE_INDEX_PATH,
    RESULTS_PER_QUERY,
    SEARCH_TOP_K,
    TEXT_EMBED_DIM,
    TEXT_INDEX_PATH,
)
from .embeddings import embed_query_for_image, embed_query_for_text

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_text_index: faiss.Index | None = None
_image_index: faiss.Index | None = None


# ---------------------------------------------------------------------------
# 인덱스 로드/저장
# ---------------------------------------------------------------------------

def _load_index(path: Path, dim: int) -> faiss.Index:
    if path.exists():
        try:
            index = faiss.read_index(str(path))
            if index.d == dim:
                return index
            logger.warning("인덱스 차원 불일치(%s) — 새로 생성", path)
        except Exception as e:
            logger.warning("인덱스 로드 실패(%s): %s — 새로 생성", path, e)
    return faiss.IndexFlatIP(dim)


def get_text_index() -> faiss.Index:
    global _text_index
    if _text_index is None:
        with _lock:
            if _text_index is None:
                _text_index = _load_index(TEXT_INDEX_PATH, TEXT_EMBED_DIM)
    return _text_index


def get_image_index() -> faiss.Index:
    global _image_index
    if _image_index is None:
        with _lock:
            if _image_index is None:
                _image_index = _load_index(IMAGE_INDEX_PATH, IMAGE_EMBED_DIM)
    return _image_index


def save_indexes() -> None:
    with _lock:
        if _text_index is not None:
            faiss.write_index(_text_index, str(TEXT_INDEX_PATH))
        if _image_index is not None:
            faiss.write_index(_image_index, str(IMAGE_INDEX_PATH))


# ---------------------------------------------------------------------------
# 인덱싱 (벡터 추가)
# ---------------------------------------------------------------------------

def add_text_vectors(vectors: np.ndarray, items: list[tuple[str, int]]) -> None:
    """
    텍스트 인덱스에 벡터 추가 + DB 매핑 기록.

    Args:
        vectors: (N, 384) 정규화된 벡터
        items: [(kind, row_id), ...] — kind: 'chunk' | 'table'
    """
    if vectors.size == 0 or not items:
        return
    assert vectors.shape[0] == len(items), "벡터 수와 항목 수 불일치"
    with _lock:
        index = _text_index if _text_index is not None else _load_index(
            TEXT_INDEX_PATH, TEXT_EMBED_DIM)
        globals()["_text_index"] = index
        start_id = index.ntotal
        index.add(vectors.astype(np.float32))
    mappings = [(start_id + i, kind, row_id)
                for i, (kind, row_id) in enumerate(items)]
    db.set_text_faiss_mappings(mappings)


def add_image_vectors(vectors: np.ndarray, image_ids: list[int]) -> None:
    """이미지 인덱스에 벡터 추가 + DB 매핑 기록."""
    if vectors.size == 0 or not image_ids:
        return
    assert vectors.shape[0] == len(image_ids), "벡터 수와 이미지 수 불일치"
    with _lock:
        index = _image_index if _image_index is not None else _load_index(
            IMAGE_INDEX_PATH, IMAGE_EMBED_DIM)
        globals()["_image_index"] = index
        start_id = index.ntotal
        index.add(vectors.astype(np.float32))
    mappings = [(start_id + i, image_id)
                for i, image_id in enumerate(image_ids)]
    db.set_image_faiss_mappings(mappings)


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------

def _snippet(text: str, max_len: int = 200) -> str:
    text = text.replace("\n", " ").strip()
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _search_text_index(query: str, top_k: int) -> list[dict]:
    """텍스트 인덱스 검색 → 청크/표 매칭 결과."""
    index = get_text_index()
    if index.ntotal == 0:
        return []
    qvec = embed_query_for_text(query)
    scores, ids = index.search(qvec, min(top_k, index.ntotal))
    faiss_ids = [int(i) for i in ids[0] if i >= 0]
    mapping = db.resolve_text_faiss_ids(faiss_ids)

    results = []
    for score, fid in zip(scores[0], ids[0]):
        fid = int(fid)
        if fid < 0 or fid not in mapping:
            continue  # 삭제되었거나 무효한 벡터
        kind, row_id = mapping[fid]
        if kind == "chunk":
            row = db.get_chunk(row_id)
            if not row:
                continue
            results.append({
                "match_type": "text",
                "score": float(score),
                "document_id": row["document_id"],
                "page_number": row["page_number"],
                "preview": _snippet(row["content"]),
            })
        elif kind == "table":
            row = db.get_table(row_id)
            if not row:
                continue
            try:
                table_data = json.loads(row["table_json"])
            except Exception:
                table_data = []
            results.append({
                "match_type": "table",
                "score": float(score),
                "document_id": row["document_id"],
                "page_number": row["page_number"],
                "preview": _snippet(row["table_text"]),
                "table_preview": table_data[:5],  # 상위 5행 미리보기
            })
        elif kind == "annotation":
            row = db.get_annotation(row_id)
            if not row:
                continue
            results.append({
                "match_type": "annotation",
                "score": float(score),
                "document_id": row["document_id"],
                "page_number": row["page_number"],
                "preview": _snippet(f"[{row['annot_type']}] {row['content']}"),
            })
        elif kind == "outline":
            row = db.get_outline(row_id)
            if not row:
                continue
            results.append({
                "match_type": "outline",
                "score": float(score),
                "document_id": row["document_id"],
                "page_number": row["page_number"],
                "preview": _snippet(f"목차: {row['title']}"),
            })
    return results


def _search_image_index(query: str, top_k: int) -> list[dict]:
    """이미지 인덱스 검색 (자연어 → CLIP 공간)."""
    index = get_image_index()
    if index.ntotal == 0:
        return []
    qvec = embed_query_for_image(query)
    scores, ids = index.search(qvec, min(top_k, index.ntotal))
    faiss_ids = [int(i) for i in ids[0] if i >= 0]
    mapping = db.resolve_image_faiss_ids(faiss_ids)

    results = []
    for score, fid in zip(scores[0], ids[0]):
        fid = int(fid)
        if fid < 0 or fid not in mapping:
            continue
        row = db.get_image(mapping[fid])
        if not row:
            continue
        results.append({
            "match_type": "image",
            "score": float(score),
            "document_id": row["document_id"],
            "page_number": row["page_number"],
            "preview": f"/images/{row['image_path']}",
            "width": row["width"],
            "height": row["height"],
        })
    return results


def search(query: str, search_type: str = "all",
           limit: int = RESULTS_PER_QUERY) -> list[dict]:
    """
    통합 검색: 문서 단위로 그룹핑하여 반환.

    Args:
        query: 검색어
        search_type: 'all' | 'text' | 'image' | 'table' | 'annotation'
    """
    matches: list[dict] = []

    if search_type in ("all", "text", "table", "annotation"):
        text_results = _search_text_index(query, SEARCH_TOP_K)
        if search_type == "text":
            text_results = [r for r in text_results
                            if r["match_type"] in ("text", "outline")]
        elif search_type == "table":
            text_results = [r for r in text_results if r["match_type"] == "table"]
        elif search_type == "annotation":
            text_results = [r for r in text_results
                            if r["match_type"] == "annotation"]
        matches.extend(text_results)

    if search_type in ("all", "image"):
        matches.extend(_search_image_index(query, SEARCH_TOP_K))

    # ----- 문서 단위 그룹핑 -----
    doc_groups: dict[int, dict] = {}
    for m in matches:
        doc_id = m["document_id"]
        group = doc_groups.setdefault(doc_id, {"best_score": -1.0, "matches": []})
        group["matches"].append(m)
        if m["score"] > group["best_score"]:
            group["best_score"] = m["score"]

    # 문서 정보 붙이고 점수순 정렬
    results = []
    for doc_id, group in doc_groups.items():
        doc = db.get_document(doc_id)
        if not doc:
            continue
        group["matches"].sort(key=lambda x: x["score"], reverse=True)
        top = group["matches"][0]
        results.append({
            "document_id": doc_id,
            "filename": doc["filename"],
            "page_count": doc["page_count"],
            "score": round(group["best_score"], 4),
            "match_type": top["match_type"],
            "page_number": top["page_number"],
            "preview": top["preview"],
            "matches": [
                {**m, "score": round(m["score"], 4)}
                for m in group["matches"][:5]  # 문서당 상위 5개 매칭만
            ],
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]
