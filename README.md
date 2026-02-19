# 주식 뉴스 자동 수집 봇 v2.0

한국 주요 경제지 RSS를 10분마다 자동 수집 → Gemini AI 분석 → **Google Sheets** 저장 → 텔레그램 전송.
텔레그램에서 **👍/👎 피드백**을 누르면 Gemini 분석 품질이 자동으로 향상됩니다.

## 주요 기능

| 기능 | 설명 |
|------|------|
| RSS 자동 수집 | 한국경제, 매일경제, 연합뉴스, 머니투데이, 이데일리, 인베스팅닷컴 |
| AI 분석 | Gemini 1.5 Flash → 심리(긍정/부정/중립), 관련종목, 테마섹터, 핵심요약 |
| Google Sheets 저장 | 두 PC + 폰에서 실시간 동일 데이터 확인 |
| 텔레그램 전송 | 기사별 분석 + 👍/👎 피드백 버튼 |
| 자동 품질 향상 | 피드백이 쌓일수록 Gemini 프롬프트 자동 개선 (Level 2) |
| 중복 필터링 | seen_articles.json으로 이미 수집한 기사 자동 제외 |

---

## 설치 방법

### 1. 저장소 클론

```bash
git clone https://github.com/<your-username>/stock-bot.git
cd stock-bot
```

### 2. 가상 환경 생성 (권장)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Mac/Linux
source venv/bin/activate
```

### 3. 패키지 설치

```bash
pip install -r requirements.txt
```

---

## Google Sheets 설정 (처음 한 번만)

> 이 설정이 가장 복잡하지만 한 번만 하면 됩니다. 순서대로 따라하세요.

### Step 1. Google Cloud 프로젝트 생성

1. [Google Cloud Console](https://console.cloud.google.com/) 접속 → 로그인
2. 상단 프로젝트 선택 → **새 프로젝트** → 이름 입력 (예: `stock-news-bot`) → 만들기

### Step 2. Google Sheets API 활성화

1. 왼쪽 메뉴 → **API 및 서비스** → **라이브러리**
2. `Google Sheets API` 검색 → 클릭 → **사용 설정**
3. 같은 방법으로 `Google Drive API` 도 **사용 설정**

### Step 3. 서비스 계정 생성 및 키 다운로드

1. 왼쪽 메뉴 → **API 및 서비스** → **사용자 인증 정보**
2. 상단 **+ 사용자 인증 정보 만들기** → **서비스 계정**
3. 서비스 계정 이름 입력 (예: `stock-bot`) → **만들고 계속하기** → **완료**
4. 방금 만든 서비스 계정 클릭 → **키** 탭 → **키 추가** → **새 키 만들기**
5. JSON 선택 → **만들기** → 파일이 자동 다운로드됨
6. 다운로드된 JSON 파일을 **프로젝트 폴더**에 복사하고 이름을 `google-credentials.json` 으로 변경

### Step 4. 구글 스프레드시트 생성 및 권한 부여

1. [Google Sheets](https://sheets.google.com) 에서 새 스프레드시트 생성
2. URL에서 시트 ID 복사:
   ```
   https://docs.google.com/spreadsheets/d/[이 부분이 GOOGLE_SHEET_ID]/edit
   ```
3. 스프레드시트 우상단 **공유** 버튼 클릭
4. `google-credentials.json` 파일 안의 `"client_email"` 값을 복사해서 공유 대상으로 추가
5. 권한: **편집자** → **완료**

---

## .env 설정

### 1. `.env.example` 복사

```bash
# Windows
copy .env.example .env

# Mac/Linux
cp .env.example .env
```

### 2. `.env` 파일 편집

```env
GEMINI_API_KEY=발급받은_Gemini_API_키
TELEGRAM_BOT_TOKEN=텔레그램_봇_토큰
TELEGRAM_CHAT_ID=텔레그램_채팅_ID
GOOGLE_SHEET_ID=구글_시트_ID
GOOGLE_CREDENTIALS_FILE=google-credentials.json
```

### API 키 발급처 요약

| 항목 | 발급처 |
|------|-------|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/app/apikey) → Create API Key |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 → @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | 봇과 대화 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서 `chat.id` 확인 |
| `GOOGLE_SHEET_ID` | 구글 시트 URL 중간 부분 |

---

## 실행 방법

```bash
python stock_news_bot.py
```

- 실행 즉시 첫 수집 시작, 이후 **10분 간격** 자동 반복
- 텔레그램으로 기사별 분석 전송 + **👍/👎 버튼** 표시
- 👍 누르면 → 좋은 분석 예시로 저장, 다음 Gemini 분석에 자동 반영
- 👎 누르면 → 나쁜 예시로 저장, Gemini가 같은 실수 반복 안 함
- 종료: `Ctrl + C`

---

## 새 PC에서 세팅하는 방법

```bash
# 1. 클론
git clone https://github.com/<your-username>/stock-bot.git
cd stock-bot

# 2. 패키지 설치
pip install -r requirements.txt

# 3. .env 생성 및 API 키 입력
copy .env.example .env   # Windows

# 4. google-credentials.json 파일을 프로젝트 폴더에 복사
#    (GitHub에 없으므로 직접 복사해야 함)

# 5. 실행
python stock_news_bot.py
```

> **핵심**: Google Sheets를 쓰기 때문에 어느 PC에서 실행해도 같은 시트에 데이터가 쌓입니다.
> `seen_articles.json`과 `feedback_store.json`은 PC마다 별도 관리됩니다.

---

## 파일 구조

```
stock-bot/
├── stock_news_bot.py         # 메인 봇 코드
├── .env.example              # 환경 변수 템플릿 (GitHub 공개)
├── .env                      # 실제 API 키 (로컬 전용, gitignore)
├── google-credentials.json   # Google 서비스 계정 키 (로컬 전용, gitignore)
├── .gitignore
├── requirements.txt
├── README.md
├── seen_articles.json        # 중복 필터 (자동 생성, 로컬 전용)
├── feedback_store.json       # 피드백 누적 데이터 (자동 생성, 로컬 전용)
└── bot.log                   # 실행 로그 (자동 생성)
```

---

## 주의 사항

- `.env`와 `google-credentials.json`은 **절대 GitHub에 올리지 마세요**. `.gitignore`로 차단되어 있습니다.
- Gemini API 무료 티어는 분당 15회 제한. 기사 간 1초 딜레이 적용됩니다.
- 피드백(`feedback_store.json`)은 PC마다 별도 저장됩니다. 더 정확한 분석을 원하면 한 PC에서 꾸준히 피드백을 누르세요.
