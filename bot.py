"""
Bot Telegram operador-only. Só responde a IDs na whitelist.
Qualquer mensagem de fora é silenciosamente ignorada (sem log de
conteúdo de quem não é autorizado).
"""
import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

from agent import processar_mensagem, reset_session

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
        # Ignora silenciosamente — sem responder, sem logar conteúdo
        logger.warning(f"Mensagem ignorada de ID não autorizado: {user_id}")
        return

    texto = update.message.text
    await update.message.chat.send_action(action="typing")

    try:
        resposta = processar_mensagem(user_id, texto)
    except Exception as e:
        logger.exception("Erro ao processar mensagem")
        resposta = (
            "Deu um erro aqui processando isso. Tenta de novo ou reformula "
            "a mensagem. Se persistir, chama o Paulo."
        )

    await update.message.reply_text(resposta)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not _autorizado(user_id):
        return
    reset_session(user_id)
    await update.message.reply_text("Conversa reiniciada. Pode começar de novo.")


def main() -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot Tassinha iniciado.")
    app.run_polling()


if __name__ == "__main__":
    main()
