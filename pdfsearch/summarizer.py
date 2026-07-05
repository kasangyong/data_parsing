"""
PDF 통합 요약 모듈 (추출 요약 방식).

원리:
1. 문서의 모든 텍스트 청크에서 문장을 분리
2. 각 문장을 임베딩하고, 전체 평균(=문서 중심 벡터)과의 유사도 계산
3. 중심에 가장 가까운 상위 N개 문장 = 문서를 대표하는 핵심 문장 (MMR로 중복 억제)
4. 핵심 문장 + 표 미리보기 + 이미지 갤러리를 통합하여 반환

무거운 생성형 LLM 없이 로컬 임베딩 모델만으로 동작한다.
"""
import json
import logging
import re

import numpy as np

from . import database as db
from .config import SUMMARY_SENTENCES
from .embeddings import embed_texts

logger = logging.getLogger(__name__)


def _split_sentences(text: str) -> list[str]:
    """한국어/영어 문장 분리 (간단한 규칙 기반)."""
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?。다])\s+", text)
    # 너무 짧거나 긴 문장 제외 (노이즈/목차 등)
    return [s.strip() for s in sentences if 20 <= len(s.strip()) <= 300]


def _select_key_sentences(sentences: list[str], pages: list[int],
                          top_n: int) -> list[dict]:
    """중심 벡터 유사도 + MMR(중복 억제)로 핵심 문장 선택."""
    if not sentences:
        return []
    if len(sentences) <= top_n:
        return [{"sentence": s, "page_number": p}
                for s, p in zip(sentences, pages)]

    vecs = embed_texts(sentences)                     # (N, d), 정규화됨
    centroid = vecs.mean(axis=0)
    centroid /= (np.linalg.norm(centroid) + 1e-12)
    centrality = vecs @ centroid                      # 중심 유사도

    # MMR: 중심과 유사하면서 이미 뽑힌 문장과는 다른 문장 선택
    selected: list[int] = []
    candidates = list(range(len(sentences)))
    lambda_ = 0.7
    while candidates and len(selected) < top_n:
        best_idx, best_score = -1, -1e9
        for i in candidates:
            redundancy = max((float(vecs[i] @ vecs[j]) for j in selected),
                             default=0.0)
            score = lambda_ * float(centrality[i]) - (1 - lambda_) * redundancy
            if score > best_score:
                best_score, best_idx = score, i
        selected.append(best_idx)
        candidates.remove(best_idx)

    # 문서 내 등장 순서대로 정렬해 자연스러운 요약 흐름 유지
    selected.sort()
    return [{"sentence": sentences[i], "page_number": pages[i]}
            for i in selected]


def summarize_document(document_id: int) -> dict | None:
    """
    문서 통합 요약: 핵심 문장 + 표 미리보기 + 이미지 갤러리.
    """
    doc = db.get_document(document_id)
    if not doc:
        return None

    chunks = db.get_chunks_by_document(document_id)
    images = db.get_images_by_document(document_id)
    tables = db.get_tables_by_document(document_id)
    outlines = db.get_outlines_by_document(document_id)
    links = db.get_links_by_document(document_id)
    annotations = db.get_annotations_by_document(document_id)

    try:
        metadata = json.loads(doc.get("metadata_json") or "{}")
    except Exception:
        metadata = {}

    # ----- 1) 핵심 문장 추출 -----
    sentences: list[str] = []
    pages: list[int] = []
    for chunk in chunks:
        for sent in _split_sentences(chunk["content"]):
            sentences.append(sent)
            pages.append(chunk["page_number"])

    key_sentences: list[dict] = []
    summary_error = None
    if sentences:
        try:
            key_sentences = _select_key_sentences(
                sentences, pages, SUMMARY_SENTENCES)
        except Exception as e:
            logger.warning("요약 문장 선택 실패: %s", e)
            summary_error = str(e)
            # 폴백: 앞부분 문장 사용
            key_sentences = [{"sentence": s, "page_number": p}
                             for s, p in zip(sentences[:SUMMARY_SENTENCES],
                                             pages[:SUMMARY_SENTENCES])]

    # ----- 2) 표 미리보기 -----
    table_previews = []
    for t in tables:
        try:
            data = json.loads(t["table_json"])
        except Exception:
            data = []
        table_previews.append({
            "id": t["id"],
            "page_number": t["page_number"],
            "rows": len(data),
            "cols": len(data[0]) if data else 0,
            "preview": data[:6],   # 상위 6행
        })

    # ----- 3) 이미지 갤러리 (임베디드 + 벡터 그래픽) -----
    image_gallery = [
        {
            "id": img["id"],
            "page_number": img["page_number"],
            "url": f"/images/{img['image_path']}",
            "width": img["width"],
            "height": img["height"],
            "kind": img.get("kind", "image"),
        }
        for img in images
    ]

    # ----- 4) 목차 -----
    outline_list = [
        {"level": o["level"], "title": o["title"],
         "page_number": o["page_number"]}
        for o in outlines
    ]

    # ----- 5) 링크 (중복 URL 제거) -----
    seen_urls: set[str] = set()
    link_list = []
    for lk in links:
        if lk["url"] in seen_urls:
            continue
        seen_urls.add(lk["url"])
        link_list.append({
            "page_number": lk["page_number"],
            "url": lk["url"],
            "anchor_text": lk["anchor_text"],
        })

    # ----- 6) 주석 -----
    annotation_list = [
        {"page_number": a["page_number"], "type": a["annot_type"],
         "content": a["content"][:300]}
        for a in annotations
    ]

    ocr_chunks = sum(1 for c in chunks if c.get("source") == "ocr")

    return {
        "document": {
            "id": doc["id"],
            "filename": doc["filename"],
            "page_count": doc["page_count"],
            "created_at": doc["created_at"],
            "pdf_url": f"/pdfs/{doc['stored_path']}",
            "metadata": metadata,
        },
        "stats": {
            "chunk_count": len(chunks),
            "ocr_chunk_count": ocr_chunks,
            "image_count": len(images),
            "table_count": len(tables),
            "outline_count": len(outlines),
            "link_count": len(link_list),
            "annotation_count": len(annotations),
        },
        "key_sentences": key_sentences,
        "tables": table_previews,
        "images": image_gallery,
        "outlines": outline_list,
        "links": link_list[:50],
        "annotations": annotation_list[:50],
        "summary_error": summary_error,
    }
