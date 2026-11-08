from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from app.db.models import AlertDelivery, DeliveryStatus
from app.db.session import AsyncSessionLocal
from app.services.alerts.ingest import ingest_alert
from app.services.alerts.sender import AlertSender, MAX_ATTEMPTS
from tests.helpers import OWNER_CHAT_ID, FakeBot, add_group, add_rule, normalized

METHOD = SendMessage(chat_id=1, text="x")


def make_sender(bot: FakeBot, sleeps: list[float], clock_value: list[float] | None = None) -> AlertSender:
    clock_value = clock_value if clock_value is not None else [0.0]

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock_value[0] += seconds

    return AlertSender(bot, AsyncSessionLocal, "Asia/Tashkent", sleep=fake_sleep, clock=lambda: clock_value[0])


async def _delivery(session, delivery_id: int) -> AlertDelivery:
    session.expire_all()
    return await session.get(AlertDelivery, delivery_id)


async def test_successful_send_stores_message_id(session):
    group = await add_group(session, -100, topic_id=42)
    await add_rule(session, group)
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    bot = FakeBot()

    await make_sender(bot, []).process(result.delivery_ids[0])

    delivery = await _delivery(session, result.delivery_ids[0])
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.message_id == 501
    call = bot.sent[0]
    assert call["chat_id"] == -100 and call["message_thread_id"] == 42
    assert "CRITICAL" in call["text"] and "Truck 1" in call["text"]
    assert call["reply_markup"] is not None


async def test_already_sent_delivery_is_not_resent(session):
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    bot = FakeBot()
    sender = make_sender(bot, [])
    await sender.process(result.delivery_ids[0])
    await sender.process(result.delivery_ids[0])
    assert len(bot.sent) == 1


async def test_rate_limit_waits_and_retries(session):
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    bot = FakeBot(errors=[TelegramRetryAfter(method=METHOD, message="Too Many Requests", retry_after=7)])
    sleeps: list[float] = []

    await make_sender(bot, sleeps).process(result.delivery_ids[0])

    assert 7 in sleeps
    assert (await _delivery(session, result.delivery_ids[0])).status == DeliveryStatus.SENT


async def test_network_error_retries_with_backoff_then_fails(session):
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    errors = [TelegramNetworkError(method=METHOD, message="timeout") for _ in range(MAX_ATTEMPTS)]
    sleeps: list[float] = []

    await make_sender(FakeBot(errors=errors), sleeps).process(result.delivery_ids[0])

    delivery = await _delivery(session, result.delivery_ids[0])
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.attempts == MAX_ATTEMPTS
    assert [s for s in sleeps if s >= 2] == [2, 4, 8, 16]


async def test_forbidden_fails_immediately_without_retry(session):
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    bot = FakeBot(errors=[TelegramForbiddenError(method=METHOD, message="bot was kicked")])

    await make_sender(bot, []).process(result.delivery_ids[0])

    delivery = await _delivery(session, result.delivery_ids[0])
    assert delivery.status == DeliveryStatus.FAILED and "kicked" in delivery.last_error
    assert bot.sent == []


async def test_group_messages_are_spaced_to_respect_telegram_limits(session):
    group = await add_group(session, -100)
    await add_rule(session, group)
    first = await ingest_alert(session, normalized("a"), owner_chat_id=OWNER_CHAT_ID)
    second = await ingest_alert(session, normalized("b"), owner_chat_id=OWNER_CHAT_ID)
    sleeps: list[float] = []
    sender = make_sender(FakeBot(), sleeps)

    await sender.process(first.delivery_ids[0])
    await sender.process(second.delivery_ids[0])

    assert sleeps == [3.0]


async def test_start_requeues_pending_deliveries_after_restart(session):
    result = await ingest_alert(session, normalized(), owner_chat_id=OWNER_CHAT_ID)
    bot = FakeBot()
    sender = make_sender(bot, [])

    await sender.start()
    await sender._queue.join()
    await sender.stop()

    assert len(bot.sent) == 1
    assert (await _delivery(session, result.delivery_ids[0])).status == DeliveryStatus.SENT
