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

IMPORTANT_TOPICS = [
    "federal reserve", "fed", "interest rate", "rate cut", "rate hike",
    "inflation", "cpi", "ppi", "payrolls", "unemployment", "gdp", "ecb",
    "oil", "brent", "wti", "opec", "gold", "gas",
    "nvidia", "apple", "tesla", "microsoft", "amazon", "google", "meta",
    "earnings", "guidance", "revenue forecast", "oracle", "spacex",
]

BLACKLIST = [
    "retirement", "retirees", "retirement community", "advisor", "advisers",
    "robo-advisor", "stock picking", "personal finance", "mortgage",
    "credit card", "housing", "real estate", "buy-in", "how to",
    "here's what", "here’s what", "opinion", "column", "watchlist",
    "etf", "etfs", "ways to play", "motley fool", "fool.com",
    "analyst", "analysts", "compare", "comparison",
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
    score = sum(1 for word in IMPORTANT_TOPICS if word in text)
    return score >= 2


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


def build_post(title: str, summary: str, source: str) -> str:
    title_ru = shorten(translate_to_ru(title), 120)
    summary_ru = shorten(translate_to_ru(summary), 420)

    sentences = re.split(r"(?<=[.!?])\s+", summary_ru)
    short_body = " ".join(sentences[:2]).strip()
    if not short_body:
        short_body = summary_ru

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
