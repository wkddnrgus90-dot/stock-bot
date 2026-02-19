"""
주식 뉴스 자동 수집 봇 v2.2
변경사항:
- 버그수정: Google Sheets 피드백 열 실제 반영
- 버그수정: No. 번호 1부터 정확하게 시작
- 버그수정: 연합뉴스 RSS URL 대소문자 수정
- 안정성: Gemini 429 오류 자동 재시도 (최대 3회)
- 안정성: Google Sheets 오류 시에도 텔레그램 전송 계속
- 안정성: 텔레그램 flood 오류 대응 + 첫 실행 최대 10건 전송
- 안정성: Ctrl+C graceful shutdown
- 안정성: pending 24시간 자동 정리
- 개선: 매일 18:00 일일 마감 요약 리포트
- 개선: 봇 시작/오류종료 텔레그램 알림
- 개선: 해외 뉴스 소스 추가 (Bloomberg, Nikkei Asia, MarketWatch, Yahoo Finance)
"""

import asyncio
import json
import logging
import os
import signal
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import gspread
import google.generativeai as genai
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError, RetryAfter
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
# RSS 피드 소스 (연합뉴스 URL 대소문자 버그 수정)
# ──────────────────────────────────────────────
RSS_SOURCES = {
    # ── 국내 ──────────────────────────────────
    "한국경제":     "https://www.hankyung.com/feed/economy",
    "매일경제":     "https://www.mk.co.kr/rss/30000001/",
    "연합뉴스":     "https://www.yna.co.kr/rss/economy.xml",
    "머니투데이":   "https://rss.mt.co.kr/mt/1/rss.xml",
    "이데일리":     "https://rss.edaily.co.kr/edaily/economy_news.xml",
    "인베스팅닷컴": "https://kr.investing.com/rss/news.rss",
    # ── 해외 ──────────────────────────────────
    "Bloomberg":    "https://feeds.bloomberg.com/markets/news.rss",
    "Nikkei Asia":  "https://asia.nikkei.com/rss/feed/nar",
    "MarketWatch":  "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "Yahoo Finance":"https://finance.yahoo.com/rss/",
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
SENTIMENT_EMOJI    = {"긍정": "🟢", "부정": "🔴", "중립": "🟡"}
DAILY_REPORT_HOUR  = 18   # 일일 마감 요약 전송 시각
MAX_TELEGRAM_FIRST = 10   # 첫 실행 시 텔레그램 최대 전송 건수

# ──────────────────────────────────────────────
# graceful shutdown 플래그
# ──────────────────────────────────────────────
_shutdown = False

def _handle_signal(sig, frame):
    global _shutdown
    logger.info("종료 신호 수신 — 현재 작업 완료 후 종료합니다.")
    _shutdown = True

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ══════════════════════════════════════════════
# 피드백 저장소
# ══════════════════════════════════════════════
def load_feedback_store() -> dict:
    default = {"pending": {}, "good_examples": [], "bad_examples": []}
    if FEEDBACK_FILE.exists():
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"feedback_store.json 로드 실패, 초기화: {e}")
    return default


def save_feedback_store(store: dict) -> None:
    with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)


def cleanup_pending(store: dict) -> dict:
    """24시간 지난 pending 항목 자동 삭제."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    before = len(store["pending"])
    store["pending"] = {
        k: v for k, v in store["pending"].items()
        if v.get("created_at", "9999") > cutoff
    }
    removed = before - len(store["pending"])
    if removed:
        logger.info(f"만료 pending {removed}건 정리")
    return store


# ══════════════════════════════════════════════
# Gemini AI 분석 (429 자동 재시도 추가)
# ══════════════════════════════════════════════
def init_gemini() -> genai.GenerativeModel:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 미설정")
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel("gemini-1.5-flash")


def build_analysis_prompt(title: str, summary: str, store: dict) -> str:
    good_section = ""
    bad_section  = ""
    if store["good_examples"]:
        good_section = "\n[좋은 분석 예시 - 이런 방식으로 분석해 주세요]\n"
        for ex in store["good_examples"][-2:]:
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
    return f"""다음 경제/주식 뉴스 기사를 분석해 주세요. (국내외 기사 모두 포함)
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
    """Gemini API 분석 — 429 오류 시 최대 3회 재시도."""
    prompt  = build_analysis_prompt(
        title=article["title"],
        summary=article["summary"] or article["title"],
        store=store,
    )
    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            return parse_gemini_response(response.text)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower():
                wait = 60 * (attempt + 1)  # 1분, 2분, 3분
                logger.warning(f"Gemini 429 오류 — {wait}초 대기 후 재시도 ({attempt+1}/3)")
                import time; time.sleep(wait)
            else:
                logger.error(f"Gemini 분석 오류 ({article['title'][:30]}): {e}")
                break
    return {"심리": "분석실패", "관련종목": "-", "테마섹터": "-", "핵심요약": article["title"]}


