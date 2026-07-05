"""
테스트 패키지 초기화.

★ 중요: 여기서 PDFSEARCH_DATA_DIR 를 격리된 임시 폴더로 지정한다.
   패키지 __init__ 은 하위 테스트 모듈이 임포트되기 전에 먼저 실행되므로,
   pdfsearch.config 가 임포트되는 시점에 이 임시 폴더를 데이터 루트로 잡는다.
   (실제 프로젝트의 .pdfsearch/ 를 절대 건드리지 않는다.)
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="pdfsearch_test_")
os.environ["PDFSEARCH_DATA_DIR"] = os.path.join(_TMP, ".pdfsearch")
# 임베딩 모델은 테스트에서 사용하지 않음 (없어도 로직이 폴백하도록 설계됨)
os.environ.setdefault("PDFSEARCH_MODELS_DIR",
                      os.path.join(_TMP, "models_none"))
