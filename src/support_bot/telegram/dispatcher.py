from aiogram import Dispatcher

from support_bot.services.answer import AnswerService
from support_bot.services.rate_limit import RateLimiter
from support_bot.telegram.handlers import create_router


def create_dispatcher(answers: AnswerService, limiter: RateLimiter) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    # Попадают в хендлеры по имени параметра.
    dispatcher["answers"] = answers
    dispatcher["limiter"] = limiter
    return dispatcher
