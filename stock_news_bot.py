"""
주식 뉴스 자동 수집 봇 v2.0
- RSS 수집 → Gemini AI 분석 → Google Sheets 저장 → 텔레그램 전송
- 텔레그램 👍/👎 피드백 버튼으로 Gemini 분석 품질 자동 향상 (Level 2)
- 중복 기사 필터링 (seen_articles.json)
- 10분 주기 자동 반복
"""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

import feedparser
import gspread
import google.generativeai as genai
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

# ──────────────────────────────────────────────
# 환경 변수 로드
# ──────────────────────────────────────────────
load_dotenv()

GEMINI_API_KEY          = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID        = os.getenv("TELEGRAM_CHAT_ID")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google-credentials.json")
GOOGLE_SHEET_ID         = os.getenv("GOOGLE_SHEET_ID")

# ──────────────────────────────────────────────
# 로깅 설정
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# RSS 피드 소스
# ──────────────────────────────────────────────
RSS_SOURCES = {
    "한국경제":    "https://www.hankyung.com/feed/economy",
    "매일경제":    "https://www.mk.co.kr/rss/30000001/",
    "연합뉴스":    "https://www.yna.co.kr/RSS/economy.xml",
    "머니투데이":  "https://rss.mt.co.kr/mt/1/rss.xml",
    "이데일리":    "https://rss.edaily.co.kr/edaily/economy_news.xml",
    "인베스팅닷컴": "https://kr.investing.com/rss/news.rss",
}

# ──────────────────────────────────────────────
# 파일 경로 / 상수
# ──────────────────────────────────────────────
SEEN_ARTICLES_FILE = Path("seen_articles.json")
FEEDBACK_FILE      = Path("feedback_store.json")

SHEET_HEADERS  = ["No.", "시간", "채널(출처)", "심리", "관련종목", "테마섹터", "핵심요약", "원본링크", "피드백"]
GSPREAD_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SENTIMENT_EMOJI = {"긍정": "🟢", "부정": "🔴", "중립": "🟡"}


# ══════════════════════════════════════════════
# 피드백 저장소
# ══════════════════════════════════════════════
def load_feedback_store() -> dict:
    """피드백 데이터 로드."""
    default = {"pending": {}, "good_examples": [], "bad_examples": []}
    if FEEDBACK_FILE.exists():
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"feedback_store.json 로드 실패, 초기화: {e}")
    return default


def save_feedback_store(store: dict) -> None:
    """피드백 데이터 저장."""
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════
# Gemini AI 분석
# ══════════════════════════════════════════════
def init_gemini() -> genai.GenerativeModel:
    """Gemini 모델 초기화."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 미설정")
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel("gemini-1.5-flash")


def build_analysis_prompt(title: str, summary: str, store: dict) -> str:
    """피드백 예시를 반영한 동적 프롬프트 생성.

    👍 누적 예시 → '이런 방식으로' 참고
    👎 누적 예시 → '이런 방식은 피해' 참고
    피드백이 쌓일수록 분석 품질이 자동으로 향상됨.
    """
    good_section = ""
    bad_section  = ""

    if store["good_examples"]:
        good_section = "\n[좋은 분석 예시 - 이런 방식으로 분석해 주세요]\n"
        for ex in store["good_examples"][-2:]:  # 최근 2개만 참조
            good_section += (
                f"제목: {ex['title']}\n"
                f"심리: {ex['analysis']['심리']} | "
                f"관련종목: {ex['analysis']['관련종목']} | "
                f"테마섹터: {ex['analysis']['테마섹터']}\n"
                f"핵심요약: {ex['analysis']['핵심요약']}\n\n"
            )

    if store["bad_examples"]:
        bad_section = "\n[나쁜 분석 예시 - 이런 방식은 피해 주세요]\n"
        for ex in store["bad_examples"][-2:]:
            bad_section += (
                f"제목: {ex['title']}\n"
                f"잘못된 요약: {ex['analysis']['핵심요약']}\n\n"
            )

    return f"""다음 한국 경제/주식 뉴스 기사를 분석해 주세요.
{good_section}{bad_section}
[분석할 기사]
제목: {title}
내용: {summary}

