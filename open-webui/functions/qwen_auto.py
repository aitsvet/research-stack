"""
title: Qwen Auto
author: local
version: 0.6.0
description: Один чат — три роли. Текст и картинка на входе идут в TEXT_MODEL и
    VISION_MODEL, а рисование и правку изображений модель вызывает сама как
    инструменты (IMAGE_MODEL через нативный DashScope API).
    Имена моделей не вбиты в код: они в валвах, потому что каталог моделей —
    свойство эндпоинта, а не аккаунта, и при переезде меняется целиком.
    Внешние инструменты OWUI (Zotero, Playwright, Terminal) доступны через
    native function calling — модель сама решает, когда их вызвать.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

NATIVE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"

# Один хост на оба API: и OpenAI-совместимый, и нативный (image-модели живут
# только в нём). Раньше адрес был записан в двух валвах по отдельности, и при
# переезде одну из них забывали — пайп отвечал текстом, но переставал рисовать.
DEFAULT_HOST = "https://token-plan.ap-southeast-1.maas.aliyuncs.com"

MAX_TOOL_ROUNDS = 16


class Pipe:
    class Valves(BaseModel):
        DASHSCOPE_API_KEY: str = Field(
            default=os.getenv("DASHSCOPE_API_KEY", ""),
            description="Ключ Model Studio; по умолчанию — из окружения контейнера",
        )
        COMPAT_BASE_URL: str = Field(
            default=os.getenv("DASHSCOPE_BASE_URL", DEFAULT_HOST + "/compatible-mode/v1"),
            description="OpenAI-совместимый эндпоинт для текстовых и VL-моделей",
        )
        NATIVE_BASE_URL: str = Field(
            default=os.getenv("DASHSCOPE_NATIVE_URL", DEFAULT_HOST),
            description="Корень нативного API для image-моделей (без /api/v1)",
        )
        # Проверено на этом эндпоинте: 3.8 переписывает картинку дословно —
        # сохраняет опечатку, транслит и апостроф. qwen3.6-flash дешевле и
        # быстрее, но «закят» у него превращается в «закат»: для расшифровки
        # доски правдоподобная незаметная подмена хуже явной ошибки.
        TEXT_MODEL: str = Field(default=os.getenv("QWEN_TEXT_MODEL", "qwen3.8-max-preview"))
        VISION_MODEL: str = Field(
            default=os.getenv("QWEN_VISION_MODEL", "qwen3.8-max-preview")
        )
        # Рисование и правка — одна и та же модель: wan2.7 принимает и голый
        # текст, и «картинка + указание». Валва две, потому что роли разные и
        # правку не жалко увести на -pro, но по умолчанию они совпадают.
        IMAGE_MODEL: str = Field(default=os.getenv("QWEN_IMAGE_MODEL", "wan2.7-image"))
        IMAGE_EDIT_MODEL: str = Field(
            default=os.getenv("QWEN_IMAGE_EDIT_MODEL", "wan2.7-image")
        )
        IMAGE_SIZE: str = Field(default="1328*1328")
        PERSIST_IMAGES: bool = Field(
            default=True,
            description="Складывать результат в файловое хранилище Open WebUI.",
        )
        WATERMARK: bool = Field(default=False)
        TIMEOUT: int = Field(default=300)
        DEBUG: bool = Field(
            default=os.getenv("QWEN_DEBUG", "").lower() in ("1", "true", "yes"),
            description="Печатать в лог контейнера отладочную информацию.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _dbg(self, *parts: Any) -> None:
        if self.valves.DEBUG:
            log.info("QWEN_AUTO " + " ".join(str(p) for p in parts))

    @staticmethod
    def _shape(messages: list) -> str:
        rows = []
        for m in messages:
            role = m.get("role")
            c = m.get("content")
            if isinstance(c, list):
                kinds = []
                for p in c:
                    tp = p.get("type")
                    if tp == "image_url":
                        u = (p.get("image_url") or {}).get("url", "")
                        head = "data-uri" if u.startswith("data:") else u[:40]
                        kinds.append(f"image_url({len(u)}b,{head})")
                    elif tp == "text":
                        kinds.append(f"text({len(p.get('text',''))}b)")
                    else:
                        kinds.append(str(tp))
                desc = "list[" + ", ".join(kinds) + "]"
            else:
                desc = f"str({len(c or '')}b)"
            files = m.get("files")
            if files:
                fkinds = [
                    f.get("type") or (f.get("content_type") or "?") for f in files
                ]
                desc += f" +files{fkinds}"
            rows.append(f"{role}:{desc}")
        return " | ".join(rows)

    TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "generate_image",
                "description": (
                    "Нарисовать новое изображение по текстовому описанию. "
                    "Вызывать, когда пользователь просит нарисовать, сгенерировать, "
                    "создать картинку/иллюстрацию/логотип."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "Подробное описание сцены на английском "
                            "или на языке пользователя.",
                        }
                    },
                    "required": ["prompt"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "edit_image",
                "description": (
                    "Изменить изображение, которое пользователь прислал или которое "
                    "было сгенерировано ранее в этом чате: заменить объект, поменять "
                    "цвет/фон/стиль, дорисовать или убрать деталь. "
                    "Не вызывать, если пользователь просто спрашивает, что на картинке."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instruction": {
                            "type": "string",
                            "description": "Что именно поменять.",
                        }
                    },
                    "required": ["instruction"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": (
                    "Поиск в интернете (несколько запросов за один вызов). "
                    "ОБЯЗАТЕЛЬНО вызывать при любых вопросах о реальных событиях, "
                    "расписаниях, адресах, ценах, контактах, новостях. "
                    "Не придумывать факты — сначала искать."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 2,
                            "maxItems": 4,
                            "description": (
                                "2–4 РАЗНЫХ запроса: разные языки и формулировки. "
                                "Минимум один запрос обязан быть СМЕШАННЫМ: точное "
                                "название/аббревиатура из вопроса ДОСЛОВНО (не "
                                "переводить и не транслитерировать), а город и "
                                "остальные слова — на языке региона (для России "
                                "кириллицей). Пример формы: «<Название как в "
                                "вопросе> <город по-местному> <тема по-местному>»."
                            ),
                        }
                    },
                    "required": ["queries"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_url",
                "description": (
                    "Загрузить и вернуть текст веб-страницы по URL. "
                    "Использовать когда search_web дал ссылку и нужен "
                    "конкретный контент со страницы."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL страницы.",
                        }
                    },
                    "required": ["url"],
                },
            },
        },
    ]

    SEARXNG_URL = os.getenv(
        "SEARXNG_QUERY_URL", "http://127.0.0.1:9090/search"
    )

    UA = (
        "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) "
        "Gecko/20100101 Firefox/140.0"
    )

    @staticmethod
    def _html_to_text(html_src: str, base_url: str = "") -> str:
        import html as html_mod
        from urllib.parse import urljoin

        text = re.sub(
            r"(?is)<(script|style|noscript|svg|head|template)[^>]*>.*?</\1>",
            " ", html_src,
        )
        text = re.sub(r"(?is)<!--.*?-->", " ", text)

        def _link(m: "re.Match") -> str:
            href, inner = m.group(1), m.group(2)
            label = re.sub(r"<[^>]+>", " ", inner)
            label = re.sub(r"\s+", " ", label).strip()
            if not label or href.startswith(("javascript:", "mailto:", "#")):
                return label
            return f"{label} [{urljoin(base_url, href)}]"

        text = re.sub(
            r'(?is)<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', _link, text
        )
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(
            r"(?i)</(p|div|li|tr|h[1-6]|table|section|article|ul|ol|dd|dt)>",
            "\n", text,
        )
        text = re.sub(r"(?i)<t[dh][^>]*>", " | ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html_mod.unescape(text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.valves.DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _has_image(messages: list) -> bool:
        return any(
            isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )

    @staticmethod
    def _last_image(messages: list) -> Optional[str]:
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for part in reversed(content):
                    if part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url")
                        if url:
                            return url
            elif isinstance(content, str):
                found = re.findall(
                    r"!\[[^\]]*\]\((data:image/[^)]+|https?://[^)]+|/api/v1/files/[^)]+)\)",
                    content,
                )
                if found:
                    return found[-1]
        return None

    async def _resolve(self, source: str) -> Optional[str]:
        if not source.startswith("/api/v1/files/"):
            return source
        try:
            import base64
            from open_webui.models.files import Files
            from open_webui.storage.provider import Storage

            file_id = source.split("/api/v1/files/")[1].split("/")[0]
            file = await Files.get_file_by_id(file_id)
            if not file:
                return None
            path = await asyncio.to_thread(Storage.get_file, file.path)
            raw = await asyncio.to_thread(lambda: open(path, "rb").read())
            mime = (file.meta or {}).get("content_type", "image/png")
            return f"data:{mime};base64,{base64.b64encode(raw).decode()}"
        except Exception:
            log.exception("resolve: failed to read image from storage")
            return None

    async def _store(self, raw: bytes, mime: str, user_id: str) -> Optional[str]:
        try:
            import io
            import uuid
            from open_webui.models.files import FileForm, Files
            from open_webui.storage.provider import Storage

            file_id = str(uuid.uuid4())
            ext = "jpg" if "jpeg" in mime or "jpg" in mime else "png"
            filename = f"{file_id}.{ext}"

            _, path = await asyncio.to_thread(
                Storage.upload_file, io.BytesIO(raw), filename,
                {"OpenWebUI-User-Id": user_id},
            )
            await Files.insert_new_file(
                user_id,
                FileForm(
                    id=file_id, filename=filename, path=path,
                    meta={"name": filename, "content_type": mime, "size": len(raw)},
                ),
            )
            return f"/api/v1/files/{file_id}/content"
        except Exception:
            log.exception("store: failed to save image")
            return None

    async def _render(
        self, client: httpx.AsyncClient, model: str, content: list,
        user_id: Optional[str] = None, size: Optional[str] = None,
    ) -> str:
        """Нативный вызов image-модели. `size` задаётся только при рисовании.

        Раньше признаком «это рисование» было совпадение имени модели с
        IMAGE_MODEL. Пока модели рисования и правки были разные, это работало;
        теперь обе — wan2.7, и правка молча получала бы навязанный размер
        холста вместо размера исходной картинки.
        """
        body = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"watermark": self.valves.WATERMARK},
        }
        if size:
            body["parameters"]["size"] = size

        resp = await client.post(
            self.valves.NATIVE_BASE_URL.rstrip("/") + NATIVE_PATH,
            headers=self._headers(), json=body,
        )
        self._dbg("native", model, "→", resp.status_code)
        if resp.status_code != 200:
            self._dbg("native ошибка:", resp.text[:300])
            return f"\n\n> Ошибка {model}: `{resp.status_code}` {resp.text[:300]}\n"

        parts = resp.json()["output"]["choices"][0]["message"]["content"]
        url = next((p["image"] for p in parts if "image" in p), None)
        if not url:
            return f"\n\n> {model} не вернул изображение.\n"

        if self.valves.PERSIST_IMAGES and user_id:
            got = await client.get(url)
            mime = got.headers.get("content-type", "image/png").split(";")[0]
            stored = await self._store(got.content, mime, user_id)
            if stored:
                url = stored
        return f"\n\n![{model}]({url})\n"

    async def _searx(self, client: httpx.AsyncClient, query: str) -> list:
        params = {"q": query, "format": "json", "language": "auto"}
        r = await client.get(self.SEARXNG_URL, params=params, timeout=15)
        items = r.json().get("results", [])
        if not items:
            params["engines"] = "bing"
            r = await client.get(self.SEARXNG_URL, params=params, timeout=15)
            items = r.json().get("results", [])
        return items

    @staticmethod
    def _collect_tool_calls(delta: dict, calls: dict) -> None:
        for tc in delta.get("tool_calls") or []:
            slot = calls.setdefault(
                tc.get("index", 0), {"name": "", "arguments": "", "tool_call_id": ""}
            )
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]
            if tc.get("id"):
                slot["tool_call_id"] = tc["id"]

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __tools__: Optional[dict] = None,
        __task__: Optional[str] = None,
    ) -> Any:
        if not self.valves.DASHSCOPE_API_KEY:
            yield "Не задан DASHSCOPE_API_KEY в Valves этой функции."
            return

        user_id = (__user__ or {}).get("id")
        messages = body.get("messages", [])

        # Служебные вызовы OWUI (заголовок чата, теги, follow-up) — обычное
        # одноразовое дополнение без инструментов и без веб-поиска.
        if __task__:
            self._dbg("task-вызов:", __task__, "— без инструментов")
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
                resp = await client.post(
                    self.valves.COMPAT_BASE_URL.rstrip("/") + "/chat/completions",
                    headers=self._headers(),
                    json={
                        "model": self.valves.TEXT_MODEL,
                        "messages": messages,
                        "stream": False,
                    },
                )
                if resp.status_code == 200:
                    yield resp.json()["choices"][0]["message"]["content"] or ""
                else:
                    yield ""
            return

        self._dbg("=== вход ===")
        self._dbg("ключи body:", sorted(body.keys()))
        self._dbg("сообщений:", len(messages), "| схема:", self._shape(messages))
        self._dbg("__tools__:", list((__tools__ or {}).keys()))
        self._dbg("body.tools:", len(body.get("tools") or []))

        has_img = self._has_image(messages)
        router = self.valves.VISION_MODEL if has_img else self.valves.TEXT_MODEL
        self._dbg("картинка во вложении:", has_img, "→ маршрут:", router)

        async def status(text: str, done: bool = False):
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "status", "data": {"description": text, "done": done}}
                )

        own_names = {t["function"]["name"] for t in self.TOOLS}

        all_tools = list(self.TOOLS)
        external_specs = body.get("tools") or []

        if not external_specs and __tools__:
            for tname, tinfo in __tools__.items():
                spec = tinfo.get("spec", {})
                if spec and tname not in own_names:
                    all_tools.append({"type": "function", "function": spec})

        if external_specs:
            for t in external_specs:
                fname = t.get("function", {}).get("name", "")
                if fname and fname not in own_names:
                    all_tools.append(t)

        self._dbg(
            f"merged tools: {len(all_tools)} total "
            f"({len(self.TOOLS)} own + {len(all_tools) - len(self.TOOLS)} external)"
        )

        SYSTEM_PROMPT = (
            "Ты — универсальный ассистент с инструментами: веб-поиск, загрузка "
            "страниц, браузер, терминал, генерация/правка изображений.\n"
            "1. Вопросы о реальном мире (факты, цены, расписания, адреса, "
            "наличие, контакты, новости) — сначала search_web с 2–4 разными "
            "запросами (разные языки и формулировки). Не отвечай из памяти и "
            "не выдумывай.\n"
            "2. Точные названия, аббревиатуры и имена из вопроса пиши в "
            "запросах ДОСЛОВНО, без перевода и транслитерации; остальные слова "
            "запроса — на языке региона темы (смешанный алфавит — норма). "
            "Похожее название или другая аббревиатура — ДРУГАЯ сущность: в "
            "источнике название должно буквально совпадать со спрошенным, "
            "иначе ищи дальше или прямо скажи, что нашёл только похожее.\n"
            "3. Сниппеты поиска — наводка, не ответ: открой 1–3 лучшие ссылки "
            "через fetch_url. Ссылки в тексте страниц даны в [квадратных "
            "скобках] — по ним можно переходить дальше.\n"
            "4. Просят полный список/расписание/подборку — найди "
            "страницу-каталог, перечисли её пункты и открой каждый релевантный; "
            "однотипные страницы запрашивай параллельно (несколько fetch_url в "
            "одном раунде); перед ответом проверь, что охватил всё.\n"
            "5. Просят ссылки на товары/страницы — давай только URL, которые "
            "реально видел в результатах поиска или на страницах; ссылки не "
            "сочиняй никогда.\n"
            "6. Оценки стоимости/бюджета — собери цифры из нескольких "
            "источников, дай разбивку по статьям и итоговый диапазон.\n"
            "7. Если fetch_url вернул мало текста или ошибку (JS-сайт, "
            "защита) — используй браузерные инструменты (navigate + snapshot) "
            "или терминал (python/curl), если они доступны.\n"
            "8. Вопросы-инструкции (как сделать X, напиши код) — отвечай по "
            "существу шагами и кодом; веб подключай для актуальных деталей "
            "(цены, версии, ссылки).\n"
            "9. Отвечай на языке пользователя, структурировано; в конце — URL "
            "источников. Чего нет в источниках — того нет в ответе; не нашёл — "
            "так и скажи.\n"
            "10. Изображения генерируй/правь ТОЛЬКО по прямой просьбе "
            "(generate_image / edit_image)."
        )

        async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
            await status(f"Маршрут: {router}")

            working_messages = list(messages)
            if not working_messages or working_messages[0].get("role") != "system":
                working_messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
            else:
                first = dict(working_messages[0])
                first["content"] = SYSTEM_PROMPT + "\n\n" + (first.get("content") or "")
                working_messages[0] = first
            tool_results_log = []
            image_mds = []

            for round_idx in range(MAX_TOOL_ROUNDS):
                payload = {
                    "model": router,
                    "messages": working_messages,
                    "tools": all_tools,
                    "parallel_tool_calls": True,
                    "stream": True,
                }
                for key in ("temperature", "top_p", "max_tokens"):
                    if body.get(key) is not None:
                        payload[key] = body[key]

                calls: dict[int, dict] = {}
                assistant_content = ""

                async with client.stream(
                    "POST",
                    self.valves.COMPAT_BASE_URL.rstrip("/") + "/chat/completions",
                    headers=self._headers(), json=payload,
                ) as resp:
                    self._dbg(f"DashScope → {resp.status_code} (round {round_idx})")
                    if resp.status_code != 200:
                        detail = (await resp.aread()).decode(errors="replace")[:400]
                        await status("", done=True)
                        yield f"Ошибка {router}: {resp.status_code} {detail}"
                        return

                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if not chunk or chunk == "[DONE]":
                            continue
                        try:
                            parsed = json.loads(chunk)
                            delta = parsed["choices"][0]["delta"]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue

                        if delta.get("content"):
                            assistant_content += delta["content"]
                            yield delta["content"]

                        self._collect_tool_calls(delta, calls)

                if not calls:
                    break

                call_names = [s["name"] for s in calls.values()]
                self._dbg(f"round {round_idx}: {len(calls)} calls:", {k: {"name": v["name"], "args": v.get("arguments","")[:300]} for k,v in calls.items()})
                await status(f"Инструменты: {', '.join(call_names)} (раунд {round_idx + 1})")

                assistant_msg = {
                    "role": "assistant",
                    "content": assistant_content or None,
                    "tool_calls": [
                        {
                            "id": s.get("tool_call_id") or f"call_{round_idx}_{s['name']}",
                            "type": "function",
                            "function": {"name": s["name"], "arguments": s["arguments"]},
                        }
                        for s in calls.values()
                    ],
                }
                working_messages.append(assistant_msg)

                for slot in calls.values():
                    result = None

                    if slot["name"] in own_names:
                        try:
                            args = json.loads(slot["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {}

                        if slot["name"] == "generate_image":
                            self._dbg("generate_image:", args.get("prompt", "")[:80])
                            await status(f"Рисую: {self.valves.IMAGE_MODEL}")
                            result = await self._render(
                                client, self.valves.IMAGE_MODEL,
                                [{"text": args.get("prompt", "")}], user_id,
                                size=self.valves.IMAGE_SIZE,
                            )
                            if result and "![" in result:
                                image_mds.append(result)
                                yield result
                                result = (
                                    "Изображение сгенерировано и уже показано "
                                    "пользователю. Ссылку повторно НЕ вставляй — "
                                    "просто кратко прокомментируй результат."
                                )
                        elif slot["name"] == "edit_image":
                            source = self._last_image(messages)
                            if source:
                                source = await self._resolve(source)
                            if not source:
                                result = "\n\n> Нечего править: в этом чате нет изображения.\n"
                            else:
                                await status(f"Правлю: {self.valves.IMAGE_EDIT_MODEL}")
                                result = await self._render(
                                    client, self.valves.IMAGE_EDIT_MODEL,
                                    [{"image": source}, {"text": args.get("instruction", "")}],
                                    user_id,
                                )
                                if result and "![" in result:
                                    image_mds.append(result)
                                    yield result
                                    result = (
                                        "Изображение отредактировано и уже "
                                        "показано пользователю. Ссылку повторно "
                                        "НЕ вставляй — просто кратко "
                                        "прокомментируй результат."
                                    )

                        elif slot["name"] == "search_web":
                            queries = args.get("queries") or []
                            if isinstance(queries, str):
                                queries = [queries]
                            if args.get("query"):
                                queries.append(args["query"])
                            queries = [
                                q.strip() for q in queries
                                if isinstance(q, str) and q.strip()
                            ][:4]
                            self._dbg("search_web:", " | ".join(queries)[:200])
                            await status(f"Ищу: {'; '.join(queries)[:60]}")
                            try:
                                seen: set = set()
                                blocks = []
                                for q in queries:
                                    items = await self._searx(client, q)
                                    lines = []
                                    for it in items[:8]:
                                        url = it.get("url", "")
                                        if not url or url in seen:
                                            continue
                                        seen.add(url)
                                        title = it.get("title", "")
                                        snippet = it.get("content", "")[:200]
                                        lines.append(f"- **{title}**\n  {url}\n  {snippet}")
                                    blocks.append(
                                        f"### {q}\n" + ("\n\n".join(lines) or "(ничего нового)")
                                    )
                                result = "\n\n".join(blocks) if seen else (
                                    "Ничего не найдено ни по одному запросу — "
                                    "попробуй другие формулировки или языки."
                                )
                            except Exception as e:
                                result = f"Ошибка поиска: {e}"

                        elif slot["name"] == "fetch_url":
                            url = args.get("url", "")
                            self._dbg("fetch_url:", url[:100])
                            await status(f"Загружаю: {url[:60]}")
                            try:
                                r = await client.get(
                                    url, timeout=25, follow_redirects=True,
                                    headers={"User-Agent": self.UA},
                                )
                                ctype = r.headers.get("content-type", "")
                                text = r.text
                                if "html" in ctype or text.lstrip()[:1] == "<":
                                    text = self._html_to_text(text, str(r.url))
                                if len(text) > 12000:
                                    text = text[:12000] + "\n\n[...обрезано...]"
                                result = (
                                    f"[{r.status_code}] {str(r.url)}\n\n{text}"
                                    if text.strip()
                                    else f"[{r.status_code}] {str(r.url)} — пустая страница"
                                )
                            except Exception as e:
                                result = f"Ошибка загрузки: {e}"

                    elif __tools__ and slot["name"] in __tools__:
                        try:
                            args = json.loads(slot["arguments"] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        self._dbg(f"external: {slot['name']}(" + str({k: str(v)[:120] for k,v in args.items()}) + ")")
                        try:
                            raw_result = await __tools__[slot["name"]]["callable"](**args)
                            if isinstance(raw_result, str):
                                result = raw_result
                            elif isinstance(raw_result, dict):
                                result = json.dumps(raw_result, ensure_ascii=False, indent=2)
                            else:
                                result = str(raw_result)
                        except Exception as e:
                            self._dbg(f"external error: {slot['name']}: {e}")
                            result = f"Ошибка инструмента {slot['name']}: {e}"

                    if result is None:
                        result = f"Инструмент '{slot['name']}' не найден."

                    tool_results_log.append(f"[{slot['name']}]: {str(result)[:200]}")
                    self._dbg(f"result [{slot['name']}]: {str(result)[:400]}")
                    tc_id = slot.get("tool_call_id") or f"call_{round_idx}_{slot['name']}"
                    working_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": result[:8000] if isinstance(result, str) else str(result)[:8000],
                    })

            else:
                # Раунды исчерпаны, а модель всё ещё просит инструменты —
                # финальный ответ без tools по уже собранным данным.
                self._dbg("лимит раундов исчерпан — финальный ответ без tools")
                await status("Собираю финальный ответ…")
                working_messages.append({
                    "role": "user",
                    "content": (
                        "Лимит вызовов инструментов исчерпан. Дай финальный "
                        "ответ по уже собранным данным, честно отметив, "
                        "чего не хватило."
                    ),
                })
                final_payload = {
                    "model": router,
                    "messages": working_messages,
                    "stream": True,
                }
                async with client.stream(
                    "POST",
                    self.valves.COMPAT_BASE_URL.rstrip("/") + "/chat/completions",
                    headers=self._headers(), json=final_payload,
                ) as resp:
                    if resp.status_code == 200:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            chunk = line[5:].strip()
                            if not chunk or chunk == "[DONE]":
                                continue
                            try:
                                delta = json.loads(chunk)["choices"][0]["delta"]
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue
                            if delta.get("content"):
                                yield delta["content"]

            if tool_results_log:
                self._dbg(f"инструменты выполнены: {len(tool_results_log)} раундов")
            self._dbg("=== конец ===")
            await status("", done=True)



