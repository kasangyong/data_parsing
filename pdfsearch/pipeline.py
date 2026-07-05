"""
공용 인제스트 파이프라인.

웹 업로드(main.py)와 폴더 일괄 처리(ingest_folder.py)가 공유하는
"PDF 바이트 → 파싱 → DB 저장 → 임베딩 → FAISS 인덱싱" 전체 흐름.

- 중복(파일 해시) 자동 감지
- 인덱싱 실패 시 문서 롤백 (부분 데이터 방지)
"""
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import database as db
from . import search as search_engine
from .config import IMAGE_DIR, PDF_DIR
from .embeddings import embed_images, embed_texts
from .parser import ParseResult, compute_file_hash, parse_pdf

logger = logging.getLogger(__name__)


class DuplicateDocumentError(Exception):
    """이미 처리된 PDF (파일 해시 동일)."""

    def __init__(self, existing: dict):
        self.existing = existing
        super().__init__(
            f"이미 업로드된 파일입니다: {existing['filename']} "
            f"(문서 ID: {existing['id']})"
        )


class ParseFailedError(Exception):
    """PDF를 읽을 수 없음."""


@dataclass
class IngestReport:
    document_id: int
    filename: str
    page_count: int
    text_chunks: int
    images: int
    vector_graphics: int
    tables: int
    outlines: int
    links: int
    annotations: int
    ocr_pages: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def ingest_pdf_bytes(file_bytes: bytes, filename: str) -> IngestReport:
    """
    PDF 바이트를 받아 전체 인제스트를 수행하고 결과 리포트를 반환.

    Raises:
        DuplicateDocumentError: 중복 파일
        ParseFailedError: 읽을 수 없는 PDF
        ModelNotReadyError: 모델 미다운로드 (embeddings에서 발생)
    """
    if not file_bytes:
        raise ParseFailedError("빈 파일입니다.")

    # 1) 중복 확인
    file_hash = compute_file_hash(file_bytes)
    existing = db.find_document_by_hash(file_hash)
    if existing:
        raise DuplicateDocumentError(existing)

    # 2) 원본 저장
    doc_key = file_hash[:12]
    stored_name = f"{doc_key}.pdf"
    stored_path = PDF_DIR / stored_name
    stored_path.write_bytes(file_bytes)

    # 3) 파싱
    try:
        result = parse_pdf(stored_path, doc_key)
    except Exception as e:
        stored_path.unlink(missing_ok=True)
        raise ParseFailedError(f"PDF 파싱 실패: {e}") from e

    if result.page_count == 0:
        stored_path.unlink(missing_ok=True)
        raise ParseFailedError(
            f"PDF를 읽을 수 없습니다. {'; '.join(result.errors[:3])}"
        )

    # 4) DB 저장
    document_id = db.insert_document(
        filename=filename,
        file_hash=file_hash,
        stored_path=stored_name,
        page_count=result.page_count,
        metadata=result.metadata,
    )

    try:
        report = _store_and_index(document_id, result)
    except Exception:
        # 저장/인덱싱 실패 시 롤백
        logger.exception("인제스트 실패 — 롤백: %s", filename)
        db.delete_document(document_id)
        stored_path.unlink(missing_ok=True)
        img_dir = IMAGE_DIR / doc_key
        if img_dir.exists():
            shutil.rmtree(img_dir, ignore_errors=True)
        raise

    report.document_id = document_id
    report.filename = filename
    return report


def _store_and_index(document_id: int, result: ParseResult) -> IngestReport:
    """파싱 결과를 DB에 저장하고 FAISS에 인덱싱."""

    # ----- DB 저장 -----
    chunk_ids = [
        db.insert_text_chunk(document_id, c.page_number, c.chunk_index,
                             c.content, source=c.source)
        for c in result.chunks
    ]
    image_ids = [
        db.insert_image(document_id, im.page_number, im.image_path,
                        im.width, im.height, kind=im.kind)
        for im in result.images
    ]
    table_ids = [
        db.insert_table(document_id, t.page_number, t.table_index,
                        t.data, t.text)
        for t in result.tables
    ]
    outline_ids = [
        db.insert_outline(document_id, o.level, o.title, o.page_number)
        for o in result.outlines
    ]
    for lk in result.links:
        db.insert_link(document_id, lk.page_number, lk.url, lk.anchor_text)
    annot_ids = [
        db.insert_annotation(document_id, a.page_number, a.annot_type,
                             a.content)
        for a in result.annotations
    ]

    # ----- 텍스트 인덱싱 (청크 + 표 + 주석 + 목차) -----
    text_items: list[tuple[str, int]] = []
    text_contents: list[str] = []

    for chunk, cid in zip(result.chunks, chunk_ids):
        text_contents.append(chunk.content)
        text_items.append(("chunk", cid))
    for table, tid in zip(result.tables, table_ids):
        text_contents.append(table.text)
        text_items.append(("table", tid))
    for annot, aid in zip(result.annotations, annot_ids):
        text_contents.append(f"[{annot.annot_type}] {annot.content}")
        text_items.append(("annotation", aid))
    # 목차 제목도 검색 대상 (짧은 제목은 제외)
    for outline, oid in zip(result.outlines, outline_ids):
        if len(outline.title) >= 4:
            text_contents.append(outline.title)
            text_items.append(("outline", oid))

    if text_contents:
        vecs = embed_texts(text_contents)
        search_engine.add_text_vectors(vecs, text_items)

    # ----- 이미지 인덱싱 (CLIP) — 임베디드 이미지 + 벡터 그래픽 -----
    if image_ids:
        paths = [IMAGE_DIR / im.image_path for im in result.images]
        img_vecs, ok_indices = embed_images(paths)
        ok_image_ids = [image_ids[i] for i in ok_indices]
        search_engine.add_image_vectors(img_vecs, ok_image_ids)

    search_engine.save_indexes()

    n_vector = sum(1 for im in result.images if im.kind == "vector")
    return IngestReport(
        document_id=document_id,
        filename="",
        page_count=result.page_count,
        text_chunks=len(result.chunks),
        images=len(result.images) - n_vector,
        vector_graphics=n_vector,
        tables=len(result.tables),
        outlines=len(result.outlines),
        links=len(result.links),
        annotations=len(result.annotations),
        ocr_pages=result.ocr_pages,
        warnings=result.errors[:10],
    )
