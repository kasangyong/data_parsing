"""
지식 그래프 빌더 — 추출 → 해소 → 관계 → 검증 → 저장의 전체 파이프라인.

단계 (ROADMAP §1.5.3 통합 파이프라인):
1. 문서별 추출: 구조(rule) + 정규식(regex) + GLiNER(선택)
2. 클래스 신뢰도: 임베딩 유사도로 타입 신뢰도 산출 (§2.2-②)
3. 엔티티 해소: 정규화 일치 + 임베딩 유사도 병합 (P3, 보수적)
4. 공출현 관계: 같은 청크에 함께 나온 엔티티 → related_to (PMI 정규화, P4)
5. 온톨로지 검증: domain/range 위반 트리플 거부 (§3.2-⑤)
6. 저장: 노이즈-OR 신뢰도 결합 (kg_database)
"""
import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import database as db
from . import kg_database as kgdb
from .kg_extract import (
    EntityCandidate,
    GlinerExtractor,
    RegexExtractor,
    RelationCandidate,
    StructureExtractor,
    class_confidence,
    normalize_name,
)
from .ontology import Ontology, load_ontology

logger = logging.getLogger(__name__)


@dataclass
class BuildReport:
    documents: int = 0
    entities: int = 0
    mentions: int = 0
    relations: int = 0
    violations: int = 0          # 온톨로지 위반으로 거부된 트리플 수
    merged: int = 0              # 엔티티 해소로 병합된 수
    used_gliner: bool = False
    warnings: list[str] = field(default_factory=list)