아래 형식으로 정확히 답변해 주세요. 각 항목은 반드시 한 줄로 작성하세요.

심리: [긍정/부정/중립 중 하나]
관련종목: [관련 종목명, 없으면 "해당없음"]
테마섹터: [관련 테마 또는 섹터명, 없으면 "기타"]
핵심요약: [2~3문장으로 핵심 내용 요약]""".strip()


def analyze_article(model: genai.GenerativeModel, article: dict, store: dict) -> dict:
    """Gemini API로 기사 분석."""
    prompt = build_analysis_prompt(
        title=article["title"],
        summary=article["summary"] or article["title"],
        store=store,
    )
    try:
        response = model.generate_content(prompt)
        return parse_gemini_response(response.text)
    except Exception as e:
        logger.error(f"Gemini 분석 오류 ({article['title'][:30]}): {e}")
        return {"심리": "분석실패", "관련종목": "-", "테마섹터": "-", "핵심요약": article["title"]}


def parse_gemini_response(text: str) -> dict:
    """Gemini 응답 텍스트 파싱."""
    result = {"심리": "중립", "관련종목": "해당없음", "테마섹터": "기타", "핵심요약": ""}
    for line in text.strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            key   = key.strip()
            value = value.strip()
            if key in result:
                result[key] = value
    return result


# ══════════════════════════════════════════════
# Google Sheets
# ══════════════════════════════════════════════
def init_gspread() -> gspread.Client:
    """Google Sheets 클라이언트 초기화."""
    creds_path = Path(GOOGLE_CREDENTIALS_FILE)
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Google 인증 파일 없음: {GOOGLE_CREDENTIALS_FILE}\n"
            "README.md의 'Google Sheets 설정' 섹션을 참고하세요."
        )
    creds = Credentials.from_service_account_file(str(creds_path), scopes=GSPREAD_SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheet(client: gspread.Client) -> gspread.Worksheet:
    """워크시트 가져오기. 없으면 헤더 포함해서 새로 생성."""
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    sheet_name  = "주식뉴스"
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=10000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS)
        # 헤더 스타일: 진한 파란 배경 + 흰 글씨 + 굵게
        ws.format("A1:I1", {
            "backgroundColor": {"red": 0.12, "green": 0.31, "blue": 0.47},
            "textFormat": {
                "bold": True,
                "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
            },
            "horizontalAlignment": "CENTER",
        })
        ws.freeze(rows=1)
        logger.info(f"새 워크시트 생성: {sheet_name}")
    return ws


def save_to_sheets(
    ws: gspread.Worksheet,
    articles_with_analysis: list[dict],
    article_ids: list[str],
) -> int:
    """Google Sheets에 기사 일괄 저장. 저장된 건수 반환."""
    if not articles_with_analysis:
        return 0

    current_rows = len(ws.get_all_values())  # 헤더 포함 현재 행 수
    rows = []

    for i, (item, article_id) in enumerate(zip(articles_with_analysis, article_ids)):
        article  = item["article"]
        analysis = item["analysis"]
        rows.append([
            current_rows + i,           # No. (헤더 제외 순번)
            article["published"],
            article["source"],
            analysis["심리"],
            analysis["관련종목"],
            analysis["테마섹터"],
            analysis["핵심요약"],
            article["link"],
            "",                          # 피드백 (👍/👎 누르면 자동 기록)
        ])

    ws.append_rows(rows, value_input_option="USER_ENTERED")
    logger.info(f"Google Sheets 저장: {len(rows)}건")
    return len(rows)


def update_sheet_feedback(ws: gspread.Worksheet, article_no: int, feedback: str) -> None:
    """피드백 열(I열) 업데이트."""
    try:
        cell = ws.find(str(article_no), in_column=1)
        if cell:
            ws.update_cell(cell.row, 9, feedback)
    except Exception as e:
        logger.error(f"시트 피드백 업데이트 오류: {e}")


# ══════════════════════════════════════════════
# RSS 수집
# ══════════════════════════════════════════════
def load_seen_articles() -> set:
    """이미 처리한 기사 URL 목록 로드."""
    if SEEN_ARTICLES_FILE.exists():
        try:
            with open(SEEN_ARTICLES_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"seen_articles.json 로드 실패: {e}")
    return set()


def save_seen_articles(seen: set) -> None:
    """처리한 기사 URL 목록 저장."""
    with open(SEEN_ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


def fetch_rss(source_name: str, url: str) -> list[dict]:
    """단일 RSS 피드에서 기사 수집."""
    articles = []
    try:
        feed = feedparser.parse(url)
        for entry in feed.entries:
            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()

            published = datetime.now().strftime("%Y-%m-%d %H:%M")
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6]).strftime("%Y-%m-%d %H:%M")

            if title and link:
                articles.append({
                    "source": source_name, "title": title,
                    "link": link, "summary": summary, "published": published,
                })
    except Exception as e:
        logger.error(f"[{source_name}] RSS 수집 오류: {e}")
    return articles


def fetch_all_rss(seen: set) -> list[dict]:
    """모든 RSS 소스에서 신규 기사 수집."""
    new_articles = []
    for name, url in RSS_SOURCES.items():
        articles = fetch_rss(name, url)
        new      = [a for a in articles if a["link"] not in seen]
        new_articles.extend(new)
        logger.info(f"[{name}] 수집 {len(articles)}건, 신규 {len(new)}건")
    return new_articles


# ══════════════════════════════════════════════
# 텔레그램 전송 (피드백 버튼 포함)
# ══════════════════════════════════════════════
async def send_summary_header(bot: Bot, count: int) -> None:
    """수집 사이클 시작 알림 헤더 메시지."""
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = f"📰 <b>주식 뉴스 요약</b> ({now})\n신규 기사 <b>{count}건</b> 분석 완료 ↓"
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")


async def send_article_with_feedback(bot: Bot, item: dict, article_id: str) -> None:
    """기사 분석 결과를 👍/👎 버튼과 함께 전송."""
    article  = item["article"]
    analysis = item["analysis"]
    emoji    = SENTIMENT_EMOJI.get(analysis["심리"], "⚪")

    text = (
        f"{emoji} <b>[{article['source']}]</b>\n"
        f"<b>{article['title']}</b>\n\n"
        f"📌 {analysis['핵심요약']}\n"
        f"🏷 {analysis['테마섹터']} | {analysis['관련종목']}\n"
        f"🔗 <a href=\"{article['link']}\">기사 보기</a>"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 정확해요", callback_data=f"good|{article_id}"),
        InlineKeyboardButton("👎 틀렸어요", callback_data=f"bad|{article_id}"),
    ]])

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


# ══════════════════════════════════════════════
# 텔레그램 피드백 콜백 핸들러
# ══════════════════════════════════════════════
async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👍/👎 버튼 클릭 시 처리.

    1. 피드백 저장 (good_examples / bad_examples)
    2. 버튼 제거 (중복 클릭 방지)
    3. 다음 Gemini 프롬프트에 자동 반영
    """
    query = update.callback_query
    await query.answer("피드백 감사합니다! 분석 품질 향상에 반영됩니다.")

    data = query.data  # "good|<article_id>" 또는 "bad|<article_id>"
    action, article_id = data.split("|", 1)

    store   = load_feedback_store()
    pending = store.get("pending", {})

    if article_id not in pending:
        # 이미 처리된 피드백 — 버튼만 제거
        await query.edit_message_reply_markup(reply_markup=None)
        return

    entry   = pending.pop(article_id)
    example = {"title": entry["title"], "analysis": entry["analysis"]}

    if action == "good":
        store["good_examples"].append(example)
        store["good_examples"] = store["good_examples"][-20:]  # 최근 20개 유지
        logger.info(f"👍 피드백 저장: {entry['title'][:40]}")
    else:
        store["bad_examples"].append(example)
        store["bad_examples"] = store["bad_examples"][-20:]
        logger.info(f"👎 피드백 저장: {entry['title'][:40]}")

    save_feedback_store(store)

    # 버튼 제거 (중복 제출 방지)
    await query.edit_message_reply_markup(reply_markup=None)


