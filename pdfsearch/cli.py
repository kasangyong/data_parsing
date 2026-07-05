"""
pdfsearch CLI — git처럼 어느 폴더에서든 쓰는 PDF 검색 도구.

사용법:
    pdfsearch init                  # 현재 폴더에 .pdfsearch/ 생성 (git init처럼)
    pdfsearch models                # 임베딩 모델 다운로드 (전역, 최초 1회)
    pdfsearch add 파일.pdf 폴더/     # PDF 파싱 + 인덱싱
    pdfsearch search "검색어"        # 터미널 검색
    pdfsearch search "그래프" -t image
    pdfsearch list                  # 인덱싱된 문서 목록
    pdfsearch status                # 현재 프로젝트 상태
    pdfsearch serve                 # 웹 UI 실행 (현재 프로젝트 데이터)

동작 원리:
    명령 실행 위치에서 상위로 올라가며 `.pdfsearch/` 폴더를 찾는다 (git 방식).
    찾은 폴더가 곧 "현재 프로젝트"이며 DB/인덱스/파일이 모두 그 안에 저장된다.
"""
import argparse
import os
import sys
import time
from pathlib import Path

DATA_DIR_NAME = ".pdfsearch"


# ---------------------------------------------------------------------------
# 프로젝트 탐색 (config 임포트 전에 수행해야 함)
# ---------------------------------------------------------------------------

def _find_project_root(start: Path | None = None) -> Path | None:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / DATA_DIR_NAME).is_dir():
            return candidate
    return None


def _require_project() -> Path:
    """프로젝트를 찾아 PDFSEARCH_DATA_DIR 환경변수를 설정하고 루트를 반환."""
    if os.environ.get("PDFSEARCH_DATA_DIR"):
        return Path(os.environ["PDFSEARCH_DATA_DIR"]).resolve().parent
    root = _find_project_root()
    if root is None:
        print("[오류] pdfsearch 프로젝트가 아닙니다.")
        print("       먼저 프로젝트 폴더에서 실행하세요:  pdfsearch init")
        sys.exit(1)
    os.environ["PDFSEARCH_DATA_DIR"] = str(root / DATA_DIR_NAME)
    return root


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    cwd = Path.cwd().resolve()
    existing = _find_project_root()
    if existing is not None:
        print(f"이미 pdfsearch 프로젝트입니다: {existing / DATA_DIR_NAME}")
        return 0

    data_dir = cwd / DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PDFSEARCH_DATA_DIR"] = str(data_dir)

    # config 임포트 → 하위 폴더 자동 생성, DB 초기화
    from .database import init_db
    init_db()

    # .gitignore에 .pdfsearch/ 추가 (git 프로젝트인 경우)
    gitignore = cwd / ".gitignore"
    marker = f"{DATA_DIR_NAME}/"
    if (cwd / ".git").exists():
        lines = gitignore.read_text(encoding="utf-8").splitlines() \
            if gitignore.exists() else []
        if marker not in [l.strip() for l in lines]:
            with gitignore.open("a", encoding="utf-8") as f:
                if lines and lines[-1].strip():
                    f.write("\n")
                f.write(f"# pdfsearch 데이터\n{marker}\n")
            print(f".gitignore 에 {marker} 추가됨")

    print(f"초기화 완료: {data_dir}")
    print("다음 단계:")
    print("  pdfsearch add <PDF파일 또는 폴더>   # 문서 추가")
    print("  pdfsearch serve                     # 웹 UI 실행")
    return 0


# ---------------------------------------------------------------------------
# models (전역 모델 다운로드)
# ---------------------------------------------------------------------------

