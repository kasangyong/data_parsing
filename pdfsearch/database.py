"""
SQLite 데이터베이스 스키마 및 CRUD.

테이블 구성:
- documents        : 업로드된 PDF 메타데이터 (+ 문서 정보 metadata_json)
- text_chunks      : 페이지별 텍스트 청크 (source: native | ocr)
- images           : 추출된 이미지 메타데이터 (kind: image | vector — 벡터 그래픽 렌더링 포함)
- tables           : 추출된 표 (JSON 구조 + 검색용 텍스트)
- outlines         : 목차 (북마크)
- links            : 하이퍼링크
- annotations      : 주석 (형광펜, 메모 등)
- faiss_text_map   : FAISS 텍스트 인덱스 ID → (kind, row_id) 매핑
- faiss_image_map  : FAISS 이미지 인덱스 ID → images.id 매핑
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

from .config import DB_PATH

# ---------------------------------------------------------------------------
# 연결 및 초기화
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    filename      TEXT NOT NULL,
    file_hash     TEXT NOT NULL UNIQUE,
    stored_path   TEXT NOT NULL,
    page_count    INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS text_chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'native',
    faiss_id    INTEGER
);

CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    image_path  TEXT NOT NULL,
    width       INTEGER NOT NULL DEFAULT 0,
    height      INTEGER NOT NULL DEFAULT 0,
    kind        TEXT NOT NULL DEFAULT 'image',
    faiss_id    INTEGER
);

CREATE TABLE IF NOT EXISTS tables (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    table_index INTEGER NOT NULL,
    table_json  TEXT NOT NULL,
    table_text  TEXT NOT NULL,
    faiss_id    INTEGER
);

CREATE TABLE IF NOT EXISTS outlines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    level       INTEGER NOT NULL DEFAULT 1,
    title       TEXT NOT NULL,
    page_number INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    url         TEXT NOT NULL,
    anchor_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS annotations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    annot_type  TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    faiss_id    INTEGER
);

-- FAISS 텍스트 인덱스 매핑: kind = 'chunk' | 'table' | 'annotation' | 'outline'
CREATE TABLE IF NOT EXISTS faiss_text_map (
    faiss_id    INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    row_id      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS faiss_image_map (
    faiss_id    INTEGER PRIMARY KEY,
    row_id      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc   ON text_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_images_doc   ON images(document_id);
CREATE INDEX IF NOT EXISTS idx_tables_doc   ON tables(document_id);
CREATE INDEX IF NOT EXISTS idx_outlines_doc ON outlines(document_id);
CREATE INDEX IF NOT EXISTS idx_links_doc    ON links(document_id);
CREATE INDEX IF NOT EXISTS idx_annots_doc   ON annotations(document_id);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    """커밋/롤백을 자동 처리하는 컨텍스트 매니저."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_session() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def find_document_by_hash(file_hash: str) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        return dict(row) if row else None


def insert_document(filename: str, file_hash: str, stored_path: str,
                    page_count: int, metadata: Optional[dict] = None) -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO documents (filename, file_hash, stored_path, page_count, "
            "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (filename, file_hash, stored_path, page_count,
             json.dumps(metadata or {}, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def get_document(document_id: int) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return dict(row) if row else None


def list_documents() -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT d.*,
                   (SELECT COUNT(*) FROM text_chunks c WHERE c.document_id = d.id) AS chunk_count,
                   (SELECT COUNT(*) FROM images i     WHERE i.document_id = d.id) AS image_count,
                   (SELECT COUNT(*) FROM tables t     WHERE t.document_id = d.id) AS table_count
            FROM documents d
            ORDER BY d.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def delete_document(document_id: int) -> bool:
    """문서와 하위 요소, FAISS 매핑을 모두 제거한다. (FAISS 벡터는 소프트 삭제)"""
    with db_session() as conn:
        # FAISS 매핑 제거 (매핑이 없으면 검색 시 무시됨)
        conn.execute(
            """
            DELETE FROM faiss_text_map WHERE
                (kind = 'chunk' AND row_id IN (SELECT id FROM text_chunks WHERE document_id = ?))
             OR (kind = 'table' AND row_id IN (SELECT id FROM tables WHERE document_id = ?))
             OR (kind = 'annotation' AND row_id IN (SELECT id FROM annotations WHERE document_id = ?))
             OR (kind = 'outline' AND row_id IN (SELECT id FROM outlines WHERE document_id = ?))
            """,
            (document_id, document_id, document_id, document_id),
        )
        conn.execute(
            "DELETE FROM faiss_image_map WHERE row_id IN "
            "(SELECT id FROM images WHERE document_id = ?)",
            (document_id,),
        )
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# text_chunks / images / tables
# ---------------------------------------------------------------------------

def insert_text_chunk(document_id: int, page_number: int, chunk_index: int,
                      content: str, source: str = "native") -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO text_chunks (document_id, page_number, chunk_index, content, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (document_id, page_number, chunk_index, content, source),
        )
        return cur.lastrowid


def insert_image(document_id: int, page_number: int, image_path: str,
                 width: int, height: int, kind: str = "image") -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO images (document_id, page_number, image_path, width, height, kind) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (document_id, page_number, image_path, width, height, kind),
        )
        return cur.lastrowid


def insert_outline(document_id: int, level: int, title: str,
                   page_number: int) -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO outlines (document_id, level, title, page_number) "
            "VALUES (?, ?, ?, ?)",
            (document_id, level, title, page_number),
        )
        return cur.lastrowid


