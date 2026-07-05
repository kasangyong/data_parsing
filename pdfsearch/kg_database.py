"""
지식 그래프 저장소 (entities / entity_mentions / relations).

ROADMAP §2.5 스키마 구현:
- entities        : 엔티티 노드 (온톨로지 클래스 + 클래스 신뢰도 + 별칭)
- entity_mentions : 출처 추적 (어느 문서/청크/페이지에서 나왔나 + 추출 신뢰도)
- relations       : 관계 엣지 (신뢰도 + 증거 수 + 추출기 감사 추적)

기존 db.sqlite 안에 함께 저장된다 (프로젝트별 독립 원칙 유지).
"""
import json
import logging
from typing import Optional

from .database import db_session

logger = logging.getLogger(__name__)

KG_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,            -- 정규화된 대표명
    entity_type     TEXT NOT NULL,            -- 온톨로지 클래스
    type_confidence REAL NOT NULL DEFAULT 0.5,
    aliases         TEXT NOT NULL DEFAULT '[]',
    UNIQUE(name, entity_type)
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id             INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    document_id           INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id              INTEGER,            -- text_chunks.id (없으면 NULL)
    page_number           INTEGER NOT NULL DEFAULT 0,
    span_text             TEXT NOT NULL DEFAULT '',
    extraction_confidence REAL NOT NULL DEFAULT 0.5,
    extractor             TEXT NOT NULL DEFAULT 'rule'
);

CREATE TABLE IF NOT EXISTS relations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relation_type  TEXT NOT NULL,
    target_id      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    confidence     REAL NOT NULL DEFAULT 0.5,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    extractor      TEXT NOT NULL DEFAULT 'rule',
    document_id    INTEGER,                   -- 대표 출처 문서
    UNIQUE(source_id, relation_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_mentions_entity ON entity_mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_mentions_doc    ON entity_mentions(document_id);
CREATE INDEX IF NOT EXISTS idx_relations_src   ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_tgt   ON relations(target_id);
"""


def init_kg_db() -> None:
    with db_session() as conn:
        conn.executescript(KG_SCHEMA)


def clear_kg() -> None:
    """지식 그래프 전체 재구축 전 초기화."""
    with db_session() as conn:
        conn.execute("DELETE FROM relations")
        conn.execute("DELETE FROM entity_mentions")
        conn.execute("DELETE FROM entities")


# ---------------------------------------------------------------------------
# entities
# ---------------------------------------------------------------------------

def upsert_entity(name: str, entity_type: str, type_confidence: float,
                  aliases: Optional[list[str]] = None) -> int:
    """같은 (name, type)이면 신뢰도 최대값으로 갱신하고 별칭을 합친다."""
    with db_session() as conn:
        row = conn.execute(
            "SELECT id, type_confidence, aliases FROM entities "
            "WHERE name = ? AND entity_type = ?",
            (name, entity_type),
        ).fetchone()
        if row:
            merged = set(json.loads(row["aliases"])) | set(aliases or [])
            conn.execute(
                "UPDATE entities SET type_confidence = ?, aliases = ? WHERE id = ?",
                (max(row["type_confidence"], type_confidence),
                 json.dumps(sorted(merged), ensure_ascii=False), row["id"]),
            )
            return row["id"]
        cur = conn.execute(
            "INSERT INTO entities (name, entity_type, type_confidence, aliases) "
            "VALUES (?, ?, ?, ?)",
            (name, entity_type, type_confidence,
             json.dumps(sorted(set(aliases or [])), ensure_ascii=False)),
        )
        return cur.lastrowid


def add_mention(entity_id: int, document_id: int, page_number: int,
                span_text: str, confidence: float, extractor: str,
                chunk_id: Optional[int] = None) -> int:
    with db_session() as conn:
        cur = conn.execute(
            "INSERT INTO entity_mentions (entity_id, document_id, chunk_id, "
            "page_number, span_text, extraction_confidence, extractor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity_id, document_id, chunk_id, page_number, span_text,
             confidence, extractor),
        )
        return cur.lastrowid


def upsert_relation(source_id: int, relation_type: str, target_id: int,
                    confidence: float, extractor: str,
                    document_id: Optional[int] = None) -> int:
    """
    같은 트리플이 이미 있으면 노이즈-OR로 신뢰도 결합 + 증거 수 증가.
    conf_new = 1 - (1-conf_old)*(1-conf)   (ROADMAP §2.2-③)
    """
    with db_session() as conn:
        row = conn.execute(
            "SELECT id, confidence, evidence_count FROM relations "
            "WHERE source_id = ? AND relation_type = ? AND target_id = ?",
            (source_id, relation_type, target_id),
        ).fetchone()
        if row:
            combined = 1.0 - (1.0 - row["confidence"]) * (1.0 - confidence)
            conn.execute(
                "UPDATE relations SET confidence = ?, evidence_count = ? "
                "WHERE id = ?",
                (round(combined, 4), row["evidence_count"] + 1, row["id"]),
            )
            return row["id"]
        cur = conn.execute(
            "INSERT INTO relations (source_id, relation_type, target_id, "
            "confidence, extractor, document_id) VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, relation_type, target_id, round(confidence, 4),
             extractor, document_id),
        )
        return cur.lastrowid


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

def list_entities(min_confidence: float = 0.0) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT e.*,
                   (SELECT COUNT(*) FROM entity_mentions m
                     WHERE m.entity_id = e.id)                AS mention_count,
                   (SELECT COUNT(DISTINCT m.document_id) FROM entity_mentions m
                     WHERE m.entity_id = e.id)                AS document_count
            FROM entities e
            WHERE e.type_confidence >= ?
            ORDER BY mention_count DESC
            """,
            (min_confidence,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["aliases"] = json.loads(d["aliases"])
            out.append(d)
        return out


def list_relations(min_confidence: float = 0.0) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM relations WHERE confidence >= ? "
            "ORDER BY confidence DESC",
            (min_confidence,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_entity_mentions(entity_id: int) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT m.*, d.filename
            FROM entity_mentions m
            JOIN documents d ON d.id = m.document_id
            WHERE m.entity_id = ?
            ORDER BY m.extraction_confidence DESC
            """,
            (entity_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def kg_stats() -> dict:
    with db_session() as conn:
        n_e = conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"]
        n_m = conn.execute("SELECT COUNT(*) c FROM entity_mentions").fetchone()["c"]
        n_r = conn.execute("SELECT COUNT(*) c FROM relations").fetchone()["c"]
        by_type = conn.execute(
            "SELECT entity_type, COUNT(*) c FROM entities "
            "GROUP BY entity_type ORDER BY c DESC"
        ).fetchall()
        by_rel = conn.execute(
            "SELECT relation_type, COUNT(*) c FROM relations "
            "GROUP BY relation_type ORDER BY c DESC"
        ).fetchall()
        return {
            "entities": n_e,
            "mentions": n_m,
            "relations": n_r,
            "entities_by_type": {r["entity_type"]: r["c"] for r in by_type},
            "relations_by_type": {r["relation_type"]: r["c"] for r in by_rel},
        }
