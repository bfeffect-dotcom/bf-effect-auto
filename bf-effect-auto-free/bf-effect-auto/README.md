# bf-effect-auto

Автоматическая публикация рыночных новостей в Telegram канал `@bf_effect_news`.

## Как работает

GitHub Actions запускает `news_bot.py` несколько раз в день. Скрипт:

1. Читает бесплатные RSS-ленты.
2. Фильтрует новости по ключевым словам.
3. Публикует до 3 важных новостей в Telegram.
4. Сохраняет уже опубликованные новости в `posted_news.json`, чтобы не было дублей.

## Что нужно настроить

В GitHub открой:

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

Добавь секрет:

```text
TELEGRAM_BOT_TOKEN
```

Значение — токен бота из BotFather.

## Ручной запуск

`Actions` → `Market News Bot` → `Run workflow`

## Расписание

Файл `.github/workflows/news.yml` запускает бота 4 раза в день.
