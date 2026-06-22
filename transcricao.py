"""
Transcrição de áudio via Groq Whisper (Sessão 12).

A Tassia manda mensagem de voz no Telegram; este módulo baixa o áudio e
transcreve pra texto, que entra no mesmo fluxo do agente.

Usa Groq (Whisper Large v3 Turbo) — rápido e barato. A mesma GROQ_API_KEY
funciona no free tier e no pago (Groq só cobra quando passa do gratuito).
"""
import os
import logging

logger = logging.getLogger(__name__)

_groq_client = None

# Modelo de transcrição do Groq. Turbo = mais rápido e barato, qualidade alta.
MODELO_WHISPER = os.environ.get("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")


def _client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def transcrever(caminho_audio: str) -> str:
    """
    Transcreve um arquivo de áudio pra texto em português.
    Recebe o caminho de um arquivo local (ex: .ogg do Telegram).
    Retorna o texto transcrito, ou levanta exceção se falhar.
    """
    with open(caminho_audio, "rb") as f:
        resp = _client().audio.transcriptions.create(
            file=(os.path.basename(caminho_audio), f.read()),
            model=MODELO_WHISPER,
            language="pt",          # fixa português — melhora precisão
            response_format="text",
        )
    # com response_format="text", a lib retorna a string direto
    texto = resp if isinstance(resp, str) else getattr(resp, "text", str(resp))
    return texto.strip()