def insert_link(document_id: int, page_number: int, url: str,
                anchor_text: str = "") -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO links (document_id, page_number, url, anchor_text) "
            "VALUES (?, ?, ?, ?)",
            (document_id, page_number, url, anchor_text),
        )
        return cur.lastrowid


def insert_annotation(document_id: int, page_number: int, annot_type: str,
                      content: str) -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO annotations (document_id, page_number, annot_type, content) "
            "VALUES (?, ?, ?, ?)",
            (document_id, page_number, annot_type, content),
        )
        return cur.lastrowid


def insert_table(document_id: int, page_number: int, table_index: int,
                 table_data: list[list], table_text: str) -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO tables (document_id, page_number, table_index, table_json, table_text) "
            "VALUES (?, ?, ?, ?, ?)",
            (document_id, page_number, table_index,
             json.dumps(table_data, ensure_ascii=False), table_text),
        )
        return cur.lastrowid


def get_chunks_by_document(document_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM text_chunks WHERE document_id = ? "
            "ORDER BY page_number, chunk_index",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_images_by_document(document_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM images WHERE document_id = ? ORDER BY page_number",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_tables_by_document(document_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM tables WHERE document_id = ? "
            "ORDER BY page_number, table_index",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_outlines_by_document(document_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM outlines WHERE document_id = ? ORDER BY id",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_links_by_document(document_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM links WHERE document_id = ? ORDER BY page_number",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_annotations_by_document(document_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE document_id = ? ORDER BY page_number",
            (document_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_annotation(annot_id: int) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM annotations WHERE id = ?", (annot_id,)
        ).fetchone()
        return dict(row) if row else None


def get_outline(outline_id: int) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM outlines WHERE id = ?", (outline_id,)
        ).fetchone()
        return dict(row) if row else None


def get_chunk(chunk_id: int) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM text_chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        return dict(row) if row else None


def get_image(image_id: int) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        return dict(row) if row else None


def get_table(table_id: int) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM tables WHERE id = ?", (table_id,)
        ).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# FAISS 매핑
# ---------------------------------------------------------------------------

_KIND_TABLE = {
    "chunk": "text_chunks",
    "table": "tables",
    "annotation": "annotations",
    "outline": "outlines",
}


def set_text_faiss_mappings(mappings: list[tuple[int, str, int]]) -> None:
    """mappings: [(faiss_id, kind, row_id), ...] — 해당 요소의 faiss_id도 함께 갱신."""
    with db_session() as conn:
        for faiss_id, kind, row_id in mappings:
            conn.execute(
                "INSERT OR REPLACE INTO faiss_text_map (faiss_id, kind, row_id) "
                "VALUES (?, ?, ?)",
                (faiss_id, kind, row_id),
            )
            table = _KIND_TABLE.get(kind)
            if table and table != "outlines":  # outlines에는 faiss_id 컬럼 없음
                conn.execute(
                    f"UPDATE {table} SET faiss_id = ? WHERE id = ?",
                    (faiss_id, row_id),
                )


def set_image_faiss_mappings(mappings: list[tuple[int, int]]) -> None:
    """mappings: [(faiss_id, image_id), ...]"""
    with db_session() as conn:
        for faiss_id, image_id in mappings:
            conn.execute(
                "INSERT OR REPLACE INTO faiss_image_map (faiss_id, row_id) "
                "VALUES (?, ?)",
                (faiss_id, image_id),
            )
            conn.execute(
                "UPDATE images SET faiss_id = ? WHERE id = ?",
                (faiss_id, image_id),
            )


def resolve_text_faiss_ids(faiss_ids: list[int]) -> dict[int, tuple[str, int]]:
    """faiss_id 리스트 → {faiss_id: (kind, row_id)}. 매핑 없는 ID는 제외(소프트 삭제)."""
    if not faiss_ids:
        return {}
    placeholders = ",".join("?" * len(faiss_ids))
    with db_session() as conn:
        rows = conn.execute(
            f"SELECT * FROM faiss_text_map WHERE faiss_id IN ({placeholders})",
            faiss_ids,
        ).fetchall()
        return {r["faiss_id"]: (r["kind"], r["row_id"]) for r in rows}


def resolve_image_faiss_ids(faiss_ids: list[int]) -> dict[int, int]:
    """faiss_id 리스트 → {faiss_id: image_id}. 매핑 없는 ID는 제외."""
    if not faiss_ids:
        return {}
    placeholders = ",".join("?" * len(faiss_ids))
    with db_session() as conn:
        rows = conn.execute(
            f"SELECT * FROM faiss_image_map WHERE faiss_id IN ({placeholders})",
            faiss_ids,
        ).fetchall()
        return {r["faiss_id"]: r["row_id"] for r in rows}
