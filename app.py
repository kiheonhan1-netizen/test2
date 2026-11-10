"""AI 관상가 - 얼굴 사진으로 관상을 풀이해 주는 Streamlit 앱."""

import base64
import datetime
import hashlib
import html
import io
import logging
import os
import re
import threading
from functools import lru_cache

import numpy as np
import openai
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # 얼굴 영역 자르기용. 없으면 사진 전체를 그대로 보낸다.
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from prompts import (
    MULTI_FACE_MESSAGE,
    NO_FACE_MESSAGE,
    REQUIRED_SECTION,
    RETRY_PROMPT,
    SYSTEM_PROMPT,
    USER_PROMPT,
)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gwansang")

DEFAULT_MODEL = "gpt-4o"
DISCLAIMER = "⚠️ 재미로 보는 결과이며 과학적 근거가 없습니다."
ALLOWED_TYPES = ["jpg", "jpeg", "png", "webp"]
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10MB
MAX_IMAGE_SIDE = 1024
WORK_IMAGE_SIDE = 2048  # 얼굴을 찾고 자를 때 쓰는 해상도 (자른 뒤 MAX_IMAGE_SIDE로 줄인다)
# 얼굴 검출 상자(대략 눈썹~턱) 기준으로 이마·머리, 귀, 턱이 보이도록 넓히는 비율
FACE_MARGIN_TOP, FACE_MARGIN_SIDE, FACE_MARGIN_BOTTOM = 0.45, 0.35, 0.3
# 두 번째로 큰 얼굴이 가장 큰 얼굴 면적의 이 비율 이상이면 '여러 명'으로 보고 자르지 않는다.
MULTI_FACE_AREA_RATIO = 0.5
# 4천만 화소(예: 8000x5000)까지만 허용한다. 일반 휴대폰 사진은 충분히 들어가고,
# 파일은 작지만 해상도만 거대한 이미지(압축 폭탄)로 서버 메모리가 터지는 것을 막는다.
MAX_IMAGE_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
MAX_COMPLETION_TOKENS = 1500
REQUEST_TIMEOUT_SEC = 60
DEFAULT_MAX_ANALYSES_PER_SESSION = 10
DEFAULT_MAX_ANALYSES_PER_DAY = 200
MAX_RESULTS_KEPT = 10  # 세션에 보관하는 사진별 결과 수

TOO_BIG_IMAGE_MESSAGE = "사진 해상도가 너무 큽니다. 4천만 화소 이하의 사진을 올려주세요."
UNREADABLE_IMAGE_MESSAGE = "이미지를 읽을 수 없습니다. 손상되지 않은 JPG, PNG, WEBP 파일을 올려주세요."


class AnalysisError(Exception):
    """사용자에게 보여줄 한국어 메시지를 담은 분석 오류."""


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------
def get_config(key: str, default: str | None = None) -> str | None:
    """st.secrets(Streamlit Cloud) → .env(os.getenv) 순서로 설정값을 찾는다."""
    try:
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        # 로컬에 secrets.toml이 없으면 st.secrets 접근 시 예외가 발생한다.
        pass
    return os.getenv(key, default)


def get_int_config(key: str, default: int) -> int:
    """정수 설정값을 읽는다. 없거나 잘못된 값이면 기본값을 쓴다."""
    value = get_config(key)
    try:
        return max(0, int(value)) if value not in (None, "") else default
    except ValueError:
        logger.warning("%s 값이 정수가 아니어서 기본값 %d을 사용합니다.", key, default)
        return default


def get_api_key() -> str:
    """OpenAI API 키를 반환한다. 없으면 안내 후 앱 실행을 멈춘다."""
    api_key = get_config("OPENAI_API_KEY")
    if not api_key:
        st.error(
            "API 키가 설정되지 않았습니다. "
            "로컬에서는 `.env` 파일을, Streamlit Cloud에서는 Secrets 설정을 확인하세요."
        )
        st.stop()
    return api_key


def get_model() -> str:
    """사용할 OpenAI 모델명을 반환한다."""
    return get_config("OPENAI_MODEL") or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# 사용 횟수 제한 (API 비용 보호)