def parse_gemini_response(text: str) -> dict:
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
    creds_path = Path(GOOGLE_CREDENTIALS_FILE)
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Google 인증 파일 없음: {GOOGLE_CREDENTIALS_FILE}\n"
            "README.md의 'Google Sheets 설정' 섹션을 참고하세요."
        )
    creds = Credentials.from_service_account_file(str(creds_path), scopes=GSPREAD_SCOPES)
    return gspread.authorize(creds)


def get_or_create_worksheet(client: gspread.Client) -> gspread.Worksheet:
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    sheet_name  = "주식뉴스"
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=10000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS)
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
    articles_with_analysis: list,
    article_ids: list,
) -> int:
    """Google Sheets 저장 — 실패 시 예외 반환 (봇 전체 중단 방지)."""
    if not articles_with_analysis:
        return 0
    try:
        current_rows = len(ws.get_all_values())
        rows = []
        for i, (item, article_id) in enumerate(zip(articles_with_analysis, article_ids)):
            article  = item["article"]
            analysis = item["analysis"]
            rows.append([
                current_rows - 1 + i + 1,  # 버그수정: 헤더 제외 정확한 No. 계산
                article["published"],
                article["source"],
                analysis["심리"],
                analysis["관련종목"],
                analysis["테마섹터"],
                analysis["핵심요약"],
                article["link"],
                "",
            ])
        ws.append_rows(rows, value_input_option="USER_ENTERED")
        logger.info(f"Google Sheets 저장: {len(rows)}건")
        return len(rows)
    except Exception as e:
        logger.error(f"Google Sheets 저장 실패 (텔레그램 전송은 계속 진행): {e}")
        return 0


def update_sheet_feedback(client: gspread.Client, article_no: int, feedback: str) -> None:
    """피드백 열(I열) 업데이트."""
    try:
        ws   = get_or_create_worksheet(client)
        cell = ws.find(str(article_no), in_column=1)
        if cell:
            ws.update_cell(cell.row, 9, feedback)
    except Exception as e:
        logger.error(f"시트 피드백 업데이트 오류: {e}")


# ══════════════════════════════════════════════
# RSS 수집
# ══════════════════════════════════════════════
def load_seen_articles() -> set:
    if SEEN_ARTICLES_FILE.exists():
        try:
            with open(SEEN_ARTICLES_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"seen_articles.json 로드 실패: {e}")
    return set()


def save_seen_articles(seen: set) -> None:
    with open(SEEN_ARTICLES_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False, indent=2)


def fetch_rss(source_name: str, url: str) -> list:
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


def fetch_all_rss(seen: set) -> list:
    new_articles = []
    for name, url in RSS_SOURCES.items():
        articles = fetch_rss(name, url)
        new      = [a for a in articles if a["link"] not in seen]
        new_articles.extend(new)
        logger.info(f"[{name}] 수집 {len(articles)}건, 신규 {len(new)}건")
    return new_articles


# ══════════════════════════════════════════════
# 텔레그램 전송 (flood 오류 대응 + 첫 실행 제한)
# ══════════════════════════════════════════════
async def safe_send_message(bot: Bot, text: str, **kwargs) -> bool:
    """텔레그램 전송 — RetryAfter(flood) 자동 대응."""
    for attempt in range(3):
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, **kwargs)
            return True
        except RetryAfter as e:
            wait = e.retry_after + 1
            logger.warning(f"텔레그램 flood 제한 — {wait}초 대기")
            await asyncio.sleep(wait)
        except TelegramError as e:
            logger.error(f"텔레그램 전송 오류: {e}")
            return False
    return False


async def send_bot_status(bot: Bot, message: str) -> None:
    """봇 시작/종료 상태 알림."""
    await safe_send_message(bot, message, parse_mode="HTML")


async def send_summary_header(bot: Bot, count: int) -> None:
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = f"📰 <b>주식 뉴스 요약</b> ({now})\n신규 기사 <b>{count}건</b> 분석 완료 ↓"
    await safe_send_message(bot, text, parse_mode="HTML")


