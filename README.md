# 주식 뉴스 자동 수집 봇

한국 주요 경제지 RSS를 10분마다 자동 수집하여 Gemini AI로 분석 후
엑셀 파일로 저장하고 텔레그램으로 전송하는 봇입니다.

## 주요 기능

- **RSS 자동 수집**: 한국경제, 매일경제, 연합뉴스, 머니투데이, 이데일리, 인베스팅닷컴
- **AI 분석 (Gemini 1.5 Flash)**: 심리(긍정/부정/중립), 관련종목, 테마섹터, 핵심요약
- **엑셀 저장**: 컬러 코딩된 스타일로 월별 파일 자동 생성
- **텔레그램 전송**: 분석 요약 메시지 자동 발송
- **중복 필터링**: 이미 수집한 기사 자동 제외 (seen_articles.json)
- **10분 주기 자동 반복**

## 엑셀 컬럼 구조

| No. | 시간 | 채널(출처) | 심리 | 관련종목 | 테마섹터 | 핵심요약 | 원본링크 |
|-----|------|-----------|------|---------|---------|---------|---------|

심리 컬러: 🟢 긍정(초록), 🔴 부정(빨강), 🟡 중립(노랑)

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

# macOS / Linux
source venv/bin/activate
```

### 3. 패키지 설치

```bash
pip install -r requirements.txt
```

---

## .env 설정 방법

### 1. `.env.example` 복사

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

### 2. `.env` 파일 편집

```
GEMINI_API_KEY=발급받은_Gemini_API_키
TELEGRAM_BOT_TOKEN=텔레그램_봇_토큰
TELEGRAM_CHAT_ID=텔레그램_채팅_ID
```

### API 키 발급 방법

| 항목 | 발급처 |
|------|-------|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/app/apikey) → Create API Key |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 → @BotFather → `/newbot` 명령어 |
| `TELEGRAM_CHAT_ID` | 봇과 대화 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서 `chat.id` 확인 |

---

## 실행 방법

```bash
python stock_news_bot.py
```

- 실행 즉시 첫 수집이 시작되며, 이후 10분 간격으로 자동 반복됩니다.
- 종료: `Ctrl + C`
- 로그는 `bot.log` 파일에도 기록됩니다.

---

## 새 PC에서 세팅하는 방법

1. Python 3.10 이상 설치 확인
   ```bash
   python --version
   ```

2. 저장소 클론 및 패키지 설치
   ```bash
   git clone https://github.com/<your-username>/stock-bot.git
   cd stock-bot
   pip install -r requirements.txt
   ```

3. `.env` 파일 생성 및 API 키 입력
   ```bash
   copy .env.example .env   # Windows
   # 메모장 또는 편집기로 .env 열어서 키 입력
   ```

4. 실행
   ```bash
   python stock_news_bot.py
   ```

> **참고**: `seen_articles.json`과 `*.xlsx` 파일은 `.gitignore`에 포함되어 있어
> GitHub에 올라가지 않습니다. 각 PC에서 독립적으로 관리됩니다.

---

## 파일 구조

```
stock-bot/
├── stock_news_bot.py     # 메인 봇 코드
├── .env.example          # 환경 변수 템플릿 (GitHub에 공개)
├── .env                  # 실제 API 키 (로컬 전용, .gitignore 처리)
├── .gitignore
├── requirements.txt
├── README.md
├── seen_articles.json    # 중복 필터 (자동 생성, 로컬 전용)
├── stock_news_YYYYMM.xlsx  # 엑셀 결과 (자동 생성, 로컬 전용)
└── bot.log               # 실행 로그 (자동 생성)
```

---

## 주의 사항

- `.env` 파일은 **절대 GitHub에 올리지 마세요**. `.gitignore`로 차단되어 있습니다.
- Gemini API 무료 티어는 분당 요청 수 제한이 있습니다. 기사 분석 간 1초 딜레이가 적용됩니다.
- 텔레그램은 한 번에 최대 10건 요약 전송 후 나머지는 엑셀 확인을 안내합니다.
