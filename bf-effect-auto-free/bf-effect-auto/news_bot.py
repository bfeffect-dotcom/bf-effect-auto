import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import feedparser
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]

CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@bf_effect_news")
STATE_FILE = Path("posted_news.json")
MAX_POSTS_PER_RUN = 3
MIN_SUMMARY_LENGTH = 60

OPENROUTER_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"

RSS_FEEDS = [
    {"url": "https://www.investing.com/rss/news_25.rss", "source": "Investing.com"},
    {"url": "https://www.investing.com/rss/news_14.rss", "source": "Investing.com"},
    {"url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,CL=F,GC=F,EURUSD=X&region=US&lang=en-US", "source": "Yahoo Finance"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "source": "CNBC"},
]

IMPORTANT_TOPICS = [
    "federal reserve", "fed", "interest rate", "rate cut", "rate hike",
    "inflation", "cpi", "ppi", "payrolls", "unemployment", "gdp", "ecb",
    "oil", "brent", "wti", "opec", "gold",
    "china", "tariff",
    "nvidia", "apple", "tesla", "microsoft", "amazon", "google", "meta",
    "earnings", "guidance",
]

BLACKLIST = [
    "retirement", "retirees", "retirement community", "advisor", "advisers",
    "robo-advisor", "stock picking", "personal finance", "mortgage",
    "credit card", "housing", "real estate", "buy-in", "how to",
    "here's what", "here’s what",
]


def clean_html(text: str) -> str:
    text = re.sub(r"<.*?>", "", text or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


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


def is_market_relevant(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
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


def create_post_with_ai(title: str, summary: str, source: str) -> str:
    prompt = f"""
Ты редактор Telegram-канала «Эффект Бабочки».

Сделай короткую публикацию на русском языке только по фактам из новости.

Если новость не про макроэкономику, центральные банки, инфляцию, ставки, нефть, золото, крупные технологические компании или их отчетность, ответь только: SKIP

Правила:
- Не придумывай факты.
- Не делай прогнозов.
- Не давай советов.
- Не используй эмодзи.
- Не используй кликбейт.
- Не пиши слова: влияние, уровень влияния, категория, что двигает рынок.

Формат:
Короткий заголовок на русском

2-4 предложения пересказа новости.

Источник: {source}

Title: {title}
Summary: {summary}
"""

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 700,
        },
        timeout=60,
    )

    if not response.ok:
        print("OpenRouter error:", response.status_code, response.text)
        return ""

    data = response.json()
    result = data["choices"][0]["message"]["content"].strip()

    if result.upper().startswith("SKIP"):
        return ""

    return result


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
        for entry in parsed.entries[:12]:
            title = clean_html(getattr(entry, "title", ""))
            link = getattr(entry, "link", "").strip()
            summary = get_summary(entry)
            if not title or not link or not summary:
                continue
            if not is_market_relevant(title, summary):
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
        post_text = create_post_with_ai(
            title=item["title"],
            summary=item["summary"],
            source=item["source"],
        )
        if not post_text:
            continue
        if send_message(post_text, item["link"]):
            posted_ids.add(item["id"])
            published += 1
    save_posted_ids(posted_ids)
    print(f"Published: {published}")


if __name__ == "__main__":
    main()
