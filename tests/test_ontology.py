"""온톨로지 검증 로직 테스트."""
import os
import tempfile

# 실제 프로젝트/레거시 DB 를 건드리지 않도록 격리된 데이터 폴더를 강제한다.
# (unittest 가 top-level 로 모듈을 로드해 tests/__init__ 이 실행되지 않는 경우 대비)
if "PDFSEARCH_DATA_DIR" not in os.environ:
    os.environ["PDFSEARCH_DATA_DIR"] = os.path.join(
        tempfile.mkdtemp(prefix="pdfsearch_test_"), ".pdfsearch")

import unittest

from pdfsearch.ontology import Ontology, load_ontology


class TestOntology(unittest.TestCase):
    def setUp(self):
        self.onto = load_ontology()

    def test_base_has_expected_types(self):
        for t in ("Person", "Organization", "Concept", "Metric", "Document"):
            self.assertTrue(self.onto.is_entity_type(t))
        self.assertFalse(self.onto.is_entity_type("Nonexistent"))

    def test_valid_relation(self):
        # works_for: Person → Organization
        self.assertTrue(
            self.onto.validate_relation("works_for", "Person", "Organization"))
        # authored_by: Document → Person
        self.assertTrue(
            self.onto.validate_relation("authored_by", "Document", "Person"))

    def test_domain_range_violation(self):
        # works_for 의 도메인은 Person 인데 Organization 을 주면 위반
        self.assertFalse(
            self.onto.validate_relation("works_for", "Organization", "Person"))
        # produces 의 레인지는 Product 인데 Person 을 주면 위반
        self.assertFalse(
            self.onto.validate_relation("produces", "Organization", "Person"))

    def test_unknown_relation_rejected(self):
        self.assertFalse(
            self.onto.validate_relation("teleports_to", "Person", "Location"))

    def test_wildcard_relation(self):
        # related_to / mentions 는 와일드카드 도메인·레인지
        self.assertTrue(
            self.onto.validate_relation("related_to", "Metric", "Event"))
        self.assertTrue(
            self.onto.validate_relation("mentions", "Document", "Metric"))

    def test_violations_helper(self):
        triples = [
            ("Person", "works_for", "Organization"),   # 정상
            ("Organization", "works_for", "Person"),    # 위반 (도메인)
            ("Event", "occurred_on", "Date"),           # 정상
            ("Event", "occurred_on", "Person"),         # 위반 (레인지)
        ]
        viols = self.onto.violations(triples)
        self.assertEqual(len(viols), 2)

    def test_project_extension_merges(self):
        # 프로젝트 확장 없이도 base 만으로 온톨로지가 만들어져야 한다
        onto = Ontology(entity_types={"Person": ""},
                        relation_types={"knows": (["Person"], ["Person"], "")})
        self.assertTrue(onto.validate_relation("knows", "Person", "Person"))
        self.assertFalse(onto.validate_relation("knows", "Person", "Organization"))


if __name__ == "__main__":
    unittest.main()
