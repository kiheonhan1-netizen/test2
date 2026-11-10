# AI 관상가 – 단계별 개발 프롬프트

> 각 단계는 앞 단계가 끝난 뒤 순서대로 실행하세요.
> 모든 프롬프트는 같은 폴더의 `prd.md`를 기준으로 합니다.

---

## 1단계: 기본 뼈대 작성

```
prd.md를 먼저 읽고, PRD의 "6.3 프로젝트 구조"에 맞춰 Streamlit 앱의 기본 뼈대를 만들어줘.

[만들 파일]
1. app.py
   - st.set_page_config (제목 "AI 관상가", 아이콘 🔮, layout="centered")
   - 제목, 부제목, 면책 고지 문구("재미로 보는 결과이며 과학적 근거가 없습니다")
   - 사이드바: 사용 방법, 촬영 팁, 개인정보 안내(이미지는 저장하지 않음) 자리만 만들기
   - 이미지 업로드 영역, "관상 보기" 버튼, 결과 영역은 자리(placeholder)만 만들고 실제 분석은 아직 하지 않기
   - API 키 로딩 함수 get_api_key(): st.secrets → .env(os.getenv) 순서로 찾고,
     둘 다 없으면 st.error로 안내 후 st.stop()
     (st.secrets 접근 시 secrets.toml이 없어도 오류가 나지 않도록 try/except 처리)
   - .env의 OPENAI_MODEL을 읽고 없으면 기본값 "gpt-4o" 사용
2. prompts.py
   - SYSTEM_PROMPT 상수만 비어 있지 않은 임시 문구로 정의 (내용은 2단계에서 채움)
3. requirements.txt: streamlit, openai, python-dotenv, Pillow
4. .env.example: OPENAI_API_KEY=, OPENAI_MODEL=gpt-4o
5. .gitignore: .env, __pycache__/, *.pyc, .streamlit/secrets.toml, .venv/, venv/
6. README.md: 로컬 실행 방법(pip install, streamlit run app.py), .env 설정 방법만 간단히

[조건]
- .env 파일 자체는 만들지 말고, 내가 직접 키를 넣을 수 있도록 안내만 해줘.
- 코드에 API 키를 절대 하드코딩하지 마.
- 다 만든 뒤 `pip install -r requirements.txt`와 `python -m py_compile app.py prompts.py`로
  문법 오류가 없는지 확인하고 결과를 알려줘.
```

---

## 2단계: 주요 기능 추가

```
prd.md의 "4. 기능 요구사항"과 "9. 예외 처리 시나리오"를 기준으로, 1단계에서 만든 뼈대에 실제 기능을 구현해줘.

[구현할 기능]
1. 이미지 입력 (F-1, F-2, F-3)
   - st.file_uploader: jpg, jpeg, png, webp만 허용
   - st.camera_input: 탭(st.tabs)으로 "사진 업로드" / "카메라 촬영" 분리
   - 10MB 초과 시 경고 후 분석 버튼 비활성화
   - 업로드한 이미지 미리보기

2. 이미지 전처리 (F-4) – 별도 함수 preprocess_image(bytes) -> str(base64)
   - Pillow ImageOps.exif_transpose로 회전 보정
   - RGBA/P 모드는 RGB로 변환
   - 긴 변 1024px 이하로 리사이즈 (비율 유지)
   - JPEG(quality=85)로 저장 후 base64 인코딩
   - 손상된 파일이면 사용자에게 한국어 오류 표시

3. 관상 분석 (F-5) – 함수 analyze_face(image_b64, model) -> str
   - openai 공식 SDK의 OpenAI 클라이언트 사용
   - Chat Completions API에 system 프롬프트 + user 메시지(텍스트 + image_url data URL) 전송
   - max_tokens 약 1500 (사용하는 모델이 max_completion_tokens만 받으면 그에 맞춰 수정)
   - 동일 이미지 재요청 비용을 줄이기 위해 st.cache_data 적용 (이미지 해시 + 모델명 기준)

4. 프롬프트 (prompts.py) – PRD 4.3, 4.4 반영
   - 역할: 수십 년 경력의 친절한 한국 관상가, 한국어 응답
   - 출력 형식: 전체 인상 / 부위별 풀이(이마, 눈썹·눈, 코, 입·턱, 귀) /
     운세 요약(재물·애정·직업·건강, ★1~5) / 성격 키워드 해시태그 3~5개 / 한마디 조언
   - 금지: 외모 비하, 인종·성별·나이 고정관념, 건강 진단, 인물 신원 추정
   - 얼굴이 없거나 여러 명이면 정해진 안내 문구로 재업로드 요청
   - 오락용 전통 관상학 콘텐츠임을 명시

5. 결과 표시 및 다운로드 (F-6, F-7, F-9, F-10)
   - 분석 중 st.spinner("관상을 살펴보는 중...")
   - 결과는 st.markdown으로 출력, 결과를 st.session_state에 저장해 재실행 시에도 유지
   - 결과 하단에 면책 고지
   - st.download_button으로 결과를 "관상결과.md"로 다운로드

6. 예외 처리 (F-8, PRD 9장)
   - openai의 AuthenticationError, RateLimitError, APIConnectionError, APIError 등을 구분해 한국어 메시지 표시
   - 상세 오류는 화면에 노출하지 말고 logging으로만 기록
   - 모델이 거절하거나 빈 응답을 주면 안내 문구 표시

[조건]
- 업로드 이미지는 디스크에 저장하지 말고 메모리에서만 처리해.
- 함수 단위로 나눠서 읽기 쉽게 작성하고, 핵심 함수에는 짧은 docstring을 달아줘.
- 구현 후 py_compile로 문법 검사를 하고, 가능하면 `streamlit run app.py`로 앱이 오류 없이 뜨는지 확인해줘.
```