# ---------------------------------------------------------------------------
@st.cache_resource
def _daily_usage() -> dict:
    """모든 사용자가 공유하는 하루 사용량 카운터. 앱이 재시작되면 초기화된다."""
    return {"lock": threading.Lock(), "date": None, "count": 0}


def remaining_session_analyses() -> int:
    limit = get_int_config("MAX_ANALYSES_PER_SESSION", DEFAULT_MAX_ANALYSES_PER_SESSION)
    return max(0, limit - st.session_state.get("analysis_count", 0))


def remaining_analyses() -> int:
    """세션 한도와 하루 한도 중 더 적은 남은 횟수 (화면 표시용)."""
    daily_limit = get_int_config("MAX_ANALYSES_PER_DAY", DEFAULT_MAX_ANALYSES_PER_DAY)
    usage = _daily_usage()
    with usage["lock"]:
        used_today = usage["count"] if usage["date"] == datetime.date.today() else 0
    return min(remaining_session_analyses(), max(0, daily_limit - used_today))


def reserve_analysis() -> str | None:
    """분석 1회를 예약한다. 한도를 넘으면 사용자에게 보여줄 안내 문구를 반환한다."""
    if remaining_session_analyses() <= 0:
        return "이번 접속에서 사용할 수 있는 분석 횟수를 모두 사용했습니다. 잠시 후 다시 이용해주세요."
    daily_limit = get_int_config("MAX_ANALYSES_PER_DAY", DEFAULT_MAX_ANALYSES_PER_DAY)
    usage = _daily_usage()
    with usage["lock"]:
        today = datetime.date.today()
        if usage["date"] != today:
            usage["date"], usage["count"] = today, 0
        if usage["count"] >= daily_limit:
            return "오늘 준비된 분석 횟수가 모두 소진되었습니다. 내일 다시 이용해주세요."
        usage["count"] += 1
    st.session_state["analysis_count"] = st.session_state.get("analysis_count", 0) + 1
    return None


def release_analysis() -> None:
    """분석이 실패하면 예약한 1회를 돌려준다."""
    st.session_state["analysis_count"] = max(0, st.session_state.get("analysis_count", 0) - 1)
    usage = _daily_usage()
    with usage["lock"]:
        usage["count"] = max(0, usage["count"] - 1)


# ---------------------------------------------------------------------------
# 이미지 처리
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _face_detector():
    """OpenCV 기본 정면 얼굴 검출기(Haar cascade). 사용할 수 없으면 None."""
    if cv2 is None:
        return None
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return None if detector.empty() else detector


def crop_to_face(img: Image.Image) -> Image.Image:
    """사진에서 가장 큰 얼굴 주변만 잘라 낸다.

    관상 풀이에 필요한 얼굴에 집중하고, 명찰·로고·배경처럼 인물을 짐작하게 하는 요소를 빼서
    AI가 신원 문제로 풀이를 거절하는 일을 줄인다. 얼굴을 못 찾았거나 비슷한 크기의 얼굴이
    여럿이면 원본을 그대로 반환한다(여러 명 판단은 AI에게 맡긴다).
    """
    detector = _face_detector()
    if detector is None:
        return img
    try:
        gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
        min_side = max(40, min(img.size) // 12)
        faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(min_side, min_side))
    except Exception:  # 검출 실패는 치명적이지 않으므로 원본으로 진행
        logger.exception("얼굴 검출 실패")
        return img
    if len(faces) == 0:
        return img
    faces = sorted(faces, key=lambda f: int(f[2]) * int(f[3]), reverse=True)
    x, y, w, h = (int(v) for v in faces[0])
    if len(faces) > 1 and faces[1][2] * faces[1][3] >= MULTI_FACE_AREA_RATIO * w * h:
        return img
    left = max(0, int(x - w * FACE_MARGIN_SIDE))
    top = max(0, int(y - h * FACE_MARGIN_TOP))
    right = min(img.width, int(x + w * (1 + FACE_MARGIN_SIDE)))
    bottom = min(img.height, int(y + h * (1 + FACE_MARGIN_BOTTOM)))
    return img.crop((left, top, right, bottom))