def cmd_models(args) -> int:
    # models 명령은 프로젝트가 없어도 실행 가능 (전역 다운로드)
    root = _find_project_root()
    if root is not None:
        os.environ.setdefault("PDFSEARCH_DATA_DIR", str(root / DATA_DIR_NAME))
    else:
        # 프로젝트 밖이면 임시 데이터 폴더 생성 방지를 위해 홈 아래 사용
        os.environ.setdefault(
            "PDFSEARCH_DATA_DIR", str(Path.home() / ".pdfsearch" / "default"))

    from .config import (
        CLIP_IMAGE_MODEL_NAME,
        CLIP_TEXT_MODEL_NAME,
        MODELS_DIR,
        TEXT_MODEL_NAME,
    )
    from .embeddings import models_ready

    models = [
        ("텍스트 임베딩 (다국어)", TEXT_MODEL_NAME, "text_model"),
        ("CLIP 이미지 인코더", CLIP_IMAGE_MODEL_NAME, "clip_image_model"),
        ("CLIP 다국어 텍스트 인코더", CLIP_TEXT_MODEL_NAME, "clip_text_model"),
    ]

    print("=" * 60)
    print("임베딩 모델 다운로드 (전역 공유 — 모든 프로젝트에서 사용)")
    print(f"저장 위치: {MODELS_DIR}")
    print("=" * 60)

    status = models_ready()
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("\n[오류] sentence-transformers 가 설치되지 않았습니다.")
        print("먼저 실행하세요:  pip install -e .")
        return 1

    failed = []
    for label, model_name, key in models:
        if status.get(key):
            print(f"\n[스킵] {label} — 이미 다운로드됨")
            continue
        print(f"\n[다운로드] {label}\n  모델: {model_name}")
        start = time.time()
        try:
            model = SentenceTransformer(model_name, cache_folder=str(MODELS_DIR))
            del model
            print(f"  완료 ({time.time() - start:.1f}초)")
        except Exception as e:
            print(f"  실패: {e}")
            failed.append(model_name)

    print("\n" + "=" * 60)
    final = models_ready()
    if final["all_ready"]:
        print("모든 모델이 준비되었습니다!")
        return 0
    print("일부 모델 다운로드에 실패했습니다:")
    for name in failed:
        print(f"  - {name}")
    print("네트워크 연결을 확인하고 다시 실행해주세요.")
    return 1


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

def cmd_add(args) -> int:
    _require_project()

    from .database import init_db
    from .embeddings import ModelNotReadyError, models_ready
    from .pipeline import (
        DuplicateDocumentError,
        ParseFailedError,
        ingest_pdf_bytes,
    )

    if not models_ready()["all_ready"]:
        print("[오류] 임베딩 모델이 아직 다운로드되지 않았습니다.")
        print("먼저 실행하세요:  pdfsearch models")
        return 1

    init_db()

    # 파일/폴더 인자 수집
    pdf_files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            pdf_files.extend(sorted(p.rglob("*.pdf")))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdf_files.append(p)
        else:
            print(f"[경고] 무시함 (PDF 아님/없음): {p}")

    if not pdf_files:
        print("처리할 PDF가 없습니다.")
        return 1

    print(f"처리할 PDF: {len(pdf_files)}개\n")
    ok = skipped = failed = 0
    for i, pdf_path in enumerate(pdf_files, 1):
        label = f"[{i}/{len(pdf_files)}] {pdf_path.name}"
        try:
            start = time.time()
            report = ingest_pdf_bytes(pdf_path.read_bytes(), pdf_path.name)
            elapsed = time.time() - start
            parts = [f"텍스트 {report.text_chunks}", f"이미지 {report.images}",
                     f"표 {report.tables}"]
            print(f"OK   {label} — {report.page_count}p, "
                  f"{', '.join(parts)} ({elapsed:.1f}초)")
            ok += 1
        except DuplicateDocumentError as e:
            print(f"SKIP {label} — 이미 처리됨 (문서 ID {e.existing['id']})")
            skipped += 1
        except ParseFailedError as e:
            print(f"FAIL {label} — {e}")
            failed += 1
        except ModelNotReadyError:
            print("[오류] 모델이 준비되지 않았습니다. pdfsearch models 실행 후 다시 시도하세요.")
            return 1
        except Exception as e:
            print(f"FAIL {label} — 예기치 못한 오류: {e}")
            failed += 1

    print(f"\n완료: 신규 {ok} | 스킵(중복) {skipped} | 실패 {failed}")
    return 0 if failed == 0 else 2


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def cmd_search(args) -> int:
    _require_project()

    from .database import init_db
    from .embeddings import models_ready
    from . import search as search_engine

    if not models_ready()["all_ready"]:
        print("[오류] 임베딩 모델이 없습니다. 먼저:  pdfsearch models")
        return 1

    init_db()
    results = search_engine.search(args.query, search_type=args.type)
    if not results:
        print("검색 결과가 없습니다.")
        return 0

    print(f"검색어: \"{args.query}\"  (유형: {args.type})  — {len(results)}개 문서\n")
    for i, r in enumerate(results, 1):
        print(f"{i}. {r['filename']}  (점수 {r['score']:.3f})")
        for m in r["matches"][:3]:
            kind = m["match_type"]
            page = m["page_number"]
            preview = str(m["preview"])[:100]
            print(f"   - [{kind}] p.{page}: {preview}")
        print()
    return 0


