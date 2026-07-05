"""
(레거시 래퍼) 폴더 일괄 파싱.

이제 CLI 명령을 사용하세요:
    pdfsearch add <폴더경로>

이 스크립트는 기존 사용법 호환을 위해 남겨둔 래퍼입니다.
기본적으로 현재 프로젝트의 `.pdfsearch/inbox/` (또는 레거시 storage/inbox/)를 처리합니다.
"""
import sys

from pdfsearch.cli import main as cli_main

if __name__ == "__main__":
    # pdfsearch add inbox 와 동일하게 동작
    from pdfsearch.config import INBOX_DIR
    sys.argv = ["pdfsearch", "add", str(INBOX_DIR)]
    sys.exit(cli_main())