def preprocess_image(image_bytes: bytes) -> bytes:
    """이미지를 검증하고 회전 보정·RGB 변환·리사이즈한 JPEG 바이트를 반환한다.

    디스크에 저장하지 않고 메모리에서만 처리한다.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            # Image.open은 헤더만 읽으므로, 픽셀을 디코딩하기 전에 해상도를 검사한다.
            width, height = img.size
            if width * height > MAX_IMAGE_PIXELS:
                raise AnalysisError(TOO_BIG_IMAGE_MESSAGE)
            img.draft("RGB", (WORK_IMAGE_SIDE, WORK_IMAGE_SIDE))  # JPEG는 디코딩 단계에서 축소해 메모리 절약
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                # 투명 배경은 흰색으로 채운다.
                rgba = img.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((WORK_IMAGE_SIDE, WORK_IMAGE_SIDE))
            img = crop_to_face(img)
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=85)
    except AnalysisError:
        raise
    except Image.DecompressionBombError as exc:
        logger.warning("해상도 초과 이미지 거부: %s", exc)
        raise AnalysisError(TOO_BIG_IMAGE_MESSAGE) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.warning("이미지 처리 실패: %s", exc)
        raise AnalysisError(UNREADABLE_IMAGE_MESSAGE) from exc
    except Exception as exc:  # 예상하지 못한 Pillow 오류도 traceback 대신 안내 문구로 처리
        logger.exception("이미지 처리 중 예상치 못한 오류")
        raise AnalysisError(UNREADABLE_IMAGE_MESSAGE) from exc
    return buffer.getvalue()


def get_processed_image(image_bytes: bytes, image_hash: str) -> tuple[bytes | None, str | None]:
    """업로드 이미지를 전처리한다. 같은 이미지는 세션에 보관한 결과를 재사용한다.

    반환값: (전처리된 JPEG 바이트, 오류 메시지) 중 하나만 값이 있다.
    """
    cached = st.session_state.get("processed")
    if cached and cached[0] == image_hash:
        return cached[1], cached[2]
    try:
        processed, error = preprocess_image(image_bytes), None
    except AnalysisError as exc:
        processed, error = None, str(exc)
    st.session_state["processed"] = (image_hash, processed, error)
    return processed, error


# ---------------------------------------------------------------------------
# OpenAI 분석
# ---------------------------------------------------------------------------
def is_valid_reading(content: str) -> bool:
    """정해진 형식의 관상 풀이이거나, 약속된 재업로드 안내 문구인지 확인한다."""
    return content.startswith((NO_FACE_MESSAGE, MULTI_FACE_MESSAGE)) or REQUIRED_SECTION in content


def strip_preamble(content: str) -> str:
    """풀이 앞에 붙은 사과·서두 문장("죄송하지만 ...")을 첫 Markdown 제목 앞에서 잘라낸다."""
    match = re.search(r"^\s*#{1,6}\s", content, re.MULTILINE)
    return content[match.start():].strip() if match else content


@st.cache_data(show_spinner=False, max_entries=100, ttl=60 * 60)
def analyze_face(image_b64: str, model: str, _api_key: str) -> str:
    """OpenAI 비전 모델로 관상을 분석해 Markdown 텍스트를 반환한다.

    모델이 형식을 벗어난 거절("누구인지 알 수 없다" 등)을 하면 한 번 더 요청하고,
    그래도 안 되면 AnalysisError를 낸다. 같은 이미지·모델 조합은 캐시된 결과를 재사용한다
    (_api_key는 캐시 키에서 제외). 예외가 발생하면 캐시되지 않는다.
    """
    client = OpenAI(api_key=_api_key, timeout=REQUEST_TIMEOUT_SEC, max_retries=1)
    content = ""
    for attempt, user_text in enumerate((USER_PROMPT, RETRY_PROMPT), start=1):
        content = _request_reading(client, model, image_b64, user_text)
        if content and is_valid_reading(content):
            return strip_preamble(content)
        logger.warning("형식을 벗어난 응답 (시도 %d): %s", attempt, content[:200])
    raise AnalysisError(
        "AI가 이 사진의 관상 풀이를 거절했습니다. 얼굴이 정면으로 크게 나온 다른 사진으로 시도해주세요."
    )


def _request_reading(client: OpenAI, model: str, image_b64: str, user_text: str) -> str:
    """OpenAI에 1회 요청해 응답 텍스트를 반환한다. 거절·빈 응답이면 빈 문자열을 반환한다."""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"},
                        },
                    ],
                },
            ],
            max_completion_tokens=MAX_COMPLETION_TOKENS,
        )
    except openai.AuthenticationError as exc:
        logger.error("OpenAI 인증 실패: %s", exc)
        raise AnalysisError("API 키가 올바르지 않습니다. `.env` 또는 Secrets의 OPENAI_API_KEY를 확인하세요.") from exc
    except openai.PermissionDeniedError as exc:
        logger.error("OpenAI 권한 오류: %s", exc)
        raise AnalysisError("이 API 키로는 선택한 모델을 사용할 수 없습니다. 모델 설정을 확인하세요.") from exc
    except openai.NotFoundError as exc:
        logger.error("OpenAI 모델 없음: %s", exc)
        raise AnalysisError(f"'{model}' 모델을 찾을 수 없습니다. OPENAI_MODEL 설정을 확인하세요.") from exc
    except openai.RateLimitError as exc:
        logger.error("OpenAI 요청 한도 초과: %s", exc)
        raise AnalysisError("요청이 많거나 사용 한도를 초과했습니다. 잠시 후 다시 시도해주세요.") from exc
    except (openai.APITimeoutError, openai.APIConnectionError) as exc:
        logger.error("OpenAI 연결 오류: %s", exc)
        raise AnalysisError("AI 서버에 연결하지 못했습니다. 네트워크 상태를 확인하고 다시 시도해주세요.") from exc
    except openai.BadRequestError as exc:
        logger.error("OpenAI 잘못된 요청: %s", exc)
        raise AnalysisError("이 사진은 분석할 수 없습니다. 다른 사진으로 다시 시도해주세요.") from exc
    except openai.OpenAIError as exc:
        logger.error("OpenAI 오류: %s", exc)
        raise AnalysisError("분석 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.") from exc

    if not response.choices:
        logger.warning("응답에 choices가 없음")
        return ""
    message = response.choices[0].message
    if getattr(message, "refusal", None):
        logger.warning("모델이 분석을 거절함: %s", message.refusal)
        return ""
    content = (message.content or "").strip()
    if not content:
        logger.warning("빈 응답 (finish_reason=%s)", response.choices[0].finish_reason)
    return content


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
# Streamlit 내부 클래스(st-emotion-cache-*)는 쓰지 않고, 직접 만든 클래스와
# st.container(key=...)가 붙여 주는 .st-key-* 클래스만 사용한다.
# 색은 금색 포인트 + 반투명/currentColor 위주로 써서 라이트·다크 모드 모두에서 읽히게 한다.
CUSTOM_CSS = """
<style>
.gw-hero { text-align: center; padding: 1.5rem 0 0.5rem; }
.gw-hero .gw-seal {
    font-size: 2.75rem; line-height: 1; display: inline-block;
    padding: 0.6rem; border-radius: 50%;
    border: 2px solid rgba(184, 134, 43, 0.55);
    background: rgba(184, 134, 43, 0.10);
}
.gw-hero h1 {
    font-family: "Gowun Batang", serif; font-weight: 700;
    font-size: clamp(2rem, 7vw, 2.75rem); margin: 0.6rem 0 0.2rem; padding: 0;
    letter-spacing: 0.05em;
}
.gw-hero .gw-sub { font-size: 1.05rem; opacity: 0.8; margin: 0 0 0.9rem; }
.gw-badge {
    display: inline-block; font-size: 0.85rem; padding: 0.3rem 0.85rem;
    border-radius: 999px; border: 1px solid rgba(184, 134, 43, 0.55);
    background: rgba(184, 134, 43, 0.12);
}
.gw-divider {
    height: 1px; margin: 1.2rem auto 1.4rem; width: 60%;
    background: linear-gradient(90deg, transparent, rgba(184, 134, 43, 0.7), transparent);
}
.gw-empty { text-align: center; padding: 0.4rem 0.2rem; }
.gw-empty .gw-empty-icon { font-size: 2.2rem; }
.gw-empty p { margin: 0.4rem 0; opacity: 0.85; }
.gw-empty ul { display: inline-block; text-align: left; margin: 0.4rem auto 0; opacity: 0.85; }
.gw-chips { display: flex; flex-wrap: wrap; gap: 0.5rem; margin: 0.2rem 0 1rem; }
.gw-chip {
    padding: 0.3rem 0.8rem; border-radius: 999px; font-size: 0.95rem; font-weight: 500;
    border: 1px solid rgba(184, 134, 43, 0.6); background: rgba(184, 134, 43, 0.14);
}
.st-key-result_card { border-color: rgba(184, 134, 43, 0.55) !important; }
.st-key-result_card h2 { font-size: clamp(1.6rem, 6vw, 2.1rem); }
.st-key-result_card h3 { font-family: "Gowun Batang", serif; font-size: clamp(1.25rem, 5vw, 1.5rem); }
.st-key-result_card td:nth-child(-n+2), .st-key-result_card th:nth-child(-n+2) { white-space: nowrap; }
.st-key-preview [data-testid="stImage"] img { border-radius: 0.75rem; }
</style>
"""


def render_header() -> None:
    st.html(CUSTOM_CSS)
    st.html(
        f"""
        <div class="gw-hero">
            <div class="gw-seal">🔮</div>
            <h1>AI 관상가</h1>
            <p class="gw-sub">사진 한 장으로 보는 나의 관상</p>
            <span class="gw-badge">{html.escape(DISCLAIMER)}</span>
        </div>
        <div class="gw-divider"></div>
        """
    )


def render_sidebar() -> None:
    with st.sidebar:
        st.markdown("### 📖 사용 방법")
        st.markdown(
            "1. **사진 업로드** 또는 **카메라 촬영** 탭에서 얼굴 사진을 준비하세요.\n"
            "2. **🔍 관상 보기** 버튼을 누르세요.\n"
            "3. 풀이 결과를 읽고, 필요하면 다운로드하세요."
        )
        st.divider()
        st.markdown("### 📸 좋은 사진 촬영 팁")
        st.markdown(
            "- 🙂 카메라를 **정면**으로 바라보기\n"
            "- 💡 **밝은 조명**에서 촬영하기\n"
            "- 🧢 모자·🕶️ 선글라스·마스크 벗기\n"
            "- 👤 **한 사람만** 나오게 하기"
        )
        st.divider()
        st.markdown("### 🔒 개인정보 안내")
        st.caption(
            "업로드한 사진은 서버에 파일로 저장하지 않습니다. 사진은 이용하는 동안에만 메모리에 머물며, "
            "사진을 지우거나 창을 닫으면 함께 사라집니다. 분석을 위해 사진이 OpenAI API로 전송됩니다."
        )


def render_empty_state() -> None:
    """사진을 올리기 전 안내 화면."""
    with st.container(border=True):
        st.html(
            """
            <div class="gw-empty">
                <div class="gw-empty-icon">🧧</div>
                <p><b>얼굴이 잘 보이는 사진 한 장</b>을 올려주세요.</p>
                <p>AI 관상가가 이런 내용을 풀이해 드려요.</p>
                <ul>
                    <li>🧭 얼굴형과 전체 인상</li>
                    <li>👁️ 이마·눈·코·입·귀 부위별 풀이</li>
                    <li>🔮 재물·애정·직업·건강운 별점</li>
                    <li>🏷️ 성격 키워드와 💬 한마디 조언</li>
                </ul>
            </div>
            """
        )


def get_input_image() -> bytes | None:
    """활성 탭(업로드/카메라)의 이미지 바이트를 반환한다.

    업로더는 항상 렌더링해서 탭을 오가도 올린 파일이 유지되게 하고,
    카메라는 탭을 연 경우에만 렌더링해서 그 전에는 웹캠 권한을 요청하지 않는다.
    """
    upload_tab, camera_tab = st.tabs(["📁 사진 업로드", "📷 카메라 촬영"], key="input_tab", on_change="rerun")
    with upload_tab:
        uploaded = st.file_uploader("얼굴 사진 업로드 (JPG, PNG, WEBP · 최대 10MB)", type=ALLOWED_TYPES, key="uploader")
    if camera_tab.open:
        with camera_tab:
            source = st.camera_input("정면을 바라보고 촬영하세요", key="camera")
    else:
        source = uploaded
    return source.getvalue() if source is not None else None


def store_result(image_hash: str, result: str) -> None:
    """사진별 결과를 세션에 보관한다(오래된 것부터 정리)."""
    results = st.session_state.setdefault("results", {})
    results[image_hash] = result
    while len(results) > MAX_RESULTS_KEPT:
        results.pop(next(iter(results)))


STAR_PATTERN = re.compile(r"[★☆]{2,}")
HASHTAG_PATTERN = re.compile(r"#([^\s#]+)")
HEADING_PATTERN = re.compile(r"^\s*#{1,6}\s")  # Markdown 제목 (해시태그 '#키워드'와 구분)
KEYWORD_HEADING = "성격 키워드"


def highlight_stars(markdown_text: str) -> str:
    """별점(★☆)을 Streamlit 색상 문법으로 금색 강조한다."""
    return STAR_PATTERN.sub(lambda m: f":orange[{m.group(0)}]", markdown_text)


def split_keyword_section(result: str) -> tuple[str, list[str], str]:
    """결과를 (키워드 섹션 이전, 키워드 목록, 이후)로 나눈다. 못 찾으면 키워드는 빈 목록."""
    lines = result.splitlines()
    for i, line in enumerate(lines):
        if HEADING_PATTERN.match(line) and KEYWORD_HEADING in line:
            end = next((j for j in range(i + 1, len(lines)) if HEADING_PATTERN.match(lines[j])), len(lines))
            body = "\n".join(lines[i + 1 : end])
            tags = HASHTAG_PATTERN.findall(body)
            if not tags:
                break
            return "\n".join(lines[: i + 1]), tags, "\n".join(lines[end:])
    return result, [], ""


def render_result(result: str) -> None:
    st.html('<div class="gw-divider"></div>')
    if result.startswith((NO_FACE_MESSAGE, MULTI_FACE_MESSAGE)):
        st.warning(result, icon="📷")
        return

    with st.container(border=True, key="result_card"):
        st.header("📜 관상 풀이 결과", anchor=False)
        before, tags, after = split_keyword_section(result)
        st.markdown(highlight_stars(before))
        if tags:
            chips = "".join(f'<span class="gw-chip">#{html.escape(tag)}</span>' for tag in tags)
            st.html(f'<div class="gw-chips">{chips}</div>')
        if after.strip():
            st.markdown(highlight_stars(after))
        st.caption(DISCLAIMER)

    st.download_button(
        "📥 결과 다운로드 (.md)",
        data=f"# AI 관상 풀이 결과\n\n{result}\n\n---\n{DISCLAIMER}\n",
        file_name="관상결과.md",
        mime="text/markdown",
        on_click="ignore",
        width="stretch",
    )


def main() -> None:
    st.set_page_config(page_title="AI 관상가", page_icon="🔮", layout="centered")

    render_header()
    render_sidebar()

    api_key = get_api_key()
    model = get_model()

    image_bytes = get_input_image()
    image_hash = hashlib.sha256(image_bytes).hexdigest() if image_bytes else None
    results: dict[str, str] = st.session_state.setdefault("results", {})

    processed = None
    if image_bytes is None:
        render_empty_state()
    elif len(image_bytes) > MAX_UPLOAD_BYTES:
        st.warning("파일 용량이 10MB를 넘습니다. 더 작은 사진을 올려주세요.")
    else:
        processed, error = get_processed_image(image_bytes, image_hash)
        if error:
            st.error(error)
        else:
            with st.container(key="preview", horizontal_alignment="center"):
                st.image(processed, caption="분석에 사용할 얼굴 영역", width=350)

    if st.button("🔍 관상 보기", type="primary", disabled=processed is None, width="stretch"):
        if image_hash not in results:
            limit_message = reserve_analysis()
            if limit_message:
                st.warning(limit_message)
            else:
                try:
                    with st.spinner("관상을 살펴보는 중..."):
                        image_b64 = base64.b64encode(processed).decode("ascii")
                        store_result(image_hash, analyze_face(image_b64, model, api_key))
                except AnalysisError as exc:
                    release_analysis()
                    st.error(str(exc))

    st.caption(f"남은 분석 횟수: {remaining_analyses()}회", text_alignment="center")

    if image_hash in results:
        render_result(results[image_hash])


if __name__ == "__main__":
    main()