class KGBuilder:
    """프로젝트 전체 문서 → 지식 그래프 구축."""

    # 공출현 관계 파라미터
    MIN_COOC = 2                 # 최소 공출현 횟수
    MAX_COOC_EDGES = 500         # 과밀 방지
    COOC_BASE_CONF = 0.55        # PMI 최고점일 때의 신뢰도 상한

    # 엔티티 해소 파라미터 (보수적 — 잘못 병합이 가장 위험)
    MERGE_SIM_THRESHOLD = 0.92

    def __init__(self, use_gliner: bool = True,
                 ontology: Ontology | None = None):
        self.ontology = ontology or load_ontology()
        self.structure = StructureExtractor()
        self.regex = RegexExtractor()
        self.gliner = GlinerExtractor() if use_gliner else None

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def build(self, rebuild: bool = True) -> BuildReport:
        report = BuildReport()
        kgdb.init_kg_db()
        if rebuild:
            kgdb.clear_kg()

        docs = db.list_documents()
        report.documents = len(docs)
        if not docs:
            return report

        # ---------- 1) 문서별 추출 ----------
        all_entities: list[EntityCandidate] = []
        all_relations: list[RelationCandidate] = []
        # 공출현 계산용: chunk_id → {(name, type), ...}
        chunk_entities: dict[int, set[tuple[str, str]]] = defaultdict(set)

        gliner_ok = bool(self.gliner and self.gliner.available())
        report.used_gliner = gliner_ok
        if self.gliner and not gliner_ok:
            report.warnings.append(
                "GLiNER 미설치 — 구조/정규식 추출만 수행 "
                "(pip install gliner 후 재구축하면 본문 엔티티가 추가됩니다)")

        for doc in docs:
            doc_id = doc["id"]
            outlines = db.get_outlines_by_document(doc_id)
            tables = db.get_tables_by_document(doc_id)
            links = db.get_links_by_document(doc_id)
            chunks = db.get_chunks_by_document(doc_id)

            # 구조 기반 (P1.5)
            res = self.structure.extract(doc, outlines, tables, links)
            for e in res.entities:
                e_doc = (e, doc_id)
                all_entities.append(e)
                e._doc_id = doc_id  # 문서 컨텍스트 부착
            for r in res.relations:
                r._doc_id = doc_id
                all_relations.append(r)

            # 본문 청크: 정규식 + GLiNER
            for chunk in chunks:
                cands = self.regex.extract_chunk(chunk)
                if gliner_ok:
                    cands.extend(self.gliner.extract_chunk(chunk))
                for e in cands:
                    e._doc_id = doc_id
                    all_entities.append(e)
                    chunk_entities[chunk["id"]].add((e.name, e.entity_type))
                # 문서 mentions 관계 (본문 엔티티도 출처 연결)
                for e in cands:
                    r = RelationCandidate(
                        source=(doc["filename"], "Document"),
                        relation="mentions",
                        target=(e.name, e.entity_type),
                        confidence=e.confidence, extractor=e.extractor)
                    r._doc_id = doc_id
                    all_relations.append(r)

        # ---------- 2) 엔티티 해소 (P3) ----------
        canonical, merged_count = self._resolve_entities(all_entities)
        report.merged = merged_count

        # ---------- 3) 클래스 신뢰도 (§2.2-②) ----------
        unique = sorted({(c.name, c.entity_type) for c in all_entities
                         if canonical.get((c.name, c.entity_type),
                                          (c.name, c.entity_type))
                         == (c.name, c.entity_type)})
        names = [n for n, _ in unique]
        types = [t for _, t in unique]
        type_conf = dict(zip(unique, class_confidence(names, types,
                                                      self.ontology)))

        # ---------- 4) 엔티티/멘션 저장 ----------
        entity_ids: dict[tuple[str, str], int] = {}
        alias_map: dict[tuple[str, str], set[str]] = defaultdict(set)
        for c in all_entities:
            key = canonical.get((c.name, c.entity_type),
                                (c.name, c.entity_type))
            if key != (c.name, c.entity_type):
                alias_map[key].add(c.name)

        for c in all_entities:
            key = canonical.get((c.name, c.entity_type),
                                (c.name, c.entity_type))
            if key not in entity_ids:
                entity_ids[key] = kgdb.upsert_entity(
                    name=key[0], entity_type=key[1],
                    type_confidence=type_conf.get(key, 0.5),
                    aliases=sorted(alias_map.get(key, set())))
            kgdb.add_mention(
                entity_id=entity_ids[key],
                document_id=getattr(c, "_doc_id", 0) or 0,
                page_number=c.page_number,
                span_text=c.span_text[:200],
                confidence=c.confidence,
                extractor=c.extractor,
                chunk_id=c.chunk_id)
        report.entities = len(entity_ids)
        report.mentions = len(all_entities)

        # ---------- 5) 공출현 관계 (P4) ----------
        cooc_relations = self._cooccurrence_relations(chunk_entities,
                                                      canonical)
        all_relations.extend(cooc_relations)

        # ---------- 6) 온톨로지 검증 + 저장 ----------
        saved = violations = 0
        for r in all_relations:
            src = canonical.get(r.source, r.source)
            tgt = canonical.get(r.target, r.target)
            if src == tgt:
                continue
            if not self.ontology.validate_relation(r.relation, src[1], tgt[1]):
                violations += 1
                continue
            sid, tid = entity_ids.get(src), entity_ids.get(tgt)
            if sid is None or tid is None:
                continue
            kgdb.upsert_relation(
                source_id=sid, relation_type=r.relation, target_id=tid,
                confidence=r.confidence, extractor=r.extractor,
                document_id=getattr(r, "_doc_id", None))
            saved += 1
        report.relations = saved
        report.violations = violations

        logger.info("KG 구축 완료: 엔티티 %d, 관계 %d (위반 거부 %d)",
                    report.entities, report.relations, report.violations)
        return report

    # ------------------------------------------------------------------
    # 엔티티 해소 (P3) — 보수적 병합
    # ------------------------------------------------------------------

    def _resolve_entities(
        self, candidates: list[EntityCandidate]
    ) -> tuple[dict[tuple[str, str], tuple[str, str]], int]:
        """
        (name, type) → 대표 (name, type) 매핑을 만든다.
        1) 정규화(소문자/공백 제거) 후 완전 일치 → 멘션 많은 쪽을 대표로
        2) (모델 있으면) 같은 타입 내 임베딩 유사도 >= 0.92 → 병합
        """
        counts = Counter((c.name, c.entity_type) for c in candidates)
        keys = list(counts.keys())
        canonical: dict[tuple[str, str], tuple[str, str]] = {}

        # --- 1단계: 표기 정규화 일치 ---
        norm_groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for name, etype in keys:
            norm = normalize_name(name).lower().replace(" ", "")
            norm_groups[(norm, etype)].append((name, etype))
        merged = 0
        for group in norm_groups.values():
            if len(group) < 2:
                continue
            rep = max(group, key=lambda k: counts[k])
            for k in group:
                if k != rep:
                    canonical[k] = rep
                    merged += 1

        # --- 2단계: 임베딩 유사도 병합 (같은 타입만, 모델 있을 때만) ---
        try:
            from .embeddings import embed_texts, models_ready
            if models_ready()["text_model"]:
                import numpy as np
                remaining = [k for k in keys if k not in canonical]
                by_type: dict[str, list[tuple[str, str]]] = defaultdict(list)
                for k in remaining:
                    by_type[k[1]].append(k)
                for etype, group in by_type.items():
                    if etype == "Document":
                        # 문서 자기-엔티티(파일명)는 절대 의미 유사도로 병합하면
                        # 안 된다. 같은 명명 규칙("서울_3반_이름_학번.pdf")을 쓰는
                        # 서로 다른 문서들은 문자열이 비슷해 임베딩 유사도가
                        # 0.97+ 로 나오지만 실제로는 완전히 다른 문서다.
                        continue
                    if len(group) < 2 or len(group) > 400:
                        continue  # 너무 크면 O(n^2) 회피
                    vecs = embed_texts([k[0] for k in group])
                    sims = vecs @ vecs.T
                    for i in range(len(group)):
                        for j in range(i + 1, len(group)):
                            if float(sims[i, j]) >= self.MERGE_SIM_THRESHOLD:
                                a, b = group[i], group[j]
                                rep, dup = ((a, b) if counts[a] >= counts[b]
                                            else (b, a))
                                if dup not in canonical:
                                    canonical[dup] = canonical.get(rep, rep)
                                    merged += 1
        except Exception as e:
            logger.info("임베딩 병합 생략: %s", e)

        # 경로 압축 (A→B→C 를 A→C 로)
        def resolve(k):
            seen = set()
            while k in canonical and k not in seen:
                seen.add(k)
                k = canonical[k]
            return k
        canonical = {k: resolve(k) for k in list(canonical.keys())}
        return canonical, merged

    # ------------------------------------------------------------------
    # 공출현 관계 (P4) — PMI 정규화 신뢰도
    # ------------------------------------------------------------------

    def _cooccurrence_relations(
        self,
        chunk_entities: dict[int, set[tuple[str, str]]],
        canonical: dict[tuple[str, str], tuple[str, str]],
    ) -> list[RelationCandidate]:
        """같은 청크에 함께 등장한 엔티티 쌍 → related_to (NPMI 기반 신뢰도)."""
        n_chunks = len(chunk_entities)
        if n_chunks == 0:
            return []

        entity_freq: Counter = Counter()
        pair_freq: Counter = Counter()
        for ents in chunk_entities.values():
            resolved = {canonical.get(e, e) for e in ents}
            # Date/Metric은 공출현 노이즈가 심해 제외
            resolved = {e for e in resolved if e[1] not in ("Date", "Metric")}
            for e in resolved:
                entity_freq[e] += 1
            ents_sorted = sorted(resolved)
            for i in range(len(ents_sorted)):
                for j in range(i + 1, len(ents_sorted)):
                    pair_freq[(ents_sorted[i], ents_sorted[j])] += 1

        out: list[RelationCandidate] = []
        scored = []
        for (a, b), n_ab in pair_freq.items():
            if n_ab < self.MIN_COOC:
                continue
            p_ab = n_ab / n_chunks
            p_a = entity_freq[a] / n_chunks
            p_b = entity_freq[b] / n_chunks
            pmi = math.log(p_ab / (p_a * p_b) + 1e-12)
            npmi = pmi / (-math.log(p_ab) + 1e-12)   # [-1, 1]
            if npmi <= 0:
                continue
            conf = round(min(self.COOC_BASE_CONF, npmi * self.COOC_BASE_CONF
                             + 0.15), 4)
            scored.append((conf, a, b))

        scored.sort(reverse=True)
        for conf, a, b in scored[: self.MAX_COOC_EDGES]:
            out.append(RelationCandidate(
                source=a, relation="related_to", target=b,
                confidence=conf, extractor="cooc"))
        return out


