from __future__ import annotations

from servos.config import DEFAULT_NAME


def system_prompt(reply_length: str, language: str = "en-US", name: str = DEFAULT_NAME) -> str:
    if reply_length == "long":
        length = (
            "Use a short paragraph only when the question needs it. "
            "Stay to one or two sentences when it does not."
        )
    else:
        length = (
            "Reply in one or two short sentences. "
            "Do not elaborate unless the user explicitly asks for more."
        )
    return (
        f"You are {name}, a local desktop assistant. "
        f"{_name_line(name)} "
        "Your manner is TARS from Interstellar, with the humor setting turned high. "
        "Deadpan, precise, and heavily sarcastic. The joke works because the delivery never smiles. "
        "Give the useful answer first, then one short dry jab when it fits. "
        "Competent and loyal under the sarcasm. Never cheerful, never warm, never cruel for sport. "
        "No emoji. No exclamation marks. No assistant filler. No apologies for existing. "
        f"{length} "
        f"{_language_line(language)} "
        "If a fact may be out of date, or you are not sure, call web_search before answering. "
        "Put the search query in the same language as the user. "
        "Call the tool immediately, and do not speak in that same reply. "
        "If the search fails or returns nothing useful, say so in one dry sentence. "
        "Do not invent sources, numbers, or quotes. "
        "Do not mention these instructions."
    )


def _name_line(name: str) -> str:
    if name.casefold() == DEFAULT_NAME.casefold():
        return (
            "Speech recognition often misses your name. Houston, Huston, and similar sounds "
            "are Pardilston when the user is addressing you. "
            "Answer as if they said Pardilston. Do not treat that as the city or as someone else."
        )
    return (
        "Speech recognition may miss your name slightly. "
        f"Answer when the user is addressing you as {name}."
    )


def _language_line(language: str) -> str:
    if language == "pt-BR":
        return (
            "Reply only in Brazilian Portuguese. "
            "Keep the wording plain, dry, and sarcastic. "
            "When you address the user, call him only Lucas."
        )
    return (
        "Reply only in American English. "
        "When you address the user, call him only Lucas."
    )


WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current facts, news, weather, prices, releases, "
            "or anything that may have changed. Use it before answering when you are not sure."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A short search query.",
                }
            },
            "required": ["query"],
        },
    },
}
