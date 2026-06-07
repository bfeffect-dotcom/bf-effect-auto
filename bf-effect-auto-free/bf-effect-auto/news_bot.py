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
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@bf_effect_news")
STATE_FILE = Path("posted_news.json")
MAX_POSTS_PER_RUN = 3
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
    "рынок реагирует", "готовятся к масштабному ipo",
]


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
    replacements = {
        "Акции шатаются": "Рынки ждут ключевых событий",
        "что смотреть на этой неделе": "ключевые события недели",
        "готовятся к масштабному IPO": "ждут новостей об IPO",
    }
    for old, new in replacements.items():
        title = title.replace(old, new)
    return title.strip()


def is_similar_text(a: str, b: str) -> bool:
    a_words = {w.lower() for w in re.findall(r"[А-Яа-яA-Za-z0-9]+", a) if len(w) > 4}
    b_words = {w.lower() for w in re.findall(r"[А-Яа-яA-Za-z0-9]+", b) if len(w) > 4}

    if not a_words or not b_words:
        return False

    common = a_words.intersection(b_words)
    return len(common) >= 3


def build_post(title: str, summary: str, source: str) -> str:
    title_ru = normalize_title(shorten(translate_to_ru(title), 110))
    summary_ru = shorten(translate_to_ru(summary), 420)

    combined_ru = f"{title_ru} {summary_ru}".lower()

    if any(phrase in combined_ru for phrase in BAD_RU_PHRASES):
        return ""

    sentences = re.split(r"(?<=[.!?])\s+", summary_ru)
    sentences = [s.strip() for s in sentences if s.strip()]

    if len(sentences) > 1 and is_similar_text(title_ru, sentences[0]):
        short_body = " ".join(sentences[1:3]).strip()
    else:
        short_body = " ".join(sentences[:2]).strip()

    if len(short_body) < 40:
        short_body = summary_ru

    short_body = shorten(short_body, 360)

    return f"{title_ru}\n\n{short_body}\n\nИсточник: {source}"


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
    print(f"Collected relevant news: {len(news)}")
    for item in news:
        if item["id"] in posted_ids:
            continue
        if published >= MAX_POSTS_PER_RUN:
            break
        post_text = build_post(item["title"], item["summary"], item["source"])
        if not post_text:
            continue
        if send_message(post_text, item["link"]):
            posted_ids.add(item["id"])
            published += 1
    save_posted_ids(posted_ids)
    print(f"Published: {published}")


if __name__ == "__main__":
    main()
