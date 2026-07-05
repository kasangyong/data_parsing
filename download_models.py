"""
(레거시 래퍼) 임베딩 모델 다운로드.

이제 CLI 명령을 사용하세요:
    pdfsearch models

이 스크립트는 기존 사용법 호환을 위해 남겨둔 래퍼입니다.
"""
import sys

from pdfsearch.cli import cmd_models

if __name__ == "__main__":
    sys.exit(cmd_models(None))
