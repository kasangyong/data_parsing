"""
평가 하네스 (P1) — 추출 품질을 자동 측정하는 실험·평가 장치.

ROADMAP §3 구현:
- 골든셋: `.pdfsearch/harness/golden/*.json` (사람이 검수한 정답)
- 실행:   pdfsearch harness run      → 리포트 저장 + 기준 비교
- 지표:   엔티티 P/R/F1 (엄격/부분일치), 타입별 F1, 관계 F1,
          온톨로지 위반 수, 고아 노드 비율, ECE(신뢰도 보정 오차)
- 회귀:   baselines.json 대비 F1 하락 시 실패 코드 반환 (CI 게이트)

골든셋 포맷 (문서당 1파일, 예: golden/report1.json):
{
  "document": "report1.pdf",          # documents.filename 과 일치
  "entities": [
    {"name": "삼성전자", "type": "Organization"},
    {"name": "매출 3조원", "type": "Metric"}
  ],
  "relations": [
    {"source": "삼성전자", "relation": "produces", "target": "반도체"}
  ]
}
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 회귀 게이트: 기준 대비 이 이상 하락하면 실패
REGRESSION_TOLERANCE = 0.02   # F1 -2%p


# ---------------------------------------------------------------------------
# 지표 계산
# ---------------------------------------------------------------------------

def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _norm(s: str) -> str:
    return "".join(s.lower().split())


@dataclass
class PRF:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        return _f1(self.precision, self.recall)

    def to_dict(self) -> dict:
        return {"precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "f1": round(self.f1, 4),
                "tp": self.tp, "fp": self.fp, "fn": self.fn}


def match_entities(predicted: list[dict], gold: list[dict],
                   partial: bool = False) -> PRF:
    """
    엔티티 매칭. strict: (정규화 이름, 타입) 완전 일치.
    partial: 이름 부분 포함 + 타입 일치도 정답으로 인정 (soft match).
    """
    gold_set = [( _norm(g["name"]), g["type"]) for g in gold]
    pred_set = [( _norm(p["name"]), p["type"]) for p in predicted]

    prf = PRF()
    unmatched_gold = list(gold_set)
    for pname, ptype in pred_set:
        hit = None
        for i, (gname, gtype) in enumerate(unmatched_gold):
            if ptype == gtype and (
                pname == gname or
                (partial and (pname in gname or gname in pname))
            ):
                hit = i
                break
        if hit is not None:
            prf.tp += 1
            unmatched_gold.pop(hit)
        else:
            prf.fp += 1
    prf.fn = len(unmatched_gold)
    return prf


def match_relations(predicted: list[dict], gold: list[dict]) -> PRF:
    """관계(트리플) 매칭 — (주어, 술어, 목적어) 정규화 완전 일치."""
    gold_set = {(_norm(g["source"]), g["relation"], _norm(g["target"]))
                for g in gold}
    pred_set = {(_norm(p["source"]), p["relation"], _norm(p["target"]))
                for p in predicted}
    prf = PRF()
    prf.tp = len(gold_set & pred_set)
    prf.fp = len(pred_set - gold_set)
    prf.fn = len(gold_set - pred_set)
    return prf


def expected_calibration_error(confidences: list[float],
                               correct: list[bool],
                               n_bins: int = 10) -> float:
    """
    ECE (ROADMAP §2.4) — 신뢰도 구간별 |평균신뢰도 - 실제정답률| 가중합.
    """
    if not confidences:
        return 0.0
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for conf, ok in zip(confidences, correct):
        idx = min(int(conf * n_bins), n_bins - 1)
        bins[idx].append((conf, ok))
    total = len(confidences)
    ece = 0.0
    for b in bins:
        if not b:
            continue
        avg_conf = sum(c for c, _ in b) / len(b)
        accuracy = sum(1 for _, ok in b if ok) / len(b)
        ece += (len(b) / total) * abs(avg_conf - accuracy)
    return round(ece, 4)


# ---------------------------------------------------------------------------
# 하네스 러너
# ---------------------------------------------------------------------------

@dataclass
class HarnessReport:
    timestamp: str = ""
    documents_evaluated: int = 0
    entity_strict: dict = field(default_factory=dict)
    entity_partial: dict = field(default_factory=dict)
    entity_by_type: dict = field(default_factory=dict)
    relation: dict = field(default_factory=dict)
    ece: float = 0.0
    graph_quality: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "documents_evaluated": self.documents_evaluated,
            "entity_strict": self.entity_strict,
            "entity_partial": self.entity_partial,
            "entity_by_type": self.entity_by_type,
            "relation": self.relation,
            "ece": self.ece,
            "graph_quality": self.graph_quality,
            "warnings": self.warnings,
        }


class Harness:
    """골든셋 기반 평가 러너."""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            from .config import DATA_DIR
            data_dir = DATA_DIR
        self.base = Path(data_dir) / "harness"
        self.golden_dir = self.base / "golden"
        self.report_dir = self.base / "report"
        self.baseline_path = self.base / "baselines.json"

    # ----- 골든셋 -----

    def init_scaffold(self) -> Path:
        """골든셋 폴더 + 예시 템플릿 생성."""
        self.golden_dir.mkdir(parents=True, exist_ok=True)
        self.report_dir.mkdir(parents=True, exist_ok=True)
        example = self.golden_dir / "_example.json.template"
        if not example.exists():
            example.write_text(json.dumps({
                "document": "문서파일명.pdf",
                "entities": [
                    {"name": "삼성전자", "type": "Organization"},
                    {"name": "머신러닝", "type": "Concept"},
                ],
                "relations": [
                    {"source": "문서파일명.pdf", "relation": "mentions",
                     "target": "머신러닝"},
                ],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.golden_dir

    def load_golden(self) -> list[dict]:
        if not self.golden_dir.exists():
            return []
        out = []
        for p in sorted(self.golden_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("document"):
                    out.append(data)
            except Exception as e:
                logger.warning("골든셋 로드 실패 (%s): %s", p.name, e)
        return out

    # ----- 예측 수집 (현재 KG에서) -----

    def _predictions_for(self, filename: str) -> tuple[list[dict], list[dict]]:
        """해당 문서에서 추출된 엔티티/관계를 KG DB에서 수집."""
        from . import kg_database as kgdb
        from .database import db_session

        kgdb.init_kg_db()
        with db_session() as conn:
            doc = conn.execute(
                "SELECT id FROM documents WHERE filename = ?", (filename,)
            ).fetchone()
            if not doc:
                return [], []
            doc_id = doc["id"]

            ents = conn.execute(
                """
                SELECT DISTINCT e.name, e.entity_type, e.type_confidence,
                       MAX(m.extraction_confidence) AS ext_conf
                FROM entities e
                JOIN entity_mentions m ON m.entity_id = e.id
                WHERE m.document_id = ?
                GROUP BY e.id
                """, (doc_id,),
            ).fetchall()
            entities = [{"name": r["name"], "type": r["entity_type"],
                         "confidence": r["ext_conf"]} for r in ents]

            rels = conn.execute(
                """
                SELECT s.name AS source, r.relation_type, t.name AS target,
                       r.confidence
                FROM relations r
                JOIN entities s ON s.id = r.source_id
                JOIN entities t ON t.id = r.target_id
                WHERE r.document_id = ? OR r.document_id IS NULL
                """, (doc_id,),
            ).fetchall()
            relations = [{"source": r["source"],
                          "relation": r["relation_type"],
                          "target": r["target"],
                          "confidence": r["confidence"]} for r in rels]
        return entities, relations

    # ----- 그래프 품질 (골든셋 불필요) -----

    def graph_quality(self) -> dict:
        from . import kg_database as kgdb
        kgdb.init_kg_db()
        stats = kgdb.kg_stats()
        entities = kgdb.list_entities()
        relations = kgdb.list_relations()
        core = [r for r in relations if r["relation_type"] != "mentions"]
        connected = {r["source_id"] for r in core} | \
                    {r["target_id"] for r in core}
        n = len(entities)
        orphan_ratio = round(1 - len(connected) / n, 4) if n else 0.0
        avg_degree = round(2 * len(core) / n, 3) if n else 0.0
        return {
            **stats,
            "orphan_node_ratio": orphan_ratio,
            "avg_degree": avg_degree,
        }

    # ----- 실행 -----

    def run(self) -> HarnessReport:
        report = HarnessReport(
            timestamp=datetime.now().isoformat(timespec="seconds"))
        report.graph_quality = self.graph_quality()

        golden = self.load_golden()
        if not golden:
            report.warnings.append(
                f"골든셋이 없습니다. {self.golden_dir} 에 정답 JSON을 추가하세요 "
                "(pdfsearch harness init 으로 템플릿 생성). "
                "그래프 품질 지표만 측정합니다.")
            self._save_report(report)
            return report

        total_es, total_ep, total_r = PRF(), PRF(), PRF()
        by_type: dict[str, PRF] = {}
        confidences: list[float] = []
        correct: list[bool] = []

        for g in golden:
            pred_e, pred_r = self._predictions_for(g["document"])
            gold_e = g.get("entities", [])
            gold_r = g.get("relations", [])

            if not pred_e and not pred_r:
                report.warnings.append(
                    f"'{g['document']}' — KG에 예측 없음 "
                    "(문서 미인덱싱이거나 kg build 미실행)")

            # 엔티티 (strict / partial)
            es = match_entities(pred_e, gold_e, partial=False)
            ep = match_entities(pred_e, gold_e, partial=True)
            for prf, agg in ((es, total_es), (ep, total_ep)):
                agg.tp += prf.tp
                agg.fp += prf.fp
                agg.fn += prf.fn

            # 타입별
            for t in {e["type"] for e in gold_e} | {e["type"] for e in pred_e}:
                sub = match_entities(
                    [e for e in pred_e if e["type"] == t],
                    [e for e in gold_e if e["type"] == t], partial=True)
                agg = by_type.setdefault(t, PRF())
                agg.tp += sub.tp
                agg.fp += sub.fp
                agg.fn += sub.fn

            # 관계
            rr = match_relations(pred_r, gold_r)
            total_r.tp += rr.tp
            total_r.fp += rr.fp
            total_r.fn += rr.fn

            # ECE용: 예측 엔티티별 (신뢰도, 정답 여부)
            gold_keys = {(_norm(e["name"]), e["type"]) for e in gold_e}
            for p in pred_e:
                conf = p.get("confidence") or 0.5
                ok = (_norm(p["name"]), p["type"]) in gold_keys
                confidences.append(float(conf))
                correct.append(ok)

        report.documents_evaluated = len(golden)
        report.entity_strict = total_es.to_dict()
        report.entity_partial = total_ep.to_dict()
        report.entity_by_type = {t: v.to_dict() for t, v in by_type.items()}
        report.relation = total_r.to_dict()
        report.ece = expected_calibration_error(confidences, correct)

        self._save_report(report)
        return report

    def _save_report(self, report: HarnessReport) -> Path:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        name = report.timestamp.replace(":", "").replace("-", "")
        path = self.report_dir / f"{name}.json"
        path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
        return path

    # ----- 회귀 게이트 -----

    def check_regression(self, report: HarnessReport) -> tuple[bool, str]:
        """baselines.json 대비 회귀 검사. (통과 여부, 메시지)"""
        if not self.baseline_path.exists():
            return True, "기준(baseline) 없음 — 이번 결과를 기준으로 저장하려면: pdfsearch harness baseline"
        try:
            base = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except Exception:
            return True, "기준 파일 손상 — 재저장 필요"

        msgs = []
        ok = True
        for key, label in (("entity_partial", "엔티티 F1(부분일치)"),
                           ("relation", "관계 F1")):
            base_f1 = (base.get(key) or {}).get("f1")
            cur_f1 = (getattr(report, key) or {}).get("f1")
            if base_f1 is None or cur_f1 is None:
                continue
            diff = cur_f1 - base_f1
            if diff < -REGRESSION_TOLERANCE:
                ok = False
                msgs.append(f"[회귀] {label}: {base_f1:.3f} → {cur_f1:.3f} "
                            f"({diff:+.3f})")
            else:
                msgs.append(f"[OK] {label}: {base_f1:.3f} → {cur_f1:.3f} "
                            f"({diff:+.3f})")
        return ok, "\n".join(msgs) if msgs else "비교 항목 없음"

    def save_baseline(self, report: HarnessReport) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        self.baseline_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
