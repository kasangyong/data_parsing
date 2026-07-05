"""엔티티 지식 그래프 구축 end-to-end 테스트 (격리된 임시 DB 사용)."""
import os
import tempfile

if "PDFSEARCH_DATA_DIR" not in os.environ:
    os.environ["PDFSEARCH_DATA_DIR"] = os.path.join(
        tempfile.mkdtemp(prefix="pdfsearch_test_"), ".pdfsearch")

import unittest

from pdfsearch import database as db
from pdfsearch import kg_database as kgdb
from pdfsearch.kg_builder import KGBuilder, get_entity_graph


class TestKGBuildE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        kgdb.init_kg_db()

    def setUp(self):
        # 매 테스트마다 KG 초기화 (문서는 남겨도 되지만 결정성을 위해 재구축)
        kgdb.clear_kg()

    def _seed_document(self):
        import uuid
        h = uuid.uuid4().hex
        doc_id = db.insert_document(
            filename=f"annual_{h[:6]}.pdf", file_hash=h,
            stored_path=f"annual_{h[:6]}.pdf", page_count=10,
            metadata={"author": "홍길동", "creation_date": "2024-03-01"})
        db.insert_outline(doc_id, 1, "1. 사업 개요", 1)
        db.insert_outline(doc_id, 2, "1.1 반도체 부문", 2)
        db.insert_table(
            doc_id, 5, 0,
            [["항목", "2024년"], ["매출", "3조원"], ["영업이익", "5000억원"]],
            "항목 2024년 매출 3조원 영업이익 5000억원")
        db.insert_text_chunk(
            doc_id, 3, 0,
            "본 보고서는 2024년 3월 기준이며 매출 3조원을 달성했다.")
        return doc_id

    def test_build_creates_entities_and_relations(self):
        self._seed_document()
        report = KGBuilder(use_gliner=False).build(rebuild=True)

        self.assertGreaterEqual(report.documents, 1)
        self.assertGreater(report.entities, 0)
        self.assertGreater(report.relations, 0)

        stats = kgdb.kg_stats()
        self.assertGreater(stats["entities"], 0)
        # 문서 엔티티 + 저자(Person) + 날짜(Date) + 개념/수치가 있어야 한다
        types = stats["entities_by_type"]
        self.assertIn("Document", types)
        self.assertIn("Person", types)

    def test_relations_respect_ontology(self):
        self._seed_document()
        KGBuilder(use_gliner=False).build(rebuild=True)

        onto = KGBuilder(use_gliner=False).ontology
        relations = kgdb.list_relations()
        self.assertTrue(relations)
        # 저장된 모든 관계는 온톨로지 domain/range 를 만족해야 한다
        id_to_type = {e["id"]: e["entity_type"] for e in kgdb.list_entities()}
        for r in relations:
            st = id_to_type[r["source_id"]]
            tt = id_to_type[r["target_id"]]
            self.assertTrue(
                onto.validate_relation(r["relation_type"], st, tt),
                f"온톨로지 위반 관계가 저장됨: {st} -{r['relation_type']}-> {tt}")

    def test_noisy_or_confidence_combination(self):
        # 같은 트리플을 두 번 upsert 하면 신뢰도가 노이즈-OR 로 결합되고 증거 수 증가
        a = kgdb.upsert_entity("삼성전자", "Organization", 0.9)
        b = kgdb.upsert_entity("반도체", "Product", 0.9)
        kgdb.upsert_relation(a, "produces", b, 0.6, "rule")
        kgdb.upsert_relation(a, "produces", b, 0.6, "rule")
        rels = [r for r in kgdb.list_relations()
                if r["source_id"] == a and r["target_id"] == b]
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["evidence_count"], 2)
        # 1 - (1-0.6)(1-0.6) = 0.84
        self.assertAlmostEqual(rels[0]["confidence"], 0.84, places=3)

    def test_entity_graph_serialization(self):
        self._seed_document()
        KGBuilder(use_gliner=False).build(rebuild=True)
        graph = get_entity_graph(min_confidence=0.0, max_nodes=100)

        self.assertIn("nodes", graph)
        self.assertIn("edges", graph)
        self.assertIn("stats", graph)
        self.assertTrue(graph["nodes"])
        node_ids = {n["id"] for n in graph["nodes"]}
        for e in graph["edges"]:
            # 엣지 양끝은 반환된 노드 집합 안에 있어야 한다
            self.assertIn(e["source"], node_ids)
            self.assertIn(e["target"], node_ids)
        # mentions 관계는 코어 그래프에서 제외된다
        for e in graph["edges"]:
            self.assertNotEqual(e["relation"], "mentions")

    def test_upsert_entity_merges_aliases(self):
        e1 = kgdb.upsert_entity("삼성전자", "Organization", 0.7, aliases=["삼성"])
        e2 = kgdb.upsert_entity("삼성전자", "Organization", 0.9, aliases=["Samsung"])
        self.assertEqual(e1, e2)  # 같은 (name, type) → 같은 id
        ent = next(e for e in kgdb.list_entities() if e["id"] == e1)
        self.assertIn("삼성", ent["aliases"])
        self.assertIn("Samsung", ent["aliases"])
        self.assertAlmostEqual(ent["type_confidence"], 0.9)  # 최대값 유지


if __name__ == "__main__":
    unittest.main()