def build_knowledge_graph(use_gliner: bool = True) -> BuildReport:
    """기본 설정으로 지식 그래프 재구축 (편의 함수)."""
    return KGBuilder(use_gliner=use_gliner).build(rebuild=True)


# ---------------------------------------------------------------------------
# 그래프 조회 (API/UI용)
# ---------------------------------------------------------------------------

def get_entity_graph(min_confidence: float = 0.3,
                     max_nodes: int = 150) -> dict:
    """엔티티 그래프를 JSON 직렬화 가능한 형태로 반환."""
    kgdb.init_kg_db()
    entities = kgdb.list_entities()
    relations = kgdb.list_relations(min_confidence=min_confidence)

    # mentions 관계는 그래프를 과밀하게 하므로 노드 연결용으로만 사용
    core_rels = [r for r in relations if r["relation_type"] != "mentions"]

    # 관계에 등장하는 엔티티 우선 + 멘션 많은 순
    connected_ids = {r["source_id"] for r in core_rels} | \
                    {r["target_id"] for r in core_rels}
    entities.sort(key=lambda e: (e["id"] not in connected_ids,
                                 -e["mention_count"]))
    entities = entities[:max_nodes]
    kept_ids = {e["id"] for e in entities}

    nodes = [{
        "id": e["id"],
        "label": e["name"],
        "entity_type": e["entity_type"],
        "type_confidence": e["type_confidence"],
        "mention_count": e["mention_count"],
        "document_count": e["document_count"],
        "aliases": e["aliases"],
    } for e in entities]

    edges = [{
        "source": r["source_id"],
        "target": r["target_id"],
        "relation": r["relation_type"],
        "confidence": r["confidence"],
        "evidence_count": r["evidence_count"],
        "extractor": r["extractor"],
    } for r in core_rels
        if r["source_id"] in kept_ids and r["target_id"] in kept_ids]

    return {"nodes": nodes, "edges": edges, "stats": kgdb.kg_stats()}
