# Open WebUI + Qwen

Дополнительный контейнер поверх стека `zotero-setup`: Open WebUI с агентом на
моделях Qwen (DashScope / Alibaba Model Studio). Ставится на любой сервер со
стеком. Синхронизация библиотеки Zotero между серверами — отдельная,
независимая от OWUI подсистема: SETUP.md, «Library sync between peers».

## Что в папке

| Файл | Назначение |
|---|---|
| `functions/qwen_auto.py` | Пайп-агент: один чат, четыре модели Qwen (текст/зрение/генерация/правка) |
| `docker-compose.yml` | Сервис OWUI в host-сети (видит Zotero MCP и Chromium на localhost) |
| `.env.example` | Переменные: ключ DashScope, порт, учётка для установщика |
| `install_function.sh` | Идемпотентная установка пайпа через REST API (файл в контейнер не подложить) |
| `nginx.example.conf` | Прокси: `client_max_body_size 50m` + `proxy_buffering off` |

## Развёртывание

```bash
cd ~ && git clone <репозиторий> zotero-setup && cd zotero-setup
$EDITOR .env                              # ZOTERO_USER/PASSWORD, ZOTERO_MCP_TOKEN
docker compose up -d                      # контейнер zotero
cd openwebui
cp .env.example .env && $EDITOR .env      # ключ DashScope, WEBUI_SECRET_KEY, учётка
docker compose up -d
./install_function.sh                     # ставит пайп, печатает список моделей
```

Если библиотека должна приехать с другого сервера — сначала первичный перенос
синхронизацией (до `docker compose up`), см. SETUP.md.

Прокси: скопировать `nginx.example.conf`, подставить `server_name`/HTTPS,
`nginx -t && systemctl reload nginx`. Строка `client_max_body_size 50m`
обязательна: дефолтный лимит 1 МБ режет загрузку картинок 413-й ошибкой
(HTML вместо JSON), и фронтенд падает на `JSON.parse`.

Проверка: залогиниться, выбрать **Qwen Auto**, отправить текст, загрузить
картинку, попросить нарисовать и потом «поменяй фон».

## Пайп qwen_auto

Одна запись в селекторе, внутри детерминированная развилка + инструменты:
текст → `QWEN_TEXT_MODEL`; картинка во вложении → `qwen3-vl-plus`;
«нарисуй …» → tool-call → `qwen-image-2.0`; «поменяй фон/цвет …» →
`qwen-image-edit-plus` (находит исходник в чате).

Вбитые в код факты: image-модели живут **только** на нативном эндпоинте
`/api/v1/services/aigc/multimodal-generation/generation` (в compatible-mode —
пустой `content`); результат кладётся в файловое хранилище OWUI, в чат идёт
короткая ссылка `/api/v1/files/{id}/content`, а не мегабайтный data-URI; ответ
отдаётся асинхронным генератором (стриминг).

## Веб-поиск

Родной поиск Alibaba: `enable_search: true` в compatible-mode; валв
`ENABLE_SEARCH` пайпа включён по умолчанию (текстовая ветка). Эффективность
зависит от модели: `qwen-plus` ищет надёжно, `qwen3-max` — нет даже с
`forced_search`, поэтому в `.env.example` текстовая модель — `qwen-plus`.
Если качество поиска не устроит — встроенный веб-поиск OWUI (Admin →
Settings → Web Search: SearXNG своим контейнером или ключ Tavily/Brave/Google
PSE): он инжектит результаты в контекст до пайпа и от модели не зависит.

## Zotero MCP

MCP — плагин **внутри** контейнера zotero: `http://127.0.0.1:23120/mcp`
(streamable HTTP), `Authorization: Bearer $ZOTERO_MCP_TOKEN`. Плагин и токен
живут в профиле Zotero (`prefs.js` → `mcp.server.authToken`, там же
`requireAuth=true`, `allowRemote=true`).

Open WebUI 0.6.31+ подключает MCP нативно: **Admin → Settings → External Tools
→ Add → Type: MCP (Streamable HTTP)**, URL выше, Auth = Bearer с токеном. OWUI
в host-сети, так что `127.0.0.1:23120` доступен напрямую. Если сборка отвечает
`400 Bad Request` (open-webui#20500) — поставить рядом mcpo (MCP→OpenAPI
прокси) отдельным сервисом compose и подключить как OpenAPI-tool-server.

После подключения включить инструменты Zotero в настройках модели/чата —
агенты смогут и читать, и писать (`add_note`, `create_item`, `add_tags`,
коллекции, вложения).

## Источники для агентов

Всё через Zotero: источники и заметки кладёт в библиотеку штатный пайплайн
(`discover.py`, `ingest_sources.py`), агенты читают и пишут через Zotero MCP;
при нескольких серверах библиотеки сводит синхронизация (SETUP.md). Клоны
рабочих репозиториев не нужны; запасной вариант, если понадобится доступ к
сырым файлам, — mcpo + filesystem-MCP с репозиториями read-only в контейнере.