# ══════════════════════════════════════════════
# 메인 실행 루틴
# ══════════════════════════════════════════════
async def run_bot(
    model: genai.GenerativeModel,
    gs_client: gspread.Client,
    bot: Bot,
) -> None:
    """봇 메인 실행 함수 (10분마다 호출)."""
    logger.info("=" * 50)
    logger.info("뉴스 수집 시작")

    seen  = load_seen_articles()
    store = load_feedback_store()

    new_arts = fetch_all_rss(seen)
    if not new_arts:
        logger.info("신규 기사 없음")
        return

    logger.info(f"신규 기사 {len(new_arts)}건 분석 시작")
    results     = []
    article_ids = []

    for idx, article in enumerate(new_arts, start=1):
        logger.info(f"  [{idx}/{len(new_arts)}] {article['source']}: {article['title'][:40]}...")
        analysis   = analyze_article(model, article, store)
        article_id = str(uuid.uuid4())[:8]

        results.append({"article": article, "analysis": analysis})
        article_ids.append(article_id)

        # 피드백 대기 목록에 등록 (👍/👎 누르면 여기서 찾음)
        store["pending"][article_id] = {
            "title":    article["title"],
            "analysis": analysis,
        }
        seen.add(article["link"])
        await asyncio.sleep(1)  # Gemini API 호출 간격

    # Google Sheets 저장
    ws = get_or_create_worksheet(gs_client)
    save_to_sheets(ws, results, article_ids)

    # 텔레그램 전송: 헤더 + 기사별 메시지 (👍/👎 버튼 포함)
    await send_summary_header(bot, len(results))
    for item, article_id in zip(results, article_ids):
        await send_article_with_feedback(bot, item, article_id)
        await asyncio.sleep(0.5)  # 텔레그램 API 제한 방지

    save_seen_articles(seen)
    save_feedback_store(store)
    logger.info("사이클 완료\n")


