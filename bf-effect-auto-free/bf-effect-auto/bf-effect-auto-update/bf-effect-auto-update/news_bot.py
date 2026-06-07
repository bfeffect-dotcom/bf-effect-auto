import os
import json
import re
import html
import hashlib
from pathlib import Path
from datetime import datetime, timezone

import feedparser
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = "@bf_effect_news"
STATE_FILE = Path("published_news.json")
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "3"))

RSS_FEEDS = [
    {"name": "MarketWatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"name": "Yahoo Finance", "url": "https://finance.yahoo.com/news/rssindex"},
    {"name": "CNBC Markets", "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"name": "CNBC Economy", "url": "https://www.cnbc.com/id/20910258/device/rss/rss.html"},
    {"name": "Investing.com", "url": "https://www.investing.com/rss/news.rss"},
    {"name": "WSJ Markets", "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    {"name": "WSJ World", "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml"},
]

KEYWORDS = [
    "inflation", "cpi", "ppi", "federal reserve", "fed", "interest rate", "rates",
    "rate cut", "rate hike", "recession", "economy", "gdp", "unemployment", "jobs",
    "treasury", "bond", "dollar", "yield",
    "oil", "brent", "wti", "opec", "gas", "natural gas", "gold", "silver", "commodities",
    "china", "tariff", "sanctions", "war", "iran", "israel", "russia", "ukraine", "red sea", "hormuz",
    "nvidia", "tesla", "apple", "microsoft", "amazon", "google", "alphabet", "meta", "amd",
    "earnings", "guidance", "forecast", "profit", "revenue",
    "stocks", "s&p", "nasdaq", "dow", "market", "futures",
]

RU_HINTS = {
    "inflation": "инфляция", "cpi": "индекс потребительских цен", "ppi": "индекс цен производителей",
    "federal reserve": "Федеральная резервная система", "fed": "ФРС", "interest rate": "процентные ставки",
    "rate cut": "снижение ставки", "rate hike": "повышение ставки", "recession": "рецессия",
    "economy": "экономика", "gdp": "ВВП", "unemployment": "безработица", "jobs": "рынок труда",
    "oil": "нефть", "brent": "Brent", "wti": "WTI", "opec": "ОПЕК", "gold": "золото",
    "china": "Китай", "tariff": "пошлины", "sanctions": "санкции", "war": "военный конфликт",
    "iran": "Иран", "israel": "Израиль", "russia": "Россия", "ukraine": "Украина", "hormuz": "Ормузский пролив",
    "nvidia": "NVIDIA", "tesla": "Tesla", "apple": "Apple", "microsoft": "Microsoft", "amazon": "Amazon",
    "google": "Google", "alphabet": "Alphabet", "meta": "Meta", "earnings": "отчетность",
    "revenue": "выручка", "profit": "прибыль", "stocks": "акции", "nasdaq": "Nasdaq", "s&p": "S&P 500",
}


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_for_id(text: str) -> str:
    text = text.lower()
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[^a-z0-9а-яё ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_news_id(title: str, link: str) -> str:
    base = normalize_for_id(title) or link
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def load_published() -> set:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("published", []))
    except Exception:
        return set()


def save_published(published: set) -> None:
    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "published": list(published)[-1000:],
    }
    STATE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def matches_keywords(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    return any(keyword in text for keyword in KEYWORDS)


def build_russian_post(title: str, summary: str, source: str) -> str:
    title_clean = clean_text(title)
    summary_clean = clean_text(summary)

    text_for_keywords = f"{title_clean} {summary_clean}".lower()
    found = [RU_HINTS[k] for k in RU_HINTS if k in text_for_keywords]
    found = list(dict.fromkeys(found))[:3]

    # Бесплатная версия без AI: аккуратный редакторский шаблон на русском.
    # Заголовок пока не переводится идеально, но пост уже выглядит чище RSS-ленты.
    headline = title_clean

    if summary_clean:
        body = summary_clean
    else:
        if found:
            body = f"Новость связана с темами: {', '.join(found)}. Такие события обычно привлекают внимание участников мировых рынков."
        else:
            body = "Событие привлекло внимание финансовых рынков и может быть важным для глобальной экономической повестки."

    if len(body) > 420:
        body = body[:417].rsplit(" ", 1)[0] + "..."

    context = ""
    if found:
        context = f"\n\nВ фокусе: {', '.join(found)}."

    return f"{headline}\n\n{body}{context}\n\nИсточник: {source}"


def send_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": CHANNEL,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    if not response.ok:
        print("Telegram error:", response.status_code, response.text)
    return response.ok


def main() -> None:
    published = load_published()
    sent = 0

    for feed in RSS_FEEDS:
        if sent >= MAX_POSTS_PER_RUN:
            break

        parsed = feedparser.parse(feed["url"])
        entries = getattr(parsed, "entries", [])[:10]

        for item in entries:
            if sent >= MAX_POSTS_PER_RUN:
                break

            title = clean_text(getattr(item, "title", ""))
            summary = clean_text(getattr(item, "summary", ""))
            link = getattr(item, "link", "")

            if not title:
                continue

            news_id = make_news_id(title, link)
            if news_id in published:
                continue

            if not matches_keywords(title, summary):
                continue

            post = build_russian_post(title, summary, feed["name"])
            if send_message(post):
                published.add(news_id)
                sent += 1
                print(f"Published: {title}")

    save_published(published)
    print(f"Done. Published {sent} posts.")


if __name__ == "__main__":
    main()