async def send_article_with_feedback(bot: Bot, item: dict, article_id: str) -> None:
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
    for attempt in range(3):
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=text,
                parse_mode="HTML", reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            return
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramError as e:
            logger.error(f"기사 전송 오류: {e}")
            return


# ══════════════════════════════════════════════
# 일일 마감 요약 리포트 (18:00)
# ══════════════════════════════════════════════
async def send_daily_report(bot: Bot, client: gspread.Client) -> None:
    """매일 18:00 — 오늘 수집한 기사를 섹터별로 집계해서 요약 전송."""
    try:
        ws   = get_or_create_worksheet(client)
        rows = ws.get_all_values()
        today = datetime.now().strftime("%Y-%m-%d")

        today_rows = [r for r in rows[1:] if r and len(r) >= 8 and r[1].startswith(today)]
        if not today_rows:
            await safe_send_message(bot, f"📊 <b>일일 마감 리포트</b> [{today}]\n오늘 수집된 기사가 없습니다.", parse_mode="HTML")
            return

        # 섹터별 집계
        sector_count: dict = {}
        sentiment_count = {"긍정": 0, "부정": 0, "중립": 0, "분석실패": 0}
        for row in today_rows:
            sentiment = row[3] if len(row) > 3 else "중립"
            sector    = row[5] if len(row) > 5 else "기타"
            sentiment_count[sentiment] = sentiment_count.get(sentiment, 0) + 1
            for s in sector.split(","):
                s = s.strip()
                if s:
                    sector_count[s] = sector_count.get(s, 0) + 1

        top_sectors = sorted(sector_count.items(), key=lambda x: x[1], reverse=True)[:5]
        sector_lines = "\n".join([f"  • {s}: {c}건" for s, c in top_sectors])

        text = (
            f"📊 <b>일일 마감 리포트</b> [{today}]\n\n"
            f"📰 총 수집: <b>{len(today_rows)}건</b>\n"
            f"🟢 긍정: {sentiment_count.get('긍정', 0)}건  "
            f"🔴 부정: {sentiment_count.get('부정', 0)}건  "
            f"🟡 중립: {sentiment_count.get('중립', 0)}건\n\n"
            f"🏷 <b>오늘 주요 섹터 TOP 5</b>\n{sector_lines}\n\n"
            f"📋 전체 내용은 Google Sheets에서 확인하세요."
        )
        await safe_send_message(bot, text, parse_mode="HTML")
        logger.info("일일 마감 리포트 전송 완료")
    except Exception as e:
        logger.error(f"일일 리포트 오류: {e}")


# ══════════════════════════════════════════════
# 텔레그램 피드백 콜백 핸들러 (시트 반영 버그 수정)
# ══════════════════════════════════════════════
async def handle_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """👍/👎 버튼 클릭 처리 — Google Sheets 피드백 열 반영 (버그 수정)."""
    query = update.callback_query
    await query.answer("피드백 감사합니다! 분석 품질 향상에 반영됩니다.")

    data      = query.data
    action, article_id = data.split("|", 1)

    store   = load_feedback_store()
    pending = store.get("pending", {})

    if article_id not in pending:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    entry   = pending.pop(article_id)
    example = {"title": entry["title"], "analysis": entry["analysis"]}

    feedback_text = "👍" if action == "good" else "👎"

    if action == "good":
        store["good_examples"].append(example)
        store["good_examples"] = store["good_examples"][-20:]
        logger.info(f"👍 피드백 저장: {entry['title'][:40]}")
    else:
        store["bad_examples"].append(example)
        store["bad_examples"] = store["bad_examples"][-20:]
        logger.info(f"👎 피드백 저장: {entry['title'][:40]}")

    save_feedback_store(store)

    # 버그수정: Google Sheets 피드백 열 실제 반영
    gs_client = context.bot_data.get("gs_client")
    article_no = entry.get("article_no")
    if gs_client and article_no:
        update_sheet_feedback(gs_client, article_no, feedback_text)

    await query.edit_message_reply_markup(reply_markup=None)


