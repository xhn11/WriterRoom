"""
XTTS-v2 TTS engine pro generování audio knih.
Lazy loading – model se načte až při prvním použití.
Dlouhé texty se automaticky rozdělí na chunky a výsledné WAV soubory se spojí.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import wave
from pathlib import Path
from typing import Callable, Optional

# Maximální délka jednoho TTS volání (ve znacích)
_CHUNK_MAX = 230

# Vestavění mluvčí XTTS-v2 (bez nutnosti vlastního hlasového vzorku)
BUILTIN_SPEAKERS = [
    "Claribel Dervla",
    "Daisy Studious",
    "Gracie Wise",
    "Ana Florence",
    "Annmarie Nele",
    "Asya Anara",
    "Brenda Stern",
    "Gitta Nikolina",
    "Sofia Hellen",
    "Tammy Grit",
    "Philip Schopenhauer",
    "Stefan Milic",
    "Vjollca Johnnie",
    "Emma Holmes",
]


def _clean_for_tts(text: str) -> str:
    """Odstraní/nahradí znaky, které XTTS čte divně."""
    # Uvozovky → nic (XTTS je čte jako hluk / zvláštní zvuk)
    text = text.replace("„", "").replace(""", "").replace(""", "").replace('"', "")
    text = text.replace("»", "").replace("«", "").replace("›", "").replace("‹", "")
    # Pomlčky a trojtečky → čárka (pauza)
    text = text.replace("–", ",").replace("—", ",").replace("…", ",")
    # Tečky → čárka (XTTS čte tečky jako zvuk, čárka dělá pauzu bez čtení)
    text = re.sub(r"\.(?=\s|$)", ",", text)
    # Více čárek za sebou → jedna
    text = re.sub(r",\s*,+", ",", text)
    # Více mezer → jedna
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def _split_text(text: str, max_chars: int = _CHUNK_MAX) -> list[str]:
    """Rozdělí text na chunky na hranicích vět, max max_chars znaků."""
    # Zachovat odstavce jako celky (menší)
    sentences = re.split(r"(?<=[.!?…])\s+", text.strip())
    chunks: list[str] = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 > max_chars:
            if current:
                chunks.append(current)
            # Pokud je věta sama o sobě příliš dlouhá, rozděl ji hrubě
            while len(sent) > max_chars:
                chunks.append(sent[:max_chars])
                sent = sent[max_chars:]
            current = sent
        else:
            current = (current + " " + sent).strip() if current else sent
    if current:
        chunks.append(current)
    return chunks or [text]


def _concat_wavs(files: list[Path], output: Path) -> None:
    """Spojí seznam WAV souborů do jednoho."""
    params = None
    frames_list: list[bytes] = []
    for f in files:
        with wave.open(str(f), "rb") as wf:
            if params is None:
                params = wf.getparams()
            frames_list.append(wf.readframes(wf.getnframes()))
    with wave.open(str(output), "wb") as out:
        out.setparams(params)  # type: ignore[arg-type]
        for fr in frames_list:
            out.writeframes(fr)


class TTSEngine:
    """Thread-safe XTTS-v2 engine s lazy loadingem."""

    def __init__(self) -> None:
        self._tts = None
        self._lock = threading.Lock()
        self._loaded = False

    @staticmethod
    def _agree_tos() -> None:
        """Nastaví env proměnnou pro automatické odsouhlasení Coqui CPML licence."""
        os.environ.setdefault("COQUI_TOS_AGREED", "1")

    def _load(self, cb: Optional[Callable[[str], None]] = None) -> None:
        with self._lock:
            if self._loaded:
                return
            self._agree_tos()
            try:
                if cb:
                    cb("Nahrávám XTTS-v2 model (první spuštění trvá 1–2 min)…")
                from TTS.api import TTS  # type: ignore
                import torch  # type: ignore

                device = "cuda" if torch.cuda.is_available() else "cpu"
                self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
                self._loaded = True
                if cb:
                    cb(f"XTTS-v2 připraven ({device.upper()})")
            except ImportError:
                if cb:
                    cb("TTS knihovna chybí – instaluji automaticky (může trvat pár minut)…")
                pkgs = ["coqui-tts", "torch", "torchaudio", "transformers<4.46"]
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "--quiet"] + pkgs
                )
                if cb:
                    cb("Instalace hotova – nahrávám XTTS-v2 model…")
                from TTS.api import TTS  # type: ignore
                import torch  # type: ignore

                device = "cuda" if torch.cuda.is_available() else "cpu"
                self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
                self._loaded = True
                if cb:
                    cb(f"XTTS-v2 připraven ({device.upper()})")

    def synthesize(
        self,
        text: str,
        output_path: Path,
        speaker_wav: Optional[Path] = None,
        speaker: str = "Ana Florence",
        language: str = "cs",
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Syntetizuje text do WAV souboru. Automaticky dělí dlouhé texty."""
        self._load(progress_callback)

        text = _clean_for_tts(text)
        chunks = _split_text(text)
        total = len(chunks)

        if total == 1:
            self._synth_chunk(chunks[0], output_path, speaker_wav, speaker, language)
            if progress_callback:
                progress_callback(f"Audio uloženo: {output_path.name}")
            return

        tmp_files: list[Path] = []
        try:
            for i, chunk in enumerate(chunks):
                if progress_callback:
                    progress_callback(f"Syntetizuji část {i + 1}/{total}…")
                tmp = output_path.with_name(f"_tmp_{i}_{output_path.name}")
                self._synth_chunk(chunk, tmp, speaker_wav, speaker, language)
                tmp_files.append(tmp)

            if progress_callback:
                progress_callback("Spojuji audio části…")
            _concat_wavs(tmp_files, output_path)
            if progress_callback:
                progress_callback(f"Audio uloženo: {output_path.name}")
        finally:
            for f in tmp_files:
                f.unlink(missing_ok=True)

    def _synth_chunk(
        self,
        text: str,
        out: Path,
        speaker_wav: Optional[Path],
        speaker: str,
        language: str,
    ) -> None:
        kwargs: dict = {
            "text": text,
            "file_path": str(out),
            "language": language,
        }
        if speaker_wav and speaker_wav.exists():
            kwargs["speaker_wav"] = str(speaker_wav)
        else:
            kwargs["speaker"] = speaker
        self._tts.tts_to_file(**kwargs)

    @property
    def is_loaded(self) -> bool:
        return self._loaded


_engine: Optional[TTSEngine] = None


def get_engine() -> TTSEngine:
    global _engine
    if _engine is None:
        _engine = TTSEngine()
    return _engine