# ---------------------------------------------------------------------------
# list / status
# ---------------------------------------------------------------------------

def cmd_list(args) -> int:
    _require_project()
    from .database import init_db, list_documents
    init_db()
    docs = list_documents()
    if not docs:
        print("인덱싱된 문서가 없습니다.  pdfsearch add <PDF> 로 추가하세요.")
        return 0
    print(f"문서 {len(docs)}개:\n")
    for d in docs:
        print(f"  [{d['id']:>3}] {d['filename']}  — {d['page_count']}p, "
              f"텍스트 {d['chunk_count']}, 이미지 {d['image_count']}, "
              f"표 {d['table_count']}  ({d['created_at']})")
    return 0


def cmd_status(args) -> int:
    root = _find_project_root()
    print("=" * 60)
    print("pdfsearch 상태")
    print("=" * 60)
    if root is None and not os.environ.get("PDFSEARCH_DATA_DIR"):
        print("프로젝트: 없음 (pdfsearch init 으로 생성하세요)")
    else:
        _require_project()
        from .config import DATA_DIR, MODELS_DIR
        from .database import init_db, list_documents
        from .embeddings import models_ready
        from .parser import is_ocr_available
        init_db()
        status = models_ready()
        print(f"프로젝트 데이터: {DATA_DIR}")
        print(f"모델 캐시(전역): {MODELS_DIR}")
        print(f"모델 준비: {'예' if status['all_ready'] else '아니오 — pdfsearch models 실행 필요'}")
        print(f"OCR 가능: {'예' if is_ocr_available() else '아니오 (Tesseract 미설치 — 스캔본 PDF만 영향)'}")
        print(f"문서 수: {len(list_documents())}")
    return 0


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

def cmd_serve(args) -> int:
    root = _require_project()
    print(f"프로젝트: {root}")
    print(f"웹 UI: http://127.0.0.1:{args.port}")

    import uvicorn
    uvicorn.run(
        "pdfsearch.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


# ---------------------------------------------------------------------------
# 엔트리포인트
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pdfsearch",
        description="PDF 멀티모달 검색 — 프로젝트 폴더별 독립 DB (git처럼 동작)",
    )
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="현재 폴더를 pdfsearch 프로젝트로 초기화")
    p_init.set_defaults(func=cmd_init)

    p_models = sub.add_parser("models", help="임베딩 모델 다운로드 (전역, 최초 1회)")
    p_models.set_defaults(func=cmd_models)

    p_add = sub.add_parser("add", help="PDF 파일/폴더를 파싱하고 인덱싱")
    p_add.add_argument("paths", nargs="+", help="PDF 파일 또는 폴더 경로")
    p_add.set_defaults(func=cmd_add)

    p_search = sub.add_parser("search", help="터미널에서 검색")
    p_search.add_argument("query", help="검색어")
    p_search.add_argument("-t", "--type", default="all",
                          choices=["all", "text", "image", "table", "annotation"],
                          help="검색 유형 (기본: all)")
    p_search.set_defaults(func=cmd_search)

    p_list = sub.add_parser("list", help="인덱싱된 문서 목록")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="현재 프로젝트/모델 상태")
    p_status.set_defaults(func=cmd_status)

    p_serve = sub.add_parser("serve", help="웹 UI 실행")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true", help="코드 변경 시 자동 재시작")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