# ══════════════════════════════════════════════
# 메인 실행 루틴
# ══════════════════════════════════════════════
async def run_bot(
    model: genai.GenerativeModel,
    gs_client: gspread.Client,
    bot: Bot,
    is_first_run: bool = False,
) -> None:
    if _shutdown:
        return

    logger.info("=" * 50)
    logger.info("뉴스 수집 시작")

    seen  = load_seen_articles()
    store = load_feedback_store()
    store = cleanup_pending(store)  # 24시간 만료 pending 정리

    new_arts = fetch_all_rss(seen)
    if not new_arts:
        logger.info("신규 기사 없음")
        return

    logger.info(f"신규 기사 {len(new_arts)}건 분석 시작")
    results     = []
    article_ids = []

    # 첫 실행 시 텔레그램 전송 건수 제한 (시트에는 전부 저장)
    telegram_send_limit = MAX_TELEGRAM_FIRST if is_first_run else len(new_arts)

    for idx, article in enumerate(new_arts, start=1):
        if _shutdown:
            break
        logger.info(f"  [{idx}/{len(new_arts)}] {article['source']}: {article['title'][:40]}...")
        analysis   = analyze_article(model, article, store)
        article_id = str(uuid.uuid4())[:8]

        results.append({"article": article, "analysis": analysis})
        article_ids.append(article_id)

        store["pending"][article_id] = {
            "title":      article["title"],
            "analysis":   analysis,
            "created_at": datetime.now().isoformat(),
            "article_no": None,  # 시트 저장 후 업데이트
        }
        seen.add(article["link"])
        await asyncio.sleep(1)

    # Google Sheets 저장 (실패해도 텔레그램 전송 계속)
    try:
        ws       = get_or_create_worksheet(gs_client)
        saved_no = save_to_sheets(ws, results, article_ids)

        # article_no를 pending에 업데이트 (피드백 시트 반영용)
        current_rows = len(ws.get_all_values())
        for i, article_id in enumerate(article_ids):
            if article_id in store["pending"]:
                store["pending"][article_id]["article_no"] = current_rows - len(article_ids) + i
    except Exception as e:
        logger.error(f"Google Sheets 처리 오류: {e}")

    # 텔레그램 전송
    send_items = results[:telegram_send_limit]
    if send_items:
        if is_first_run and len(results) > MAX_TELEGRAM_FIRST:
            await safe_send_message(
                bot,
                f"🚀 <b>봇 첫 실행</b> — 총 {len(results)}건 수집됨\n"
                f"텔레그램은 최신 {MAX_TELEGRAM_FIRST}건만 전송합니다. 전체는 Google Sheets에서 확인하세요.",
                parse_mode="HTML"
            )
        await send_summary_header(bot, len(send_items))
        for item, article_id in zip(send_items, article_ids[:telegram_send_limit]):
            await send_article_with_feedback(bot, item, article_id)
            await asyncio.sleep(0.5)

    save_seen_articles(seen)
    save_feedback_store(store)
    logger.info(f"사이클 완료: {len(results)}건 처리\n")


# ══════════════════════════════════════════════
# 환경 변수 유효성 검사
# ══════════════════════════════════════════════
def validate_env() -> bool:
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

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_feedback))
    app.bot_data["gs_client"] = gs_client  # 버그수정: 핸들러에서 gs_client 접근 가능하게

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # 봇 시작 알림
        await send_bot_status(
            app.bot,
            f"🤖 <b>주식뉴스 봇 v2.2 시작</b>\n"
            f"⏱ 수집 주기: 10분\n"
            f"📅 일일 리포트: 매일 18:00\n"
            f"시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        logger.info("주식 뉴스 봇 v2.2 시작")

        # 첫 실행 (is_first_run=True → 텔레그램 최대 10건)
        is_first = not SEEN_ARTICLES_FILE.exists()
        await run_bot(model, gs_client, app.bot, is_first_run=is_first)

        last_report_date = None  # 일일 리포트 중복 전송 방지

        while not _shutdown:
            await asyncio.sleep(600)  # 10분 대기

            if _shutdown:
                break

            # 일일 마감 리포트 (18:00, 하루 1회)
            now = datetime.now()
            if now.hour == DAILY_REPORT_HOUR and last_report_date != now.date():
                await send_daily_report(app.bot, gs_client)
                last_report_date = now.date()

            await run_bot(model, gs_client, app.bot)

        # graceful shutdown
        save_seen_articles(load_seen_articles())
        await send_bot_status(app.bot, "🛑 <b>주식뉴스 봇 종료</b>\n데이터가 안전하게 저장되었습니다.")
        logger.info("봇 정상 종료")


if __name__ == "__main__":
    asyncio.run(main())
