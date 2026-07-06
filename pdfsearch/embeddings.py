"""
임베딩 모듈 (모델 지연 로딩).

★ 핵심 원칙:
- 이 모듈을 임포트해도 모델은 절대 로드/다운로드되지 않는다.
- 모델은 `download_models.py` 를 실행해야 다운로드된다.
- 모델이 로컬에 없으면 ModelNotReadyError 를 발생시켜
  API 레벨에서 명확한 안내 메시지를 반환할 수 있게 한다.

모델 3종:
1. 텍스트 임베딩  : paraphrase-multilingual-MiniLM-L12-v2 (384차원)
2. 이미지 임베딩  : clip-ViT-B-32 (512차원)
3. 이미지 검색용 텍스트 인코더: clip-ViT-B-32-multilingual-v1 (512차원, CLIP 공간)
"""
import logging
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .config import (
    CLIP_IMAGE_MODEL_NAME,
    CLIP_TEXT_MODEL_NAME,
    MODELS_DIR,
    TEXT_MODEL_NAME,
)

logger = logging.getLogger(__name__)


class ModelNotReadyError(RuntimeError):
    """모델이 아직 다운로드되지 않았을 때 발생."""

    def __init__(self):
        super().__init__(
            "임베딩 모델이 아직 다운로드되지 않았습니다. "
            "터미널에서 `python download_models.py` 를 먼저 실행해주세요."
        )


# ---------------------------------------------------------------------------
# 모델 존재 여부 확인 (모델을 로드하지 않고 파일 시스템만 확인)
# ---------------------------------------------------------------------------

def _hf_cache_dir_for(model_name: str) -> Path:
    """HuggingFace 캐시 디렉터리 경로 계산. 예: models/models--org--name

    SentenceTransformer(..., cache_folder=MODELS_DIR)로 받으면 huggingface_hub가
    저장소를 MODELS_DIR 바로 아래에 두므로 (HF_HOME 기본 레이아웃의 `hub/` 중간
    폴더는 붙지 않음), 여기서도 같은 경로로 확인해야 한다.
    """
    safe = model_name.replace("/", "--")
    return MODELS_DIR / f"models--{safe}"


def _is_model_cached(model_name: str) -> bool:
    d = _hf_cache_dir_for(model_name)
    if not d.exists():
        return False
    # snapshots 폴더에 실제 파일이 있는지 확인
    snapshots = d / "snapshots"
    if not snapshots.exists():
        return False
    return any(snapshots.iterdir())


def models_ready() -> dict:
    """모델별 다운로드 상태. (모델을 로드하지 않음)"""
    status = {
        "text_model": _is_model_cached(TEXT_MODEL_NAME),
        "clip_image_model": _is_model_cached(CLIP_IMAGE_MODEL_NAME),
        "clip_text_model": _is_model_cached(CLIP_TEXT_MODEL_NAME),
    }
    status["all_ready"] = all(status.values())
    return status


# ---------------------------------------------------------------------------
# 지연 로딩 싱글턴
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_text_model = None          # 텍스트 임베딩 모델
_clip_image_model = None    # CLIP 이미지 인코더
_clip_text_model = None     # CLIP 다국어 텍스트 인코더


def _load_model(model_name: str):
    """로컬 캐시에서만 로드 (다운로드 시도 안 함)."""
    if not _is_model_cached(model_name):
        raise ModelNotReadyError()
    from sentence_transformers import SentenceTransformer
    logger.info("모델 로딩: %s", model_name)
    return SentenceTransformer(model_name, cache_folder=str(MODELS_DIR))


def get_text_model():
    global _text_model
    if _text_model is None:
        with _lock:
            if _text_model is None:
                _text_model = _load_model(TEXT_MODEL_NAME)
    return _text_model


def get_clip_image_model():
    global _clip_image_model
    if _clip_image_model is None:
        with _lock:
            if _clip_image_model is None:
                _clip_image_model = _load_model(CLIP_IMAGE_MODEL_NAME)
    return _clip_image_model


def get_clip_text_model():
    global _clip_text_model
    if _clip_text_model is None:
        with _lock:
            if _clip_text_model is None:
                _clip_text_model = _load_model(CLIP_TEXT_MODEL_NAME)
    return _clip_text_model


# ---------------------------------------------------------------------------
# 임베딩 함수 (모두 L2 정규화 → FAISS 내적 = 코사인 유사도)
# ---------------------------------------------------------------------------

def _normalize(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1e-12
    return vectors / norms


def embed_texts(texts: list[str]) -> np.ndarray:
    """텍스트 리스트 → (N, 384) 정규화된 임베딩."""
    if not texts:
        return np.empty((0, 0), dtype=np.float32)
    model = get_text_model()
    vecs = model.encode(texts, batch_size=32, show_progress_bar=False,
                        convert_to_numpy=True)
    return _normalize(vecs)


def embed_images(image_paths: list[str | Path]) -> tuple[np.ndarray, list[int]]:
    """
    이미지 파일 경로 리스트 → (M, 512) 정규화된 CLIP 임베딩.
    손상된 이미지는 건너뛰며, 성공한 인덱스 리스트를 함께 반환한다.

    Returns:
        (embeddings, ok_indices) — ok_indices[i] 는 image_paths 내 원본 인덱스
    """
    from PIL import Image

    if not image_paths:
        return np.empty((0, 0), dtype=np.float32), []

    model = get_clip_image_model()
    pil_images = []
    ok_indices: list[int] = []
    for i, p in enumerate(image_paths):
        try:
            img = Image.open(p)
            img.load()
            if img.mode != "RGB":
                img = img.convert("RGB")
            pil_images.append(img)
            ok_indices.append(i)
        except Exception as e:
            logger.warning("이미지 로드 실패 (%s): %s", p, e)

    if not pil_images:
        return np.empty((0, 0), dtype=np.float32), []

    vecs = model.encode(pil_images, batch_size=16, show_progress_bar=False,
                        convert_to_numpy=True)
    return _normalize(vecs), ok_indices


def embed_query_for_text(query: str) -> np.ndarray:
    """검색어 → 텍스트 인덱스용 쿼리 벡터 (1, 384)."""
    return embed_texts([query])


def embed_query_for_image(query: str) -> np.ndarray:
    """검색어 → CLIP 공간 쿼리 벡터 (1, 512). 자연어로 이미지 검색 가능."""
    model = get_clip_text_model()
    vec = model.encode([query], show_progress_bar=False, convert_to_numpy=True)
    return _normalize(vec)
