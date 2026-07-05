"""추출기(정규화, 정규식, 구조 기반) 테스트 — 모델/네트워크 불필요."""
import os
import tempfile

if "PDFSEARCH_DATA_DIR" not in os.environ:
    os.environ["PDFSEARCH_DATA_DIR"] = os.path.join(
        tempfile.mkdtemp(prefix="pdfsearch_test_"), ".pdfsearch")

import unittest

from pdfsearch.kg_extract import (
    RegexExtractor,
    StructureExtractor,
    _norm_date,
    normalize_name,
)


class TestNormalization(unittest.TestCase):
    def test_normalize_name_trims_and_collapses(self):
        self.assertEqual(normalize_name("  삼성   전자  "), "삼성 전자")
        self.assertEqual(normalize_name("(머신러닝)"), "머신러닝")
        self.assertEqual(normalize_name("“인용구”.".replace("“", '"').replace("”", '"')),
                         "인용구")

    def test_norm_date(self):
        self.assertEqual(_norm_date("2024-03-15"), "2024.03.15")
        self.assertEqual(_norm_date("2024 / 03"), "2024.03")


class TestRegexExtractor(unittest.TestCase):
    def setUp(self):
        self.ex = RegexExtractor()

    def _chunk(self, text):
        return {"id": 1, "page_number": 3, "content": text}

    def test_extracts_dates(self):
        out = self.ex.extract_chunk(self._chunk("보고서는 2024년 3월에 발표되었다."))
        dates = [e for e in out if e.entity_type == "Date"]
        self.assertTrue(dates)
        self.assertEqual(dates[0].extractor, "regex")

    def test_extracts_metrics(self):
        out = self.ex.extract_chunk(self._chunk("매출은 3조원, 성장률은 12% 였다."))
        metrics = [e for e in out if e.entity_type == "Metric"]
        names = {m.name for m in metrics}
        self.assertTrue(any("조원" in n for n in names))
        self.assertTrue(any("%" in n for n in names))

    def test_page_and_chunk_id_propagated(self):
        out = self.ex.extract_chunk(self._chunk("2023년 매출 500억원"))
        self.assertTrue(out)
        for e in out:
            self.assertEqual(e.page_number, 3)
            self.assertEqual(e.chunk_id, 1)

    def test_caps_per_chunk(self):
        text = " ".join(f"{y}년" for y in range(2000, 2030))
        out = self.ex.extract_chunk(self._chunk(text))
        dates = [e for e in out if e.entity_type == "Date"]
        self.assertLessEqual(len(dates), self.ex.MAX_PER_CHUNK)


class TestStructureExtractor(unittest.TestCase):
    def setUp(self):
        self.ex = StructureExtractor()

    def test_document_is_entity(self):
        doc = {"filename": "report.pdf", "metadata_json": "{}"}
        res = self.ex.extract(doc, [], [], [])
        docs = [e for e in res.entities if e.entity_type == "Document"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].name, "report.pdf")

    def test_metadata_author_and_date(self):
        doc = {"filename": "r.pdf",
               "metadata_json": '{"author": "홍길동", "creation_date": "2024-03-01"}'}
        res = self.ex.extract(doc, [], [], [])
        types = {e.entity_type for e in res.entities}
        self.assertIn("Person", types)
        self.assertIn("Date", types)
        rels = {r.relation for r in res.relations}
        self.assertIn("authored_by", rels)
        self.assertIn("created_on", rels)

    def test_outline_hierarchy_part_of(self):
        doc = {"filename": "r.pdf", "metadata_json": "{}"}
        outlines = [
            {"level": 1, "title": "1. 서론", "page_number": 1},
            {"level": 2, "title": "1.1 배경", "page_number": 2},
        ]
        res = self.ex.extract(doc, outlines, [], [])
        concepts = [e for e in res.entities if e.entity_type == "Concept"]
        self.assertGreaterEqual(len(concepts), 2)
        part_of = [r for r in res.relations if r.relation == "part_of"]
        self.assertEqual(len(part_of), 1)  # 배경 → 서론

    def test_table_metric_measured_as(self):
        doc = {"filename": "r.pdf", "metadata_json": "{}"}
        tables = [{
            "page_number": 5,
            "table_json": '[["항목", "2024년"], ["매출", "3조원"], ["영업이익", "5000억원"]]',
        }]
        res = self.ex.extract(doc, [], tables, [])
        metrics = [e for e in res.entities if e.entity_type == "Metric"]
        self.assertTrue(metrics)
        measured = [r for r in res.relations if r.relation == "measured_as"]
        self.assertTrue(measured)


if __name__ == "__main__":
    unittest.main()
