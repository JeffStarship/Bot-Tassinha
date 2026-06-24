"""
Bot Telegram operador-only. Só responde a IDs na whitelist.
Qualquer mensagem de fora é silenciosamente ignorada.
"""
import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from agent import processar_mensagem, reset_session
import transcricao

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ALLOWED_IDS = {
    int(x) for x in os.environ["TELEGRAM_ALLOWED_USER_IDS"].split(",") if x.strip()
}


def _autorizado(user_id: int) -> bool:
    return user_id in ALLOWED_IDS


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _autorizado(user_id):
        logger.warning(f"Mensagem ignorada de ID não autorizado: {user_id}")
        return

    texto = update.message.text
    await update.message.chat.send_action(action="typing")

    try:
        resposta = processar_mensagem(user_id, texto)
    except Exception:
        logger.exception("Erro ao processar mensagem")
        resposta = (
            "Deu um erro aqui processando isso. Tenta de novo ou reformula "
            "a mensagem. Se persistir, chama o Paulo."
        )

    await update.message.reply_text(resposta)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mensagem de voz: baixa, transcreve via Groq, joga no mesmo fluxo do texto."""
    user_id = update.effective_user.id
    if not _autorizado(user_id):
        logger.warning(f"Áudio ignorado de ID não autorizado: {user_id}")
        return

    await update.message.chat.send_action(action="typing")

    # baixa o áudio pra um arquivo temporário
    import tempfile
    voice = update.message.voice or update.message.audio
    if voice is None:
        await update.message.reply_text("Não consegui pegar esse áudio. Tenta de novo ou escreve.")
        return

    try:
        arquivo = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=True) as tmp:
            await arquivo.download_to_drive(tmp.name)
            texto = transcricao.transcrever(tmp.name)
    except Exception:
        logger.exception("Erro ao transcrever áudio")
        await update.message.reply_text(
            "Não consegui entender o áudio dessa vez. Tenta mandar de novo, "
            "falando um pouco mais devagar, ou escreve."
        )
        return

    if not texto:
        await update.message.reply_text(
            "O áudio veio vazio ou não deu pra entender. Tenta de novo ou escreve."
        )
        return

    logger.info(f"Áudio transcrito de {user_id}: {texto[:80]}")

    try:
        resposta = processar_mensagem(user_id, texto)
    except Exception:
        logger.exception("Erro ao processar áudio transcrito")
        resposta = (
            "Entendi o áudio mas deu um erro processando. Tenta de novo ou "
            "reformula. Se persistir, chama o Paulo."
        )

    await update.message.reply_text(resposta)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Foto: baixa, converte pra base64 e manda pro modelo interpretar."""
    user_id = update.effective_user.id
    if not _autorizado(user_id):
        logger.warning(f"Foto ignorada de ID não autorizado: {user_id}")
        return

    await update.message.chat.send_action(action="typing")

    import base64
    # photo é lista de tamanhos; o último é o de maior resolução
    foto = update.message.photo[-1] if update.message.photo else None
    # se vier como documento de imagem (sem compressão), trata também
    if foto is None and update.message.document and \
       (update.message.document.mime_type or "").startswith("image/"):
        doc = update.message.document
        media_type = doc.mime_type
        file_id = doc.file_id
    elif foto is not None:
        media_type = "image/jpeg"  # Telegram entrega foto comprimida como jpeg
        file_id = foto.file_id
    else:
        await update.message.reply_text("Não consegui pegar essa imagem. Tenta de novo.")
        return

    legenda = (update.message.caption or "").strip()

    try:
        arquivo = await context.bot.get_file(file_id)
        ba = await arquivo.download_as_bytearray()
        imagem_b64 = base64.b64encode(bytes(ba)).decode("utf-8")
    except Exception:
        logger.exception("Erro ao baixar imagem")
        await update.message.reply_text(
            "Não consegui baixar a imagem. Tenta mandar de novo."
        )
        return

    logger.info(f"Imagem recebida de {user_id} ({len(imagem_b64)} b64, legenda: {legenda[:40]!r})")

    try:
        resposta = processar_mensagem(
            user_id, legenda, imagem_b64=imagem_b64, imagem_media_type=media_type
        )
    except Exception:
        logger.exception("Erro ao processar imagem")
        resposta = (
            "Recebi a imagem mas deu um erro processando. Tenta de novo, ou me "
            "manda a informação por texto/áudio."
        )

    await update.message.reply_text(resposta)


async def cmd_consultor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Válvula manual: força escalada pro consultor (Sonnet)."""
    user_id = update.effective_user.id
    if not _autorizado(user_id):
        return

    # Texto após o /consultor
    pergunta = update.message.text.replace("/consultor", "", 1).strip()
    if not pergunta:
        await update.message.reply_text(
            "Manda a pergunta junto, tipo: /consultor tô indo bem esse mês?"
        )
        return

    await update.message.chat.send_action(action="typing")
    try:
        resposta = processar_mensagem(user_id, pergunta, forcar_consultor=True)
    except Exception:
        logger.exception("Erro no /consultor")
        resposta = "Deu erro acionando o consultor. Tenta de novo em alguns minutos."
    await update.message.reply_text(resposta)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _autorizado(user_id):
        return
    reset_session(user_id)
    await update.message.reply_text("Conversa reiniciada. Pode começar de novo.")


AJUDA_TEXTO = """Oi! Eu sou seu assistente. Você fala comigo em português normal, como se tivesse mandando mensagem pra alguém — pode escrever, mandar áudio OU foto, como preferir. Não precisa de comando nem formato certo. Algumas coisas que eu faço:

CLIENTES E ATENDIMENTOS
- "cadastra a Bruna, telefone (48) 99999-0000, veio por indicação"
- "atendi a Bruna hoje, manutenção" (eu puxo o preço sozinho)
- "agenda a Bruna pra sexta às 15h, alongamento"
- "a Bruna cancelou" / "a Bruna cancelou mas já vou remarcar"

PREÇOS
- "cadastra os serviços: alongamento 150, manutenção 90, banho de gel 70"
- "me mostra a tabela de preços"
- quando mudar um preço, é só mandar de novo

DINHEIRO
- "qual meu saldo esse mês?"
- "quanto faturei esse mês?"
- "lança 80 de material" / "aluguel de 500 todo mês"

CLIENTES SUMINDO
- "quem tá em risco de sumir?"
- "de quanto em quanto tempo a Bruna costuma voltar?"

INDICAÇÕES
- "a Bruna indicou a Fernanda"
- "quantas a Bruna indicou?"
- "quem mais me indicou clientes?"

LEMBRETES DAS CLIENTES (você escolhe)
- "pra Bruna, manda o lembrete 2 horas antes"
- "não manda lembrete pra Bruna"
- "volta a Bruna pro padrão"

NO MEIO DO ATENDIMENTO
- "a Bruna ainda não começou" / "a Bruna começou às 15h"

TODO DIA às 9h eu te mando um resumo. Domingo um resumo da semana, e dia 1º um do mês.

Se quiser um conselho mais pensado sobre o negócio, escreve /consultor e a pergunta. Ex: /consultor tô indo bem esse mês?

Qualquer dúvida, é só me perguntar do seu jeito."""


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _autorizado(user_id):
        return
    await update.message.reply_text(AJUDA_TEXTO)


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("ajuda", cmd_ajuda))
    app.add_handler(CommandHandler("start", cmd_ajuda))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("consultor", cmd_consultor))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))

    logger.info("Bot Tassinha iniciado (Haiku + roteamento Sonnet).")
    app.run_polling()


if __name__ == "__main__":
    main()
