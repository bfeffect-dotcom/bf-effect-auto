import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import feedparser
import requests
from deep_translator import GoogleTranslator

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")

CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@bf_effect_news")
STATE_FILE = Path("posted_news.json")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "3"))
MIN_SUMMARY_LENGTH = 60

RSS_FEEDS = [
    {"url": "https://www.investing.com/rss/news_25.rss", "source": "Investing.com"},
    {"url": "https://www.investing.com/rss/news_14.rss", "source": "Investing.com"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,CL=F,GC=F,EURUSD=X&region=US&lang=en-US", "source": "Yahoo Finance"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "source": "CNBC"},
]

STRONG_TOPICS = [
    "federal reserve", "fed", "interest rate", "rate cut", "rate hike",
    "inflation", "cpi", "ppi", "payrolls", "unemployment", "gdp", "ecb",
    "oil", "brent", "wti", "opec", "gold", "gas",
    "tariff", "tariffs", "sanctions", "iran", "israel", "ukraine", "russia", "hormuz",
]

COMPANY_TOPICS = [
    "nvidia", "apple", "tesla", "microsoft", "amazon", "google", "meta", "oracle", "spacex",
]

COMPANY_EVENT_WORDS = [
    "earnings", "results", "guidance", "forecast", "revenue forecast", "ipo",
    "deal", "agreement", "contract", "partnership", "acquisition", "merger",
    "buyout", "layoffs", "antitrust", "regulator", "sec", "ftc",
]

BLACKLIST = [
    "retirement", "retirees", "retirement community", "advisor", "advisers",
    "robo-advisor", "stock picking", "personal finance", "mortgage",
    "credit card", "housing", "real estate", "buy-in", "how to",
    "here's what", "here’s what", "opinion", "column", "watchlist",
    "etf", "etfs", "ways to play", "motley fool", "fool.com",
    "compare", "comparison", "what to watch", "watch this week", "this week",
    "week ahead", "weekly", "outlook", "market outlook", "preview",
    "analyst says", "analysts say", "wall street says", "wall street gauges",
    "investing strategy", "portfolio", "best stocks", "top stocks",
]

BAD_RU_PHRASES = [
    "акции шатаются", "что смотреть", "на этой неделе", "инвесторы ждут",
    "рынок реагирует", "готовятся к масштабному ipo", "так или иначе",
]

BAD_AI_PATTERNS = [
    "The user wants", "I need to", "Draft", "Check constraints", "Input:",
    "Output:", "Title:", "Summary:", "Reasoning", "Let's", "Here is",
]

TITLE_REPLACEMENTS = {
    "Китайские электромобили могут появиться в США в течение нескольких лет, так или иначе": "Китайские электромобили могут выйти на рынок США несмотря на тарифы",
    "Акции шатаются": "Рынки ждут ключевых событий",
    "что смотреть на этой неделе": "ключевые события недели",
    "готовятся к масштабному IPO": "ждут новостей об IPO",
}


def clean_html(text: str) -> str:
    text = re.sub(r"<.*?>", "", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def translate_to_ru(text: str) -> str:
    if not text:
        return ""
    try:
        return GoogleTranslator(source="auto", target="ru").translate(text)
    except Exception as e:
        print("Translation error:", e)
        return text


def load_posted_ids() -> set:
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_posted_ids(posted_ids: set) -> None:
    STATE_FILE.write_text(
        json.dumps(list(posted_ids)[-500:], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def item_id(title: str, link: str) -> str:
    return hashlib.sha256(f"{title}|{link}".encode("utf-8")).hexdigest()


def is_market_relevant(title: str, summary: str, link: str) -> bool:
    text = f"{title} {summary} {link}".lower()

    if any(word in text for word in BLACKLIST):
        return False

    if any(word in text for word in STRONG_TOPICS):
        return True

    has_company = any(word in text for word in COMPANY_TOPICS)
    has_company_event = any(word in text for word in COMPANY_EVENT_WORDS)
    return has_company and has_company_event


def get_summary(entry) -> str:
    candidates = [
        getattr(entry, "summary", ""),
        getattr(entry, "description", ""),
        getattr(entry, "subtitle", ""),
    ]
    for candidate in candidates:
        summary = clean_html(candidate)
        if len(summary) >= MIN_SUMMARY_LENGTH:
            return summary
    return ""


def shorten(text: str, limit: int) -> str:
    text = clean_html(text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(".,;:") + "."


def normalize_title(title: str) -> str:
    for old, new in TITLE_REPLACEMENTS.items():
        title = title.replace(old, new)
    return title.strip()


def is_valid_ai_post(text: str) -> bool:
    if not text:
        return False
    if text.upper().startswith("SKIP"):
        return False
    if any(pattern in text for pattern in BAD_AI_PATTERNS):
        return False
    if "```" in text:
        return False
    if "Источник:" not in text:
        return False
    return True


def create_ai_post(title: str, summary: str, source: str) -> str:
    if not OPENROUTER_API_KEY:
        return ""
        
    prompt = f"""
    Сделай короткий русский Telegram-пост по новости.

    Верни только JSON:
    {{"title":"...","fact":"...","why":"..."}}

    Правила:
    - title: короткий заголовок.
    - fact: 1-2 предложения только о том, что произошло.
    - why: 1 предложение, почему это важно для экономики, бизнеса, сырья или технологий.
    - Без длинной аналитики.
    - Без прогнозов.
    - Не добавляй факты, которых нет в новости.
    - Не используй markdown.

    Новость: {title}
    Описание: {summary}
    """
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 420,
            },
            timeout=60,
        )
    except Exception as e:
        print("OpenRouter request error:", e)
        return ""

    if not response.ok:
        print("OpenRouter HTTP error:", response.status_code, response.text[:500])
        return ""

    try:
        data = response.json()
        result = data["choices"][0]["message"].get("content", "").strip()
    except Exception as e:
        print("OpenRouter parse error:", e, response.text[:500])
        return ""

    result = clean_html(result)
    if len(result) > 900:
        result = shorten(result, 900)

    if not is_valid_ai_post(result):
        print("AI post rejected")
        return ""

    return result


def build_fallback_post(title: str, summary: str, source: str) -> str:
    title_ru = normalize_title(shorten(translate_to_ru(title), 115))
    summary_ru = shorten(translate_to_ru(summary), 520)

    combined_ru = f"{title_ru} {summary_ru}".lower()
    if any(phrase in combined_ru for phrase in BAD_RU_PHRASES):
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", summary_ru)
    sentences = [s.strip() for s in sentences if s.strip()]
    short_body = " ".join(sentences[:2]).strip() or summary_ru

    short_body = shorten(short_body, 420)
    return f"{title_ru}\n\n{short_body}\n\nИсточник: {source}"


def build_post(title: str, summary: str, link: str, source: str) -> str:
    ai_post = create_ai_post(title, summary, source)
    if ai_post:
        return ai_post
    return build_fallback_post(title, summary, source)


def send_message(text: str, link: str) -> bool:
    message = f"{text}\n\n{link}"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": CHANNEL,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    if not response.ok:
        print("Telegram error:", response.status_code, response.text)
        return False
    return True


def collect_news() -> List[Dict]:
    results = []
    seen_ids = set()
    for feed in RSS_FEEDS:
        parsed = feedparser.parse(feed["url"])
        print(f"Feed {feed['source']}: {len(parsed.entries)} entries")
        for entry in parsed.entries[:15]:
            title = clean_html(getattr(entry, "title", ""))
            link = getattr(entry, "link", "").strip()
            summary = get_summary(entry)
            if not title or not link or not summary:
                continue
            if not is_market_relevant(title, summary, link):
                continue
            uid = item_id(title, link)
            if uid in seen_ids:
                continue
            seen_ids.add(uid)
            results.append({
                "id": uid,
                "title": title,
                "summary": summary,
                "link": link,
                "source": feed["source"],
            })
    return results


def main() -> None:
    print("Run started:", datetime.now(timezone.utc).isoformat())
    posted_ids = load_posted_ids()
    news = collect_news()
    published = 0
    published_sources = set()
    print(f"Collected relevant news: {len(news)}")
    for item in news:
        for item in news:
    if item["id"] in posted_ids:
        continue

    if item["source"] in published_sources:
        continue
        if published >= MAX_POSTS_PER_RUN:
            break
        post_text = build_post(item["title"], item["summary"], item["link"], item["source"])
        if not post_text:
            continue
        if send_message(post_text, item["link"]):
    posted_ids.add(item["id"])
    published_sources.add(item["source"])
    published += 1
    save_posted_ids(posted_ids)
    print(f"Published: {published}")


if __name__ == "__main__":
    main()
