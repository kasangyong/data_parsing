"""
온톨로지 (P0) — 지식 그래프의 스키마(가드레일).

- base 온톨로지: 엔티티 9클래스 + 관계 11종 (ROADMAP §1.2)
- 프로젝트별 확장: `.pdfsearch/ontology.yaml` (있으면 base에 병합)
- 검증: 관계의 domain/range가 온톨로지와 일치하는지 검사 (SHACL 대체 경량 구현)

wildcard "*" 는 모든 엔티티 타입을 허용한다는 뜻.
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# base 온톨로지 정의
# ---------------------------------------------------------------------------

BASE_ENTITY_TYPES: dict[str, str] = {
    # 클래스명: 설명 (설명은 GLiNER 라벨/임베딩 클래스 신뢰도 계산에도 사용)
    "Person":        "사람, 인물, 저자 (예: 홍길동, John Smith)",
    "Organization":  "회사, 기관, 부서, 단체 (예: 삼성전자, 금융위원회)",
    "Location":      "장소, 지명, 국가, 도시 (예: 서울, 판교)",
    "Product":       "제품, 시스템, 서비스, 소프트웨어 (예: GPT-4, 갤럭시)",
    "Concept":       "추상 개념, 기술, 방법론, 용어 (예: 머신러닝, 유동성 리스크)",
    "Event":         "사건, 행사, 회의 (예: 2024 컨퍼런스, 금융위기)",
    "Date":          "날짜, 시간 표현 (예: 2024-03, 3분기)",
    "Metric":        "수치와 단위 (예: 매출 3조원, 정확도 95%)",
    "Document":      "문서, 보고서, 논문 (수집된 PDF 자신)",
}

# 관계명: (도메인 타입들, 레인지 타입들, 설명)
BASE_RELATION_TYPES: dict[str, tuple[list[str], list[str], str]] = {
    "works_for":   (["Person"], ["Organization"], "소속"),
    "located_in":  (["Organization", "Event", "Person"], ["Location"], "위치"),
    "produces":    (["Organization"], ["Product"], "생산/제공"),
    "part_of":     (["*"], ["*"], "일부/하위 (목차 계층 포함)"),
    "uses":        (["Product", "Organization", "Concept"],
                    ["Product", "Concept"], "사용/의존"),
    "related_to":  (["*"], ["*"], "연관 (폴백 관계)"),
    "mentions":    (["Document"], ["*"], "문서가 엔티티를 언급 (출처)"),
    "defines":     (["Document"], ["Concept"], "용어 정의"),
    "measured_as": (["Concept"], ["Metric"], "측정값"),
    "occurred_on": (["Event"], ["Date"], "발생 일시"),
    "authored_by": (["Document"], ["Person"], "저자"),
    "references":  (["Document"], ["Document"], "문서 간 참조 (링크)"),
    "created_on":  (["Document"], ["Date"], "문서 생성일"),
}


@dataclass
class Ontology:
    """base + 프로젝트 확장이 병합된 온톨로지."""
    entity_types: dict[str, str] = field(default_factory=dict)
    relation_types: dict[str, tuple[list[str], list[str], str]] = field(
        default_factory=dict)

    # ---------------- 검증 ----------------

    def is_entity_type(self, t: str) -> bool:
        return t in self.entity_types

    def validate_relation(self, relation: str, source_type: str,
                          target_type: str) -> bool:
        """관계의 domain/range 검사. 위반이면 False (= 환각/오추출 필터)."""
        spec = self.relation_types.get(relation)
        if spec is None:
            return False
        domain, range_, _ = spec
        ok_d = "*" in domain or source_type in domain
        ok_r = "*" in range_ or target_type in range_
        return ok_d and ok_r

    def violations(self, triples: list[tuple[str, str, str]]) -> list[dict]:
        """[(src_type, relation, tgt_type), ...] 중 위반 목록 반환 (하네스용)."""
        out = []
        for src_t, rel, tgt_t in triples:
            if not self.validate_relation(rel, src_t, tgt_t):
                out.append({"source_type": src_t, "relation": rel,
                            "target_type": tgt_t})
        return out

    # ---------------- 직렬화 ----------------

    def to_dict(self) -> dict:
        return {
            "entity_types": self.entity_types,
            "relation_types": {
                k: {"domain": v[0], "range": v[1], "description": v[2]}
                for k, v in self.relation_types.items()
            },
        }


# ---------------------------------------------------------------------------
# 로딩 (base + `.pdfsearch/ontology.yaml` 병합)
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict | None:
    """PyYAML이 있으면 사용, 없으면 JSON 시도 후 건너뜀 (선택 의존성)."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        try:
            return json.loads(text)
        except Exception:
            logger.warning(
                "ontology.yaml 로드 실패: PyYAML 미설치 (pip install pyyaml)")
            return None
    except Exception as e:
        logger.warning("ontology.yaml 파싱 실패: %s", e)
        return None


def load_ontology(data_dir: Path | None = None) -> Ontology:
    """base 온톨로지 + 프로젝트 확장 파일 병합."""
    onto = Ontology(
        entity_types=dict(BASE_ENTITY_TYPES),
        relation_types=dict(BASE_RELATION_TYPES),
    )

    if data_dir is None:
        from .config import DATA_DIR
        data_dir = DATA_DIR
    ext_path = Path(data_dir) / "ontology.yaml"
    if not ext_path.exists():
        return onto

    data = _load_yaml(ext_path)
    if not isinstance(data, dict):
        return onto

    for et in data.get("entity_types") or []:
        if isinstance(et, dict) and et.get("name"):
            onto.entity_types[str(et["name"])] = str(et.get("description", ""))
    for rt in data.get("relation_types") or []:
        if isinstance(rt, dict) and rt.get("name"):
            onto.relation_types[str(rt["name"])] = (
                [str(x) for x in (rt.get("domain") or ["*"])],
                [str(x) for x in (rt.get("range") or ["*"])],
                str(rt.get("description", "")),
            )
    logger.info("프로젝트 온톨로지 확장 로드: %s", ext_path)
    return onto


ONTOLOGY_TEMPLATE = """\
# pdfsearch 프로젝트 온톨로지 확장 (base 온톨로지에 병합됨)
# 예시 — 필요 없으면 이 파일을 삭제해도 됩니다.
entity_types:
  # - name: Regulation
  #   description: 법령, 규정, 가이드라인
relation_types:
  # - name: regulates
  #   domain: [Regulation]
  #   range: [Organization, Product]
  #   description: 규제하다
"""
