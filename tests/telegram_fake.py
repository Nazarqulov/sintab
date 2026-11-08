"""Haqiqiy aiogram Dispatcher'ga update berib, bot yuborgan so'rovlarni tarmoqsiz yozib olish."""

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update
from aiogram.types import User as TgUser

_ids = itertools.count(1)


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:
        self.requests.append(method)
        if isinstance(method, SendMessage):
            return Message(
                message_id=next(_ids),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""

    async def close(self) -> None:
        pass

    def sent(self, chat_id: int | None = None) -> list[SendMessage]:
        return [m for m in self.requests if isinstance(m, SendMessage) and (chat_id is None or m.chat_id == chat_id)]

    def edits(self) -> list[EditMessageText]:
        return [m for m in self.requests if isinstance(m, EditMessageText)]

    def answers(self) -> list[AnswerCallbackQuery]:
        return [m for m in self.requests if isinstance(m, AnswerCallbackQuery)]

    def last_text(self) -> str:
        texts = [m.text for m in self.requests if isinstance(m, (SendMessage, EditMessageText))]
        return texts[-1] if texts else ""

    def clear(self) -> None:
        self.requests.clear()


def tg_user(user_id: int, username: str | None = None, name: str = "Test User") -> TgUser:
    first, _, last = name.partition(" ")
    return TgUser(id=user_id, is_bot=False, first_name=first, last_name=last or None, username=username, language_code="uz")


def message_update(
    user_id: int,
    text: str | None = None,
    *,
    contact: Contact | None = None,
    chat_id: int | None = None,
    chat_type: str = "private",
    name: str = "Test User",
    chat_title: str | None = None,
    thread_id: int | None = None,
) -> Update:
    message = Message(
        message_id=next(_ids),
        date=datetime.now(UTC),
        chat=Chat(id=chat_id if chat_id is not None else user_id, type=chat_type, title=chat_title),
        from_user=tg_user(user_id, name=name),
        text=text,
        contact=contact,
        message_thread_id=thread_id,
        is_topic_message=True if thread_id else None,
    )
    return Update(update_id=next(_ids), message=message)


def callback_update(user_id: int, data: str, *, chat_id: int | None = None) -> Update:
    message = Message(
        message_id=next(_ids),
        date=datetime.now(UTC),
        chat=Chat(id=chat_id if chat_id is not None else user_id, type="private"),
        text="...",
    )
    query = CallbackQuery(
        id=str(next(_ids)), from_user=tg_user(user_id), chat_instance="ci", data=data, message=message
    )
    return Update(update_id=next(_ids), callback_query=query)


def own_contact(user_id: int, phone: str = "+1 (312) 555-0101") -> Contact:
    return Contact(phone_number=phone, first_name="Test", user_id=user_id)
