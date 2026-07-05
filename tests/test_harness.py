"""하네스 지표(P/R/F1, ECE, 매칭) 테스트."""
import os
import tempfile

if "PDFSEARCH_DATA_DIR" not in os.environ:
    os.environ["PDFSEARCH_DATA_DIR"] = os.path.join(
        tempfile.mkdtemp(prefix="pdfsearch_test_"), ".pdfsearch")

import unittest

from pdfsearch.harness import (
    PRF,
    expected_calibration_error,
    match_entities,
    match_relations,
)


class TestPRF(unittest.TestCase):
    def test_precision_recall_f1(self):
        prf = PRF(tp=8, fp=2, fn=2)
        self.assertAlmostEqual(prf.precision, 0.8)
        self.assertAlmostEqual(prf.recall, 0.8)
        self.assertAlmostEqual(prf.f1, 0.8)

    def test_zero_division_safe(self):
        prf = PRF(tp=0, fp=0, fn=0)
        self.assertEqual(prf.precision, 0.0)
        self.assertEqual(prf.recall, 0.0)
        self.assertEqual(prf.f1, 0.0)


class TestEntityMatching(unittest.TestCase):
    def test_strict_exact_match(self):
        pred = [{"name": "삼성전자", "type": "Organization"},
                {"name": "머신러닝", "type": "Concept"}]
        gold = [{"name": "삼성전자", "type": "Organization"}]
        prf = match_entities(pred, gold, partial=False)
        self.assertEqual(prf.tp, 1)
        self.assertEqual(prf.fp, 1)   # 머신러닝은 오탐
        self.assertEqual(prf.fn, 0)

    def test_normalization_ignores_case_space(self):
        pred = [{"name": "Machine Learning", "type": "Concept"}]
        gold = [{"name": "machinelearning", "type": "Concept"}]
        prf = match_entities(pred, gold, partial=False)
        self.assertEqual(prf.tp, 1)

    def test_type_mismatch_is_not_match(self):
        pred = [{"name": "서울", "type": "Organization"}]
        gold = [{"name": "서울", "type": "Location"}]
        prf = match_entities(pred, gold, partial=False)
        self.assertEqual(prf.tp, 0)
        self.assertEqual(prf.fp, 1)
        self.assertEqual(prf.fn, 1)

    def test_partial_match(self):
        # 부분 포함 + 타입 일치 → 정답 인정
        pred = [{"name": "삼성전자 반도체 사업부", "type": "Organization"}]
        gold = [{"name": "삼성전자", "type": "Organization"}]
        strict = match_entities(pred, gold, partial=False)
        partial = match_entities(pred, gold, partial=True)
        self.assertEqual(strict.tp, 0)
        self.assertEqual(partial.tp, 1)

    def test_each_gold_matched_once(self):
        pred = [{"name": "A", "type": "Concept"},
                {"name": "A", "type": "Concept"}]
        gold = [{"name": "A", "type": "Concept"}]
        prf = match_entities(pred, gold, partial=False)
        self.assertEqual(prf.tp, 1)
        self.assertEqual(prf.fp, 1)   # 두 번째 A 는 매칭할 gold 가 없음


class TestRelationMatching(unittest.TestCase):
    def test_triple_exact_match(self):
        pred = [{"source": "삼성전자", "relation": "produces", "target": "반도체"},
                {"source": "A", "relation": "uses", "target": "B"}]
        gold = [{"source": "삼성전자", "relation": "produces", "target": "반도체"}]
        prf = match_relations(pred, gold)
        self.assertEqual(prf.tp, 1)
        self.assertEqual(prf.fp, 1)
        self.assertEqual(prf.fn, 0)

    def test_relation_type_matters(self):
        pred = [{"source": "A", "relation": "uses", "target": "B"}]
        gold = [{"source": "A", "relation": "related_to", "target": "B"}]
        prf = match_relations(pred, gold)
        self.assertEqual(prf.tp, 0)


class TestECE(unittest.TestCase):
    def test_perfect_calibration_is_zero(self):
        # 신뢰도 1.0 이고 전부 정답 → 오차 0
        ece = expected_calibration_error([1.0, 1.0, 1.0], [True, True, True])
        self.assertEqual(ece, 0.0)

    def test_empty_is_zero(self):
        self.assertEqual(expected_calibration_error([], []), 0.0)

    def test_overconfident_has_error(self):
        # 신뢰도 0.9 인데 절반만 정답 → 뚜렷한 보정 오차
        confs = [0.9, 0.9, 0.9, 0.9]
        correct = [True, False, True, False]
        ece = expected_calibration_error(confs, correct)
        self.assertGreater(ece, 0.3)

    def test_ece_in_unit_range(self):
        ece = expected_calibration_error([0.2, 0.8, 0.5], [False, True, True])
        self.assertGreaterEqual(ece, 0.0)
        self.assertLessEqual(ece, 1.0)


if __name__ == "__main__":
    unittest.main()
