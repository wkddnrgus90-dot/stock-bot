"""
봇 실행 전 설정 검사 스크립트 v2.1
버그수정: _get_client_email() 함수를 상단으로 이동
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

OK   = "  ✅"
FAIL = "  ❌"
WARN = "  ⚠️ "

errors = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = OK if ok else FAIL
    print(f"{status} {label}" + (f"\n       → {detail}" if detail else ""))
    if not ok:
        errors.append(label)
    return ok


# 버그수정: 함수를 상단으로 이동 (하단에 정의 후 상단에서 호출하던 NameError 수정)
def _get_client_email(creds_path: Path) -> str:
    try:
        with open(creds_path, "r") as f:
            return json.load(f).get("client_email", "확인 불가")
    except Exception:
        return "확인 불가"


# ──────────────────────────────────────────────
print("\n[1단계] .env 환경 변수 확인")
# ──────────────────────────────────────────────

GEMINI_API_KEY          = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID        = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_SHEET_ID         = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google-credentials.json")

check("GEMINI_API_KEY",     bool(GEMINI_API_KEY),     ".env 파일에 입력 필요")
check("TELEGRAM_BOT_TOKEN", bool(TELEGRAM_BOT_TOKEN), ".env 파일에 입력 필요 (@BotFather에서 발급)")
check("TELEGRAM_CHAT_ID",   bool(TELEGRAM_CHAT_ID),   ".env 파일에 입력 필요")
check("GOOGLE_SHEET_ID",    bool(GOOGLE_SHEET_ID),    ".env 파일에 입력 필요 (구글 시트 URL 중간 부분)")
check(
    "google-credentials.json",
    Path(GOOGLE_CREDENTIALS_FILE).exists(),
    f"{GOOGLE_CREDENTIALS_FILE} 파일이 프로젝트 폴더에 없음 (Google Cloud에서 다운로드 필요)",
)

# ──────────────────────────────────────────────
print("\n[2단계] Gemini API 연결 테스트")
# ──────────────────────────────────────────────

if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model    = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content("안녕하세요. 테스트입니다. '연결 성공'이라고만 답해주세요.")
        check("Gemini API 연결", True, f"응답: {response.text.strip()[:50]}")
    except Exception as e:
        check("Gemini API 연결", False, str(e))
else:
    print(f"{WARN} Gemini API 키 미설정 — 건너뜀")

# ──────────────────────────────────────────────
print("\n[3단계] 텔레그램 봇 연결 테스트")
# ──────────────────────────────────────────────

if TELEGRAM_BOT_TOKEN:
    try:
        import requests
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
            timeout=10,
        )
        if resp.ok:
            bot_name = resp.json()["result"]["username"]
            check("Telegram 봇 연결", True, f"봇 이름: @{bot_name}")
        else:
            check("Telegram 봇 연결", False, f"응답 오류: {resp.status_code} — 토큰 확인 필요")
    except Exception as e:
        check("Telegram 봇 연결", False, str(e))
else:
    print(f"{WARN} 텔레그램 토큰 미설정 — 건너뜀")

# ──────────────────────────────────────────────
print("\n[4단계] Google Sheets 연결 테스트")
# ──────────────────────────────────────────────

creds_path = Path(GOOGLE_CREDENTIALS_FILE)
if creds_path.exists() and GOOGLE_SHEET_ID:
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        SCOPES = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
        client = gspread.authorize(creds)
        sheet  = client.open_by_key(GOOGLE_SHEET_ID)
        check("Google Sheets 연결", True, f"시트 제목: {sheet.title}")
    except gspread.exceptions.APIError as e:
        if "403" in str(e):
            check(
                "Google Sheets 연결",
                False,
                "권한 없음 — 구글 시트를 서비스 계정 이메일에 '편집자'로 공유했는지 확인하세요.\n"
                f"       서비스 계정 이메일: {_get_client_email(creds_path)}",
            )
        else:
            check("Google Sheets 연결", False, str(e))
    except Exception as e:
        check("Google Sheets 연결", False, str(e))
elif not creds_path.exists():
    print(f"{WARN} google-credentials.json 없음 — 건너뜀")
else:
    print(f"{WARN} GOOGLE_SHEET_ID 미설정 — 건너뜀")


# ──────────────────────────────────────────────
print("\n" + "=" * 45)
if not errors:
    print("🎉 모든 항목 통과! 이제 봇을 실행할 수 있습니다.")
    print("\n실행 명령어:")
    print("  python stock_news_bot.py")
else:
    print(f"⚠️  {len(errors)}개 항목 해결 필요:")
    for e in errors:
        print(f"   - {e}")
    print("\nREADME.md를 참고하거나 Claude에게 막힌 부분을 알려주세요.")
print("=" * 45 + "\n")
