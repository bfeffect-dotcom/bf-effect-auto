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

RSS_FEEDS = [
    {"url": "https://www.investing.com/rss/news_25.rss", "source": "Investing.com"},
    {"url": "https://www.investing.com/rss/news_14.rss", "source": "Investing.com"},
    {"url": "https://www.marketwatch.com/rss/topstories", "source": "MarketWatch"},
    {"url": "https://www.marketwatch.com/rss/marketpulse", "source": "MarketWatch"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,CL=F,GC=F,EURUSD=X&region=US&lang=en-US", "source": "Yahoo Finance"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "source": "CNBC"},
]

KEYWORDS = [
    "inflation", "cpi", "ppi", "fed", "federal reserve", "interest rate", "rates",
    "recession", "gdp", "jobs", "payrolls", "unemployment", "economy", "central bank",
    "oil", "brent", "wti", "opec", "gas", "gold", "commodities", "energy", "crude",
    "war", "sanctions", "tariff", "iran", "china", "russia", "israel", "hormuz",
    "conflict", "attack", "missile", "ceasefire",
    "nvidia", "apple", "tesla", "microsoft", "amazon", "google", "meta",
    "earnings", "guidance", "profit", "revenue",
    "stocks", "market", "nasdaq", "s&p", "dow", "yields", "dollar", "euro",
    "volatility", "vix",
]

def clean_html(text: str) -> str:
    text = re.sub(r"<.*?>", "", text or "")
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
        encoding="utf-8"
    )

def item_id(title: str, link: str) -> str:
    return hashlib.sha256(f"{title}|{link}".encode("utf-8")).hexdigest()

def is_market_relevant(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(word in text for word in KEYWORDS)

def trim_text(text: str, max_len: int = 450) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "."

def build_message(item: Dict) -> str:
    title_ru = translate_to_ru(item["title"])
    summary_ru = translate_to_ru(item["summary"])

 summary_ru = trim_text(summary_ru, 450)

    return f"""{title_ru}

{summary_ru}

Источник: {item['source']}
{item['link']}"""

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
    for feed in RSS_FEEDS:
        parsed = feedparser.parse(feed["url"])
        for entry in parsed.entries[:10]:
            title = clean_html(getattr(entry, "title", ""))
            link = getattr(entry, "link", "").strip()
            summary = clean_html(getattr(entry, "summary", ""))
            if len(summary) < 80:
    continue

            if not title or not link:
                continue

            if not is_market_relevant(title, summary):
                continue

            results.append({
                "id": item_id(title, link),
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

    for item in news:
        if item["id"] in posted_ids:
            continue
        if published >= MAX_POSTS_PER_RUN:
            break

        if send_message(build_message(item)):
            posted_ids.add(item["id"])
            published += 1

    save_posted_ids(posted_ids)
    print(f"Published: {published}")

if __name__ == "__main__":
    main()
