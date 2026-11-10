# 🔮 AI 관상가

얼굴 사진을 업로드하면 OpenAI 이미지 분석 모델이 관상을 풀이해 주는 Streamlit 앱입니다.
재미로 보는 결과이며 과학적 근거가 없습니다.

## 로컬 실행

1. 패키지 설치 (Python 3.10 이상)
   ```bash
   pip install -r requirements.txt
   ```
2. `.env.example`을 복사해 `.env`를 만들고 API 키 입력
   ```env
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-4o
   MAX_ANALYSES_PER_SESSION=10
   MAX_ANALYSES_PER_DAY=200
   ```
3. 실행
   ```bash
   streamlit run app.py
   ```

> `.env`는 `.gitignore`에 포함되어 GitHub에 올라가지 않습니다.

## 설정값

| 이름 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `OPENAI_API_KEY` | ✅ | - | OpenAI API 키 |
| `OPENAI_MODEL` | | `gpt-4o` | 이미지 분석을 지원하는 모델 |
| `MAX_ANALYSES_PER_SESSION` | | `10` | 접속 1회(브라우저 세션)당 분석 가능 횟수 |
| `MAX_ANALYSES_PER_DAY` | | `200` | 앱 전체의 하루 분석 가능 횟수 (앱이 재시작되면 초기화) |

## Streamlit Cloud 배포

1. GitHub에 저장소를 만들고 코드를 올립니다. 올리기 전에 `git status`로 `.env`가 목록에 없는지 확인하세요.
2. [share.streamlit.io](https://share.streamlit.io)에서 GitHub 계정을 연동하고 **Create app**을 누릅니다.
3. 저장소, 브랜치(`main`), 메인 파일(`app.py`)을 선택합니다.
4. **Advanced settings → Secrets**에 아래 내용을 TOML 형식으로 입력합니다.
   ```toml
   OPENAI_API_KEY = "sk-..."
   OPENAI_MODEL = "gpt-4o"
   MAX_ANALYSES_PER_SESSION = 10
   MAX_ANALYSES_PER_DAY = 200
   ```
5. **Deploy**를 누르고, 발급된 URL에서 동작을 확인합니다.

> 앱의 사용 횟수 제한은 기본적인 보호 장치입니다. OpenAI 대시보드의 **Limits**에서 월 사용 한도도 함께 설정하는 것을 권장합니다.
