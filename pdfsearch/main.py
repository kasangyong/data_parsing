"""
FastAPI 서버 — PDF 멀티모달 검색 엔진.

실행:
    pdfsearch serve          # 권장 (현재 프로젝트 데이터 사용)
    uvicorn pdfsearch.main:app --reload   # 개발용

★ 모델이 다운로드되지 않아도 서버는 정상 기동한다.
  모델 필요 기능(업로드/검색/요약)은 503 + 안내 메시지를 반환한다.
"""
import logging
import shutil
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import database as db
from . import graph as graph_builder
from . import search as search_engine
from .config import DATA_DIR, IMAGE_DIR, PDF_DIR, STATIC_DIR
from .embeddings import ModelNotReadyError, models_ready
from .parser import is_ocr_available
from .pipeline import (
    DuplicateDocumentError,
    ParseFailedError,
    ingest_pdf_bytes,
)
from .summarizer import summarize_document

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="PDF 멀티모달 검색 엔진", version="2.0.0")

logger.info("데이터 디렉터리: %s", DATA_DIR)

# DB 초기화 (서버 기동 시 1회)
db.init_db()

# 정적 파일 서빙
app.mount("/images", StaticFiles(directory=IMAGE_DIR), name="images")
app.mount("/pdfs", StaticFiles(directory=PDF_DIR), name="pdfs")


# ---------------------------------------------------------------------------
# 예외 핸들러: 모델 미준비 → 503
# ---------------------------------------------------------------------------

@app.exception_handler(ModelNotReadyError)
async def model_not_ready_handler(request, exc: ModelNotReadyError):
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc), "models": models_ready()},
    )


# ---------------------------------------------------------------------------
# 상태 확인
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status():
    """모델 다운로드 상태 + OCR 가용 여부 + 문서 수."""
    status = models_ready()
    return {
        "models": status,
        "ocr_available": is_ocr_available(),
        "documents": len(db.list_documents()),
        "message": (
            "모든 모델이 준비되었습니다."
            if status["all_ready"]
            else "모델이 아직 다운로드되지 않았습니다. "
                 "`pdfsearch models` 를 실행해주세요."
        ),
    }


# ---------------------------------------------------------------------------
# PDF 업로드 → 파싱 → 인덱싱 (공용 파이프라인 사용)
# ---------------------------------------------------------------------------

@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있습니다.")

    # 모델 준비 여부를 먼저 확인 (파싱 후 실패하는 낭비 방지)
    if not models_ready()["all_ready"]:
        raise ModelNotReadyError()

    file_bytes = await file.read()

    try:
        report = ingest_pdf_bytes(file_bytes, file.filename)
    except DuplicateDocumentError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ParseFailedError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ModelNotReadyError:
        raise
    except Exception as e:
        logger.exception("업로드 처리 실패")
        raise HTTPException(status_code=500, detail=f"처리 실패: {e}")

    return {
        "document_id": report.document_id,
        "filename": report.filename,
        "page_count": report.page_count,
        "extracted": {
            "text_chunks": report.text_chunks,
            "images": report.images,
            "vector_graphics": report.vector_graphics,
            "tables": report.tables,
            "outlines": report.outlines,
            "links": report.links,
            "annotations": report.annotations,
        },
        "ocr_pages": report.ocr_pages,
        "warnings": report.warnings,
    }


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------

@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, description="검색어"),
    type: str = Query("all", pattern="^(all|text|image|table|annotation)$"),
):
    if not models_ready()["all_ready"]:
        raise ModelNotReadyError()
    results = search_engine.search(q.strip(), search_type=type)
    return {"query": q, "type": type, "count": len(results), "results": results}


# ---------------------------------------------------------------------------
# 지식 그래프
# ---------------------------------------------------------------------------

@app.get("/api/graph")
def get_graph(
    threshold: float = Query(0.35, ge=0.0, le=1.0, description="엣지 유사도 임계값"),
    max_edges: int = Query(4, ge=1, le=20, description="노드당 최대 엣지 수"),
):
    """문서 지식 그래프 (노드 = 문서, 엣지 = 의미 유사도 + 공유 키워드)."""
    builder = graph_builder.KnowledgeGraphBuilder(
        similarity_threshold=threshold,
        max_edges_per_node=max_edges,
    )
    return builder.build()


# ---------------------------------------------------------------------------
# 문서 관리
# ---------------------------------------------------------------------------

@app.get("/api/documents")
def get_documents():
    return {"documents": db.list_documents()}


@app.get("/api/documents/{document_id}")
def get_document_detail(document_id: int):
    doc = db.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return {
        **doc,
        "pdf_url": f"/pdfs/{doc['stored_path']}",
        "chunk_count": len(db.get_chunks_by_document(document_id)),
        "image_count": len(db.get_images_by_document(document_id)),
        "table_count": len(db.get_tables_by_document(document_id)),
        "outline_count": len(db.get_outlines_by_document(document_id)),
        "link_count": len(db.get_links_by_document(document_id)),
        "annotation_count": len(db.get_annotations_by_document(document_id)),
    }


@app.get("/api/documents/{document_id}/summary")
def get_document_summary(document_id: int):
    if not models_ready()["all_ready"]:
        raise ModelNotReadyError()
    summary = summarize_document(document_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
    return summary


@app.delete("/api/documents/{document_id}")
def remove_document(document_id: int):
    doc = db.get_document(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")

    # 이미지/PDF 파일 정리
    images = db.get_images_by_document(document_id)
    for img in images:
        try:
            (IMAGE_DIR / img["image_path"]).unlink(missing_ok=True)
        except Exception as e:
            logger.warning("이미지 파일 삭제 실패: %s", e)
    doc_key = Path(doc["stored_path"]).stem
    img_dir = IMAGE_DIR / doc_key
    if img_dir.exists():
        shutil.rmtree(img_dir, ignore_errors=True)
    try:
        (PDF_DIR / doc["stored_path"]).unlink(missing_ok=True)
    except Exception as e:
        logger.warning("PDF 파일 삭제 실패: %s", e)

    db.delete_document(document_id)
    return {"deleted": document_id}


# ---------------------------------------------------------------------------
# 웹 UI (SPA)
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
