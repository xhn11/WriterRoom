"""
AI generátor kapitol z kostry scény pomocí Copilot API.
"""
from __future__ import annotations

import json
import logging
import re
import time
from copilot_api import ask, ask_json, RateLimitError

log = logging.getLogger("generator")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

_SYSTEM_CHAPTER = (
    "Jsi zkušený spisovatel beletrie. Tvým JEDINÝM úkolem je rozepsat přiloženou kostru do plnohodnotné literární kapitoly.\n"
    "PRAVIDLA:\n"
    "1. Piš VÝHRADNĚ o postavách, místech a událostech, které jsou uvedeny v kostře. Nevymýšlej žádné nové postavy, místa ani události.\n"
    "2. Zachovej náladu, tempo a záměr kostry – pokud kostra popisuje útěk v uličce, piš o útěku v uličce.\n"
    "3. Piš dramaticky, poeticky a poutavě. Používej dialog, popis prostředí a vnitřní monolog postav.\n"
    "4. Výstup musí být POUZE text kapitoly – žádné nadpisy, žádné komentáře, jen příběh."
)

_SYSTEM_SUMMARY = (
    "Jsi asistent pro shrnutí literárních textů. Shrň kapitolu do 2–3 vět tak, "
    "aby sloužila jako kontext pro psaní dalších kapitol. Buď stručný a výstižný."
)


def generate_chapter(
    skeleton: str,
    project_title: str = "",
    genre: str = "",
    style_notes: str = "",
    previous_summary: str = "",
    temperature: float = 0.85,
    max_tokens: int = 3000,
) -> str:
    """Vygeneruje text kapitoly z kostry. Vrací string s textem kapitoly."""
    parts: list[str] = []
    if project_title:
        parts.append(f"Název díla: {project_title}")
    if genre:
        parts.append(f"Žánr: {genre}")
    if style_notes:
        parts.append(f"Styl / poznámky: {style_notes}")
    if previous_summary:
        parts.append(f"Shrnutí předchozí kapitoly: {previous_summary}")

    header = "\n".join(parts)
    user_msg = f"{header}\n\nKostra kapitoly:\n{skeleton}" if header else f"Kostra kapitoly:\n{skeleton}"

    log.debug("=== PROMPT DO AI ===\n%s\n====================", user_msg)
    return _call(user_msg, _SYSTEM_CHAPTER, temperature, max_tokens)


def summarize_chapter(text: str) -> str:
    """Stručné shrnutí kapitoly pro kontext dalšího psaní."""
    return _call(
        f"Shrň tuto kapitolu:\n\n{text}",
        _SYSTEM_SUMMARY,
        temperature=0.3,
        max_tokens=200,
    )


def _call(user_msg: str, system: str, temperature: float, max_tokens: int) -> str:
    try:
        return ask(user_msg, system=system, temperature=temperature, max_tokens=max_tokens)
    except RateLimitError as e:
        time.sleep(e.wait_seconds)
        return ask(user_msg, system=system, temperature=temperature, max_tokens=max_tokens)


# ── Import / rozdělení surového textu ────────────────────────────────────────

_SYSTEM_SPLIT = (
    "Jsi editor beletrie. Dostaneš surový nepřeformátovaný text (může to být celá kniha, "
    "scénář, poznámky, nebo cokoliv jiného) a tvým úkolem je:\n"
    "1. Rozdělit ho na logické kapitoly podle obsahu, předělů, nebo tematických celků.\n"
    "2. Každé kapitole dát výstižný název.\n"
    "3. Vrátit výsledek VÝHRADNĚ jako JSON pole objektů ve tvaru:\n"
    '   [{"title": "Název kapitoly", "text": "Text kapitoly...\"}, ...]\n'
    "Žádný jiný text. Pouze JSON pole. Zachovej původní text kapitol beze změn – pouze rozděl."
)


def split_raw_text(raw: str) -> list[dict]:
    """
    Rozdělí surový text do kapitol pomocí AI.
    Vrací list dictů: [{"title": str, "text": str}, ...]
    """
    # Pro velmi dlouhé texty posíláme po částech a necháme AI rozdělit každou zvlášť
    MAX_CHARS = 12000
    if len(raw) <= MAX_CHARS:
        return _split_chunk(raw)

    # Hrubé rozdělení na části a rekurzivní zpracování
    parts = _hard_split(raw, MAX_CHARS)
    result: list[dict] = []
    for part in parts:
        result.extend(_split_chunk(part))
    return result


def _split_chunk(text: str) -> list[dict]:
    try:
        data = ask_json(text, system=_SYSTEM_SPLIT, temperature=0.2, max_tokens=4000)
    except RateLimitError as e:
        time.sleep(e.wait_seconds)
        data = ask_json(text, system=_SYSTEM_SPLIT, temperature=0.2, max_tokens=4000)
    if isinstance(data, list):
        return [{"title": str(d.get("title", f"Kapitola")), "text": str(d.get("text", ""))} for d in data]
    raise ValueError("AI nevrátila platný seznam kapitol.")


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Rozdělí text na části max_chars znaků na hranicích odstavců."""
    paragraphs = re.split(r"\n{2,}", text)
    parts: list[str] = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) + 2 > max_chars:
            if current:
                parts.append(current.strip())
            current = p
        else:
            current = (current + "\n\n" + p).strip() if current else p
    if current:
        parts.append(current.strip())
    return parts or [text]
