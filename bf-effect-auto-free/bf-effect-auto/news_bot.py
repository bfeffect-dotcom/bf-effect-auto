import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import feedparser
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@bf_effect_news")
STATE_FILE = Path("posted_news.json")

RSS_FEEDS = [
    "https://www.investing.com/rss/news_25.rss",
    "https://www.investing.com/rss/news_14.rss",
    "https://www.marketwatch.com/rss/topstories",
    "https://www.marketwatch.com/rss/marketpulse",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,CL=F,GC=F,EURUSD=X&region=US&lang=en-US",
]

KEYWORDS = {
    "Макроэкономика": [
        "inflation", "cpi", "ppi", "fed", "federal reserve", "interest rate", "rates",
        "recession", "gdp", "jobs", "payrolls", "unemployment", "economy", "central bank",
    ],
    "Сырьё": [
        "oil", "brent", "wti", "opec", "gas", "gold", "commodities", "energy",
        "crude", "supply", "inventory",
    ],
    "Геополитика": [
        "war", "sanctions", "tariff", "iran", "china", "russia", "israel", "strait",
        "hormuz", "conflict", "attack", "missile", "ceasefire",
    ],
    "Компании": [
        "nvidia", "apple", "tesla", "microsoft", "amazon", "google", "meta",
        "earnings", "guidance", "profit", "revenue",
    ],
    "Рынки": [
        "stocks", "market", "nasdaq", "s&p", "dow", "yields", "dollar", "euro",
        "bitcoin", "volatility", "vix",
    ],
}

CATEGORY_EMOJI = {
    "Макроэкономика": "📊",
    "Сырьё": "🛢",
    "Геополитика": "🌍",
    "Компании": "🏢",
    "Рынки": "📈",
}

SOURCE_NAMES = {
    "investing.com": "Investing.com",
    "marketwatch.com": "MarketWatch",
    "finance.yahoo.com": "Yahoo Finance",
}


def load_posted_ids() -> set:
    if not STATE_FILE.exists():
        return set()
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_posted_ids(posted_ids: set) -> None:
    # Keep last 500 ids to avoid file growth.
    trimmed = list(posted_ids)[-500:]
    STATE_FILE.write_text(json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8")


def item_id(title: str, link: str) -> str:
    raw = f"{title}|{link}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def detect_category(text: str) -> str | None:
    text_lower = text.lower()
    best_category = None
    best_score = 0
    for category, words in KEYWORDS.items():
        score = sum(1 for word in words if word in text_lower)
        if score > best_score:
            best_category = category
            best_score = score
    return best_category if best_score > 0 else None


def impact_level(text: str) -> str:
    text_lower = text.lower()
    critical_words = ["war", "attack", "emergency", "crash", "plunge", "surge", "fed", "cpi", "inflation", "sanctions", "hormuz"]
    high_words = ["oil", "rates", "earnings", "tariff", "recession", "yields", "opec", "gold", "nasdaq"]
    if any(w in text_lower for w in critical_words):
        return "🔴 Высокий"
    if any(w in text_lower for w in high_words):
        return "🟠 Средний"
    return "🟡 Возможное влияние"


def source_name(feed_url: str, link: str) -> str:
    combined = f"{feed_url} {link}".lower()
    for domain, name in SOURCE_NAMES.items():
        if domain in combined:
            return name
    return "Источник RSS"


def build_message(item: Dict) -> str:
    emoji = CATEGORY_EMOJI.get(item["category"], "🚨")
    return f"""{emoji} Что Двигает Рынок

Категория: {item['category']}

Событие:
{item['title']}

Уровень влияния:
{item['impact']}

Источник:
{item['source']}

Ссылка:
{item['link']}

Не является инвестиционной рекомендацией."""


def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": CHANNEL,
            "text": text,
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
    for feed_url in RSS_FEEDS:
        parsed = feedparser.parse(feed_url)
        for entry in parsed.entries[:10]:
            title = getattr(entry, "title", "").strip()
            link = getattr(entry, "link", "").strip()
            summary = getattr(entry, "summary", "")
            if not title or not link:
                continue

            text = f"{title} {summary}"
            category = detect_category(text)
            if not category:
                continue

            results.append(
                {
                    "id": item_id(title, link),
                    "title": title,
                    "link": link,
                    "category": category,
                    "impact": impact_level(text),
                    "source": source_name(feed_url, link),
                }
            )
    return results


def main() -> None:
    print("Run started:", datetime.now(timezone.utc).isoformat())
    posted_ids = load_posted_ids()
    news = collect_news()

    published = 0
    for item in news:
        if item["id"] in posted_ids:
            continue
        if published >= 3:
            break

        message = build_message(item)
        if send_message(message):
            posted_ids.add(item["id"])
            published += 1

    save_posted_ids(posted_ids)
    print(f"Published: {published}")


if __name__ == "__main__":
    main()
