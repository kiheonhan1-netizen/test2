# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

AI 관상가 — a Streamlit app that sends an uploaded face photo to an OpenAI vision model and returns a Korean physiognomy (관상) reading for entertainment. All four stages of `dev_prompts.md` are implemented.

- `prd.md` — the source of truth for requirements (feature IDs F-1..F-10, non-functional reqs in §5, error cases in §9). Check work against it.
- `dev_prompts.md` — the four-stage build plan (1: skeleton, 2: core features, 3: UI, 4: PRD review + Playwright testing).
- `.env` — holds the real `OPENAI_API_KEY`. Do not read, print, or commit it.

## Commands

```bash
pip install -r requirements.txt        # Python 3.10+ (code uses `X | None` hints)
streamlit run app.py
python -m py_compile app.py prompts.py                          # quick syntax check
streamlit run app.py --server.headless true --server.port 8501  # for Playwright testing

# Headless smoke render (no browser, no API call). PYTHONIOENCODING is needed because
# the Windows console is cp949 and the UI text contains emoji.
PYTHONIOENCODING=utf-8 python -c "from streamlit.testing.v1 import AppTest; at = AppTest.from_file('app.py').run(); print(list(at.exception), [e.value for e in at.error])"
```

There is no test suite. Verification is done by running the app and driving it with Playwright (see stage 4 in `dev_prompts.md`).

## Architecture

- `app.py` pipeline: tabs (uploader is always rendered so it survives tab switches; `st.camera_input` is rendered only when its tab is `.open`, so the webcam isn't requested early) → `get_processed_image` runs `preprocess_image` once per image hash at upload time. This validates the image, rejects images over `MAX_IMAGE_PIXELS` before decoding, crops to the largest face (`crop_to_face`, OpenCV Haar cascade at ≤2048px working size, with margins for forehead, ears, and chin), and produces a ≤1024px JPEG. The crop is skipped when no face is found or when a second face is ≥50% the area of the largest. In that case the model decides between no face and multiple faces. `opencv-python-headless` is pinned to `<5` because 5.0 no longer ships the Haar cascade XML files. The preview and the API call both use that small JPEG, never the raw upload. Next, `reserve_analysis` checks the usage limits and `analyze_face` calls OpenAI Chat Completions (`st.cache_data`).
- State: results live in `st.session_state["results"]`, keyed by image SHA-256. So switching tabs or images and coming back restores the right result, and there is no explicit "reset on change" logic. `release_analysis` refunds a reserved use when the call fails.
- Usage limits: per-session (`analysis_count` in session_state) plus an app-wide daily counter in an `st.cache_resource` dict with a lock. The daily counter resets when the process restarts.
- Errors: everything user-facing is raised as `AnalysisError` (Korean message), and details go to `logging`. Pillow's `DecompressionBombError` is not an `OSError`, so it is caught explicitly.
- `prompts.py`: `SYSTEM_PROMPT` defines the output sections (전체 인상 / 부위별 풀이 / 운세 요약 table with ★☆ / 성격 키워드 hashtags / 조언) and the content rules. If you change the section headings or hashtag format, also update `split_keyword_section` and `highlight_stars` in `app.py`, which parse them for the chip and star rendering. `NO_FACE_MESSAGE`/`MULTI_FACE_MESSAGE` are matched by prefix to show a warning instead of a result card.
- Refusals: GPT-4o often refuses real-person photos, especially public figures ("누구인지 알 수 없다", "유명한 인물이라…"). Context such as name tags, news watermarks, and venues makes this worse, and face cropping was the change that fixed it (news photos that were refused twice passed on the first try once cropped). The prompt therefore frames the task as describing visible features through traditional 관상학 interpretations, with no identification. `analyze_face` treats any reply that lacks `REQUIRED_SECTION` (and isn't a no-face/multi-face message) as a refusal, retries once with `RETRY_PROMPT`, and otherwise raises `AnalysisError`. `strip_preamble` drops apology text before the first heading. Expect up to 2 API calls per analysis.
- Config: `st.secrets` → `.env` (`get_config`). Keys: `OPENAI_API_KEY`, `OPENAI_MODEL` (default `gpt-4o`), `MAX_ANALYSES_PER_SESSION`, `MAX_ANALYSES_PER_DAY`. `.streamlit/config.toml` holds the light/dark theme and `server.maxUploadSize = 10`.
- CSS targets only custom `gw-*` classes and `.st-key-<key>` container classes, never Streamlit's generated `st-emotion-cache-*` classes.

## Testing notes

- To check the UI without paying for API calls, run a scratch script that imports `app`, replaces `app.analyze_face` with a stub returning sample Markdown, and calls `app.main()`. Launch it with `streamlit run` from the project root so `.streamlit/config.toml` applies. Because `app.py` is then an imported module rather than the main script, Streamlit does not hot-reload it, so restart the server after editing `app.py`.
- To test usage limits, start the server with low values, e.g. `MAX_ANALYSES_PER_SESSION=2 MAX_ANALYSES_PER_DAY=3 streamlit run ...` (env vars apply when there are no Secrets). The daily counter lives in `st.cache_resource` and survives page reloads, so restart the server to reset it.
- The Playwright MCP can only write and upload files under the project root. Use `.playwright-mcp/` (gitignored).
- Streamlit scrolls inside its main container, so full-page screenshots get clipped. Enlarge the viewport or screenshot an element instead.
- `gpt-4o` treats simple drawn/cartoon faces as "no face", so the full result path needs a real photo.

## Constraints

- Deployment: GitHub → Streamlit Community Cloud. `.env` is not deployed, so secrets go in the app's Secrets settings (TOML).
- Uploaded images are processed in memory only. Never write them to disk.
- All UI text is in Korean. Every screen shows the entertainment-only disclaimer.
- Real OpenAI calls cost money. During testing, make at most one, or only with the user's approval. Use generated test images, never real people's photos.
- The project folder name is Korean (`관상`), so watch path/encoding handling on Windows.
