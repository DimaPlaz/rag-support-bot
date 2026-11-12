_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "greeting": (
            "Hi! I'm the BoxHub Self-Storage assistant.\n\n"
            "Ask me about unit sizes, prices, access hours, payments or storage rules. "
            "I answer from our knowledge base and show where the answer comes from. "
            "If I can't help, I'll pass your question to a person.\n\n"
            "Try one of these:"
        ),
        "sources": "Sources",
        "rate_limited": "You're sending messages too quickly. Please wait a minute and try again.",
        "too_long": "Please keep your question under 1000 characters.",
        "text_only": "I can only read text messages. Please type your question.",
        "error": "Sorry, something went wrong. Please try again in a moment.",
        "chat_id": "This chat's ID: {chat_id}",
    },
    "ru": {
        "greeting": (
            "Здравствуйте! Я ассистент BoxHub Self-Storage.\n\n"
            "Спросите о размерах боксов, ценах, доступе, оплате или правилах хранения. "
            "Я отвечаю по базе знаний и показываю, откуда взят ответ. "
            "Если не смогу помочь — передам вопрос сотруднику.\n\n"
            "Например:"
        ),
        "sources": "Источники",
        "rate_limited": "Слишком много сообщений подряд. Подождите минуту и попробуйте снова.",
        "too_long": "Пожалуйста, уложите вопрос в 1000 символов.",
        "text_only": "Я понимаю только текст. Напишите, пожалуйста, вопрос.",
        "error": "Извините, что-то пошло не так. Попробуйте ещё раз чуть позже.",
        "chat_id": "ID этого чата: {chat_id}",
    },
}

_EXAMPLES: dict[str, list[str]] = {
    "en": [
        "How much is a medium unit?",
        "Can I store a motorcycle?",
        "What happens if I pay late?",
    ],
    "ru": [
        "Сколько стоит бокс среднего размера?",
        "Можно ли хранить мотоцикл?",
        "Что будет, если просрочить оплату?",
    ],
}


def language(language_code: str | None) -> str:
    return "ru" if language_code and language_code.lower().startswith("ru") else "en"


def text(key: str, language_code: str | None) -> str:
    return _TEXTS[language(language_code)][key]


def examples(language_code: str | None) -> list[str]:
    return _EXAMPLES[language(language_code)]