# ══════════════════════════════════════════════
# 환경 변수 유효성 검사
# ══════════════════════════════════════════════
def validate_env() -> bool:
    """필수 환경 변수 및 파일 확인."""
    missing = []
    for key, val in [
        ("GEMINI_API_KEY",      GEMINI_API_KEY),
        ("TELEGRAM_BOT_TOKEN",  TELEGRAM_BOT_TOKEN),
        ("TELEGRAM_CHAT_ID",    TELEGRAM_CHAT_ID),
        ("GOOGLE_SHEET_ID",     GOOGLE_SHEET_ID),
    ]:
        if not val:
            missing.append(key)

    if not Path(GOOGLE_CREDENTIALS_FILE).exists():
        missing.append(f"Google 인증 JSON 파일 ({GOOGLE_CREDENTIALS_FILE})")

    if missing:
        logger.error(f"누락 항목: {', '.join(missing)}")
        logger.error(".env 파일과 README.md의 설정 방법을 확인하세요.")
        return False
    return True


# ══════════════════════════════════════════════
# 엔트리포인트
# ══════════════════════════════════════════════
async def main() -> None:
    if not validate_env():
        return

    try:
        model = init_gemini()
        logger.info("Gemini 초기화 완료")
    except Exception as e:
        logger.error(f"Gemini 초기화 실패: {e}")
        return

    try:
        gs_client = init_gspread()
        logger.info("Google Sheets 연결 완료")
    except Exception as e:
        logger.error(f"Google Sheets 연결 실패: {e}")
        return

    # 텔레그램 Application 시작 (비동기 폴링 — 👍/👎 콜백 수신)
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_feedback))

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("주식 뉴스 봇 v2.0 시작 (10분 간격 자동 수집)")

        # 최초 즉시 실행
        await run_bot(model, gs_client, app.bot)

        # 10분(600초) 간격 반복
        while True:
            await asyncio.sleep(600)
            await run_bot(model, gs_client, app.bot)


if __name__ == "__main__":
    asyncio.run(main())
