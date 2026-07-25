"""
title: Qwen Auto
author: local
version: 0.3.0
description: Один чат — четыре модели. Текст идёт в qwen3-max, картинка на входе в
    qwen3-vl, а рисование и правку картинок модель вызывает сама как инструменты
    (qwen-image / qwen-image-edit через нативный DashScope API).
"""

# httpx уже входит в backend/requirements.txt самого Open WebUI, поэтому строки
# `requirements:` во фронтматтере нет — иначе загрузка функции дёргала бы pip.

import asyncio
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Optional

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# Нативный (не OpenAI-совместимый) эндпоинт: image-модели Model Studio живут только
# здесь, в compatible-mode они отвечают пустым content.
NATIVE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"


class Pipe:
    class Valves(BaseModel):
        # Значения по умолчанию берутся из окружения контейнера, поэтому ключ
        # задаётся в docker-compose и не хранится в тексте функции. Всё, что
        # позже поменяют в UI, ложится в БД и перекрывает эти значения.
        DASHSCOPE_API_KEY: str = Field(
            default=os.getenv("DASHSCOPE_API_KEY", ""),
            description="Ключ Model Studio; по умолчанию — из окружения контейнера",
        )
        COMPAT_BASE_URL: str = Field(
            default=os.getenv(
                "DASHSCOPE_BASE_URL",
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            ),
            description="OpenAI-совместимый эндпоинт для текстовых и VL-моделей",
        )
        NATIVE_BASE_URL: str = Field(
            default=os.getenv(
                "DASHSCOPE_NATIVE_URL", "https://dashscope-intl.aliyuncs.com"
            ),
            description="Корень нативного API для image-моделей (без /api/v1)",
        )
        TEXT_MODEL: str = Field(default=os.getenv("QWEN_TEXT_MODEL", "qwen3-max"))
        VISION_MODEL: str = Field(default=os.getenv("QWEN_VISION_MODEL", "qwen3-vl-plus"))
        IMAGE_MODEL: str = Field(default=os.getenv("QWEN_IMAGE_MODEL", "qwen-image-2.0"))
        IMAGE_EDIT_MODEL: str = Field(
            default=os.getenv("QWEN_IMAGE_EDIT_MODEL", "qwen-image-edit-plus")
        )
        IMAGE_SIZE: str = Field(default="1328*1328")
        ENABLE_SEARCH: bool = Field(
            default=os.getenv("QWEN_ENABLE_SEARCH", "true").lower()
            in ("1", "true", "yes"),
            description="Родной веб-поиск DashScope для текстовой ветки "
            "(enable_search). Не требует стороннего API.",
        )
        PERSIST_IMAGES: bool = Field(
            default=True,
            description="Складывать результат в файловое хранилище Open WebUI и "
            "отдавать короткую внутреннюю ссылку. Выключить — в чат уйдёт прямая "
            "ссылка на OSS, она протухает примерно через неделю.",
        )
        WATERMARK: bool = Field(default=False)
        TIMEOUT: int = Field(default=300)

    def __init__(self):
        self.valves = self.Valves()

    # ---------- инструменты, которые видит роутер ----------

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
    ]

    # ---------- вспомогательное ----------

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
        """Последняя картинка в диалоге: вложение пользователя или наш же результат."""
        for msg in reversed(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for part in reversed(content):
                    if part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url")
                        if url:
                            return url
            elif isinstance(content, str):
                # Внутренняя ссылка на хранилище относительная, поэтому шаблон
                # обязан ловить и её — иначе «поменяй фон» после генерации не
                # найдёт, что именно править.
                found = re.findall(
                    r"!\[[^\]]*\]\((data:image/[^)]+|https?://[^)]+|/api/v1/files/[^)]+)\)",
                    content,
                )
                if found:
                    return found[-1]
        return None

    async def _resolve(self, source: str) -> Optional[str]:
        """Внутреннюю ссылку на хранилище разворачивает обратно в data-URI.

        DashScope такую ссылку не заберёт — она относительная и закрыта авторизацией,
        а data-URI на вход он принимает.
        """
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
            log.exception("не удалось прочитать картинку из хранилища Open WebUI")
            return None

    async def _store(self, raw: bytes, mime: str, user_id: str) -> Optional[str]:
        """Кладёт картинку в хранилище Open WebUI и отдаёт короткую ссылку.

        Вставлять картинку в ответ как data-URI не стоит: она уезжает в поток
        одним куском на пару мегабайт и вдобавок перегружает историю чата.
        Прямая OSS-ссылка от DashScope живёт лишь около недели. Внутренняя
        ссылка занимает полсотни байт и не протухает.
        """
        try:
            import io
            import uuid

            from open_webui.models.files import FileForm, Files
            from open_webui.storage.provider import Storage

            file_id = str(uuid.uuid4())
            ext = "jpg" if "jpeg" in mime or "jpg" in mime else "png"
            filename = f"{file_id}.{ext}"

            _, path = await asyncio.to_thread(
                Storage.upload_file,
                io.BytesIO(raw),
                filename,
                {"OpenWebUI-User-Id": user_id},
            )
            await Files.insert_new_file(
                user_id,
                FileForm(
                    id=file_id,
                    filename=filename,
                    path=path,
                    meta={"name": filename, "content_type": mime, "size": len(raw)},
                ),
            )
            return f"/api/v1/files/{file_id}/content"
        except Exception:
            # Версии Open WebUI расходятся по этим внутренним API, поэтому отказ
            # не должен ронять ответ — просто отдадим прямую ссылку.
            log.exception("не удалось сохранить картинку в хранилище Open WebUI")
            return None

    async def _render(
        self,
        client: httpx.AsyncClient,
        model: str,
        content: list,
        user_id: Optional[str] = None,
    ) -> str:
        """Дёргает нативный эндпоинт и отдаёт готовый markdown с картинкой."""
        body = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"watermark": self.valves.WATERMARK},
        }
        if model == self.valves.IMAGE_MODEL:
            body["parameters"]["size"] = self.valves.IMAGE_SIZE

        resp = await client.post(
            self.valves.NATIVE_BASE_URL.rstrip("/") + NATIVE_PATH,
            headers=self._headers(),
            json=body,
        )
        if resp.status_code != 200:
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

    # ---------- основной поток ----------

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> Any:
        """Асинхронный генератор: отдаёт ответ кусками по мере поступления.

        Open WebUI заворачивает каждое значение из генератора в отдельное
        SSE-событие, поэтому стриминг даёт привычный эффект печатающегося
        текста и не собирает длинный ответ в один огромный кусок.
        """
        if not self.valves.DASHSCOPE_API_KEY:
            yield "Не задан DASHSCOPE_API_KEY в Valves этой функции."
            return

        user_id = (__user__ or {}).get("id")
        messages = body.get("messages", [])

        # Единственная детерминированная развилка: есть вложенная картинка —
        # разговор ведёт VL-модель, иначе текстовая. Рисование и правку модель
        # инициирует сама через вызов инструментов.
        router = (
            self.valves.VISION_MODEL
            if self._has_image(messages)
            else self.valves.TEXT_MODEL
        )

        async def status(text: str, done: bool = False):
            if __event_emitter__:
                await __event_emitter__(
                    {"type": "status", "data": {"description": text, "done": done}}
                )

        async with httpx.AsyncClient(timeout=self.valves.TIMEOUT) as client:
            await status(f"Маршрут: {router}")
            payload = {
                "model": router,
                "messages": messages,
                "tools": self.TOOLS,
                "stream": True,
            }
            # Родной веб-поиск DashScope — только для текстовой ветки; на VL и
            # при вызове инструментов он не нужен.
            if self.valves.ENABLE_SEARCH and router == self.valves.TEXT_MODEL:
                payload["enable_search"] = True
            for key in ("temperature", "top_p", "max_tokens"):
                if body.get(key) is not None:
                    payload[key] = body[key]

            # Вызовы инструментов приходят по частям и собираются по индексу:
            # имя обычно в первом фрагменте, аргументы дописываются кусками.
            calls: dict[int, dict] = {}

            async with client.stream(
                "POST",
                self.valves.COMPAT_BASE_URL.rstrip("/") + "/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
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
                        delta = json.loads(chunk)["choices"][0]["delta"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

                    if delta.get("content"):
                        yield delta["content"]

                    for tc in delta.get("tool_calls") or []:
                        slot = calls.setdefault(
                            tc.get("index", 0), {"name": "", "args": ""}
                        )
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]

            for slot in calls.values():
                try:
                    args = json.loads(slot["args"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                if slot["name"] == "generate_image":
                    await status(f"Рисую: {self.valves.IMAGE_MODEL}")
                    yield await self._render(
                        client,
                        self.valves.IMAGE_MODEL,
                        [{"text": args.get("prompt", "")}],
                        user_id,
                    )

                elif slot["name"] == "edit_image":
                    source = self._last_image(messages)
                    if source:
                        source = await self._resolve(source)
                    if not source:
                        yield "\n\n> Нечего править: в этом чате нет изображения.\n"
                        continue
                    await status(f"Правлю: {self.valves.IMAGE_EDIT_MODEL}")
                    yield await self._render(
                        client,
                        self.valves.IMAGE_EDIT_MODEL,
                        [{"image": source}, {"text": args.get("instruction", "")}],
                        user_id,
                    )

            await status("", done=True)