---

## 3단계: UI 디자인

```
prd.md의 "7. 화면 구성"을 기준으로 앱의 UI를 다듬어줘. 기능 로직은 바꾸지 말고 화면만 개선해.

[디자인 방향]
- 콘셉트: 동양적이고 신비로운 "관상가의 방" 느낌 (먹색·한지색·금색 계열 포인트)
- 모바일에서도 잘 보이도록 한 열(centered) 레이아웃 유지

[작업 내용]
1. .streamlit/config.toml 생성: primaryColor, backgroundColor, secondaryBackgroundColor, textColor, font 지정
2. st.markdown(unsafe_allow_html=True)로 최소한의 CSS 추가
   - 헤더: 큰 제목 + 부제목 + 면책 고지를 배지/캡션 형태로
   - 결과 영역: 카드 형태(둥근 모서리, 은은한 테두리, 여백)
   - "관상 보기" 버튼: 가로 전체 너비, 강조 색상
   - Streamlit 내부 클래스명(st-emotion-cache-*)에 의존하는 CSS는 쓰지 말고,
     data-testid나 직접 감싼 div 클래스만 사용
3. 미리보기 이미지는 적당한 크기(최대 너비 약 350px)로 가운데 정렬
4. 결과 표시 개선
   - 운세 요약 별점이 잘 보이도록 구성
   - 성격 키워드는 태그(칩) 모양으로 보이면 좋음 (어렵다면 Markdown 유지)
5. 사이드바: 사용 방법(1-2-3 단계), 좋은 사진 촬영 팁(정면, 밝은 조명, 모자·선글라스 X), 개인정보 안내를 정리
6. 빈 상태(사진 업로드 전)에는 안내 문구와 예시 설명 표시
7. 라이트/다크 모드 모두에서 글자가 잘 보이는지 확인

[조건]
- 외부 이미지·폰트 CDN은 꼭 필요할 때만 쓰고, 쓴다면 Google Fonts 정도로 제한해.
- 수정 후 `streamlit run app.py`로 실행해서 화면이 깨지지 않는지 확인하고, 바뀐 점을 요약해줘.
```

---

## 4단계: PRD 기준 검토 및 버그 점검 (Playwright 활용)

```
지금까지 만든 앱이 prd.md대로 개발되었는지, 문제나 버그가 없는지 자세하게 검토해줘.
코드 리뷰와 Playwright를 이용한 실제 브라우저 테스트를 모두 해줘.

[1. 정적 검토 – 코드 리뷰]
- prd.md의 기능 요구사항(F-1 ~ F-10), 비기능 요구사항(5장), 예외 처리(9장)를 항목별로
  "구현됨 / 부분 구현 / 미구현"으로 체크리스트 표를 만들어줘.
- 보안: API 키 하드코딩 여부, .gitignore에 .env 포함 여부, 오류 메시지에 키나 상세 예외가 노출되는지
- 개인정보: 이미지가 디스크에 저장되는 코드가 없는지
- Streamlit Cloud 배포 호환성: secrets.toml이 없을 때 st.secrets 접근 오류, requirements.txt 누락 패키지,
  파일 경로/인코딩 문제(한글 폴더명 포함)
- st.cache_data, st.session_state 사용이 올바른지 (다른 사진을 올렸는데 이전 결과가 남는 문제 등)
- 이미지 전처리 예외: PNG 투명 배경, 회전된 사진, 아주 작은/큰 이미지, 손상된 파일
- OpenAI API 호출 파라미터가 현재 SDK·모델과 맞는지

[2. 동적 검토 – Playwright 브라우저 테스트]
- `streamlit run app.py --server.headless true --server.port 8501`을 백그라운드로 실행
- 테스트용 이미지는 Pillow로 임시 폴더에 생성해서 사용 (실제 인물 사진은 쓰지 말 것):
  ① 일반 JPG, ② 투명 PNG, ③ 10MB 초과 파일, ④ 손상된(확장자만 jpg인) 파일, ⑤ txt 파일
- Playwright로 다음 시나리오를 확인하고 각 단계마다 스크린샷을 남겨줘:
  a. 첫 화면: 제목, 면책 고지, 사이드바, 업로더가 보이는지
  b. 지원 형식 업로드 → 미리보기 표시
  c. 10MB 초과 / 손상 파일 / 지원하지 않는 형식 → 적절한 오류 메시지
  d. "관상 보기" 클릭 → 스피너 → 결과 표시 → 다운로드 버튼
     (API 키가 없으면 키 없음 안내가 제대로 뜨는지 확인. API 비용이 드는 실제 호출은
      한 번만 하거나, 내가 허락할 때만 해줘)
  e. 다른 사진으로 교체했을 때 이전 결과가 올바르게 초기화되는지
  f. 모바일 화면 크기(390x844)와 데스크톱(1280x800)에서 레이아웃이 깨지지 않는지
  g. 브라우저 콘솔 오류, Streamlit 서버 로그의 경고·예외
- 테스트가 끝나면 Streamlit 서버를 종료해줘.

[3. 결과 보고]
- PRD 체크리스트 표
- 발견한 문제를 심각도(높음/중간/낮음)별로 정리: 위치(파일:줄), 재현 방법, 원인, 수정 제안
- 바로 고칠 수 있는 버그는 수정 전 목록을 먼저 보여주고, 내가 승인하면 수정한 뒤 해당 테스트를 다시 돌려줘.
- 배포 전에 내가 직접 해야 할 일(Secrets 설정 등)을 마지막에 정리해줘.
```
