import asyncio
import logging
import threading

import pytest_asyncio

from app.alerts import AdminAlertHandler, notify_started

LOGGER = "polny_zachet.test_alerts"


class FakeBot:
    def __init__(self, fail_for=(), log_error_on_send=False):
        self.sent: list[tuple[int, str]] = []
        self.fail_for = set(fail_for)
        self.log_error_on_send = log_error_on_send

    async def send_message(self, chat_id, text):
        if self.log_error_on_send:  # как aiogram, который при сетевой ошибке пишет ERROR в лог
            logging.getLogger("aiogram.client").error("сеть недоступна")
        if chat_id in self.fail_for:
            raise RuntimeError("Forbidden: bot was blocked")
        self.sent.append((chat_id, text))


@pytest_asyncio.fixture
async def setup():
    """(bot, handler, logger); окно 0.15 с вместо 5 минут."""
    bot = FakeBot()
    handler = AdminAlertHandler(bot, [1, 2], asyncio.get_running_loop(), window=0.15)
    logger = logging.getLogger(LOGGER)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield bot, handler, logger
    logger.removeHandler(handler)


async def settle(handler):
    await asyncio.sleep(0.02)
    await handler.drain()


async def test_error_goes_to_every_admin(setup):
    bot, handler, logger = setup
    logger.error("Что-то сломалось")
    await settle(handler)
    assert sorted(c for c, _ in bot.sent) == [1, 2]
    assert "Что-то сломалось" in bot.sent[0][1] and LOGGER in bot.sent[0][1]


async def test_lower_levels_are_not_sent(setup):
    bot, handler, logger = setup
    logger.info("инфо")
    logger.warning("предупреждение")
    await settle(handler)
    assert bot.sent == []


async def test_critical_is_sent(setup):
    bot, handler, logger = setup
    logger.critical("совсем плохо")
    await settle(handler)
    assert len(bot.sent) == 2


async def test_exception_type_and_text_included_html_escaped(setup):
    bot, handler, logger = setup
    try:
        raise ValueError("<плохо>")
    except ValueError:
        logger.exception("Ошибка в обработчике")
    await settle(handler)
    text = bot.sent[0][1]
    assert "ValueError: &lt;плохо&gt;" in text and "Traceback" not in text


async def test_identical_errors_sent_once_per_window_then_summary(setup):
    bot, handler, logger = setup
    for _ in range(5):
        logger.error("Одна и та же ошибка")
    await settle(handler)
    assert len(bot.sent) == 2  # только первая, двум админам
    await asyncio.sleep(0.25)  # окно закончилось — приходит сводка
    await handler.drain()
    summary = [t for _, t in bot.sent[2:]]
    assert len(summary) == 2 and "ещё 4 раз" in summary[0] and "Одна и та же ошибка" in summary[0]


async def test_error_after_window_without_repeats_sent_again_no_summary(setup):
    bot, handler, logger = setup
    logger.error("Редкая ошибка")
    await settle(handler)
    await asyncio.sleep(0.2)
    logger.error("Редкая ошибка")
    await settle(handler)
    assert len(bot.sent) == 4 and not any("повторилась" in t for _, t in bot.sent)


async def test_parametrized_messages_count_as_same_error(setup):
    bot, handler, logger = setup
    logger.error("Сбой %d", 1)
    logger.error("Сбой %d", 2)
    await settle(handler)
    assert len(bot.sent) == 2


async def test_different_errors_are_not_merged(setup):
    bot, handler, logger = setup
    logger.error("Первая")
    logger.error("Вторая")
    try:
        raise KeyError("x")
    except KeyError:
        logger.error("Первая", exc_info=True)  # то же сообщение, другой тип исключения
    await settle(handler)
    assert len(bot.sent) == 6


async def test_send_failure_does_not_raise_or_stop_other_admins(setup, caplog):
    bot, handler, logger = setup
    bot.fail_for = {1}
    logger.error("Ошибка")
    await settle(handler)
    assert [c for c, _ in bot.sent] == [2]  # второй админ получил, первый — сбой тихо


async def test_errors_logged_during_sending_do_not_cause_new_alerts():
    bot = FakeBot(fail_for={1}, log_error_on_send=True)
    handler = AdminAlertHandler(bot, [1, 2], asyncio.get_running_loop(), window=0.05)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("polny_zachet.x").error("Первичная ошибка")
        await asyncio.sleep(0.3)
        await handler.drain()
    finally:
        root.removeHandler(handler)
    # ровно одно оповещение второму админу; ERROR из самой отправки («сеть недоступна») повторно не оповещает
    assert [t for c, t in bot.sent] and all("Первичная ошибка" in t for _, t in bot.sent)
    assert len(bot.sent) == 1


async def test_logging_from_another_thread_is_delivered(setup):
    bot, handler, logger = setup
    t = threading.Thread(target=lambda: logger.error("Из потока"))
    t.start()
    t.join()
    await settle(handler)
    assert len(bot.sent) == 2


async def test_emit_after_loop_closed_does_not_raise():
    loop = asyncio.new_event_loop()
    loop.close()
    handler = AdminAlertHandler(FakeBot(), [1], loop)
    logger = logging.getLogger("polny_zachet.closed")
    logger.addHandler(handler)
    try:
        logger.error("после остановки")  # не должно бросить исключение
    finally:
        logger.removeHandler(handler)


# --- «Бот запущен» ---

async def test_started_message_text_and_recipients():
    bot = FakeBot()
    await notify_started(bot, [1, 2], 3, 120)
    assert bot.sent == [(1, "Бот запущен. Контент: 3 категорий, 120 пунктов"),
                        (2, "Бот запущен. Контент: 3 категорий, 120 пунктов")]


async def test_started_message_failure_does_not_raise_and_does_not_block_others():
    bot = FakeBot(fail_for={1})
    await notify_started(bot, [1, 2], 1, 3)
    assert [c for c, _ in bot.sent] == [2]
