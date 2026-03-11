"""
ASSISTANT IA — CENTRE DE GESTION
Bot Telegram principal — compatible Python 3.11/3.12/3.13
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def send_long_message(msg, text: str, parse_mode: str = "Markdown"):
    """Envoie un message long en le découpant si nécessaire (limite Telegram = 4096 chars)."""
    max_len = 4000
    if len(text) <= max_len:
        await msg.reply_text(text, parse_mode=parse_mode)
        return
    # Découper proprement aux sauts de ligne
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                parts.append(current)
            current = line
        else:
            current += ("\n" + line) if current else line
    if current:
        parts.append(current)
    for i, part in enumerate(parts):
        suffix = f"\n_(suite {i+1}/{len(parts)})_" if len(parts) > 1 else ""
        await msg.reply_text(part + suffix, parse_mode=parse_mode)


from modules.sourcing import analyze_sourcing
from modules.stock import create_product, get_stock_summary, find_product
from modules.listings import generate_listing, publish_listing
from modules.reports import generate_report
from modules.accounting import get_financial_summary
from config.settings import TELEGRAM_TOKEN, AUTHORIZED_USERS

# ─── SESSIONS ────────────────────────────────────────────
user_sessions = {}

def get_session(user_id: int) -> dict:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "mode": None,
            "photos_buffer": [],
            "descriptions_buffer": [],
            "pending_listing_ref": None,
        }
    return user_sessions[user_id]

def is_authorized(user_id: int) -> bool:
    if not AUTHORIZED_USERS:
        return True
    return user_id in AUTHORIZED_USERS

# ─── COMMANDES ───────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("⛔ Accès non autorisé.")
        return
    await update.message.reply_text(
        f"👋 Bonjour {user.first_name} !\n\n"
        "🤖 *Assistant IA — Gestion Business*\n\n"
        "📌 *Commandes :*\n"
        "📸 Photo → Analyse sourcing automatique\n"
        "🛒 /acheter → Enregistrer un achat\n"
        "📦 /stock → Résumé du stock\n"
        "📊 /rapport → Rapport hebdomadaire\n"
        "💰 /finances → Bilan financier\n"
        "🔍 /chercher [nom] → Trouver un produit\n"
        "📝 /annonce [ref] → Générer une annonce\n"
        "❓ /aide → Toutes les commandes",
        parse_mode="Markdown"
    )

async def aide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *TOUTES LES COMMANDES*\n\n"
        "📸 *Photo seule* → Analyse prix marché\n"
        "🛒 */acheter* → Démarrer enregistrement achat\n"
        "📦 */stock* → État du stock\n"
        "🔍 */chercher [terme]* → Localiser un objet\n"
        "📝 */annonce [ref]* → Générer annonce de vente\n"
        "📊 */rapport* → Rapport 7 jours\n"
        "📊 */rapport mensuel* → Bilan du mois\n"
        "💰 */finances* → Bilan financier complet\n\n"
        "💡 *Flow achat :*\n"
        "1. /acheter\n"
        "2. Envoie tes photos\n"
        "3. /terminer\\_photos\n"
        "4. Tape `prix;source` ex: `45;Brocante Lyon`",
        parse_mode="Markdown"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return
    session = get_session(user_id)
    msg = update.message
    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_url = file.file_path
    caption = msg.caption or ""

    if session["mode"] == "enregistrer_achat":
        session["photos_buffer"].append(file_url)
        await msg.reply_text(
            f"📸 Photo {len(session['photos_buffer'])} ajoutée ✅\n"
            f"Envoie d'autres photos ou tape /terminer\\_photos",
            parse_mode="Markdown"
        )
        return

    thinking_msg = await msg.reply_text(
        "🔍 *Analyse en cours...*\n⏳ ~20 secondes",
        parse_mode="Markdown"
    )
    try:
        result = await analyze_sourcing(file_url, caption)
        await thinking_msg.delete()
        await send_long_message(msg, result)
        keyboard = [[
            InlineKeyboardButton("✅ J'achète", callback_data=f"acheter|{file_url}|{caption}"),
            InlineKeyboardButton("❌ Je passe", callback_data="passer"),
        ]]
        await msg.reply_text("👆 Décision ?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Erreur sourcing: {e}")
        await thinking_msg.edit_text(f"⚠️ Erreur : {str(e)}")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)
    data = query.data

    if data.startswith("acheter|"):
        parts = data.split("|", 2)
        photo_url = parts[1]
        caption_saved = parts[2] if len(parts) > 2 else ""
        session["mode"] = "attente_prix_source"
        session["photos_buffer"] = [photo_url]
        session["descriptions_buffer"] = [caption_saved] if caption_saved else []
        await query.edit_message_text(
            "🛒 *On enregistre l'achat !*\n\n"
            "Format : `prix;source`\n"
            "Exemple : `25;Brocante Lyon`",
            parse_mode="Markdown"
        )
    elif data == "passer":
        await query.edit_message_text("✅ OK, on passe !")
    elif data.startswith("publier|"):
        ref = data.split("|", 1)[1]
        thinking = await query.message.reply_text("📡 Publication en cours...")
        try:
            result = await publish_listing(ref)
            await thinking.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {e}")
    elif data.startswith("annuler_pub|"):
        await query.edit_message_text("❌ Publication annulée.")

async def cmd_acheter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    session["mode"] = "enregistrer_achat"
    session["photos_buffer"] = []
    session["descriptions_buffer"] = []
    await update.message.reply_text(
        "📸 *Mode achat activé !*\n\n"
        "Envoie les photos de l'objet.\n"
        "Quand tu as fini → /terminer\\_photos",
        parse_mode="Markdown"
    )

async def cmd_terminer_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    if not session["photos_buffer"]:
        await update.message.reply_text("⚠️ Aucune photo. Tape /acheter puis envoie des photos.")
        return
    session["mode"] = "attente_prix_source"
    nb = len(session["photos_buffer"])
    await update.message.reply_text(
        f"✅ *{nb} photo(s) enregistrée(s) !*\n\n"
        "Maintenant : `prix;source`\n\n"
        "Exemples :\n"
        "`45;Brocante Lyon`\n"
        "`12;Emmaüs Paris`\n"
        "`80;Enchères en ligne`",
        parse_mode="Markdown"
    )

async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thinking = await update.message.reply_text("📦 Chargement...")
    try:
        result = await get_stock_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

async def cmd_chercher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage : `/chercher [nom]`", parse_mode="Markdown")
        return
    q = " ".join(context.args)
    thinking = await update.message.reply_text(f"🔍 Recherche *{q}*...", parse_mode="Markdown")
    try:
        result = await find_product(q)
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

async def cmd_annonce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage : `/annonce [ref]`\nEx: `/annonce REF-2025-0001`", parse_mode="Markdown")
        return
    ref = context.args[0].upper()
    session = get_session(update.effective_user.id)
    thinking = await update.message.reply_text(f"✍️ Génération *{ref}*...", parse_mode="Markdown")
    try:
        result = await generate_listing(ref)
        await thinking.delete()
        session["pending_listing_ref"] = ref
        await update.message.reply_text(result, parse_mode="Markdown")
        keyboard = [
            [InlineKeyboardButton("🚀 Publier", callback_data=f"publier|{ref}")],
            [InlineKeyboardButton("❌ Annuler", callback_data=f"annuler_pub|{ref}")]
        ]
        await update.message.reply_text("👆 Que faire ?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

async def cmd_rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    periode = "mois" if context.args and context.args[0].lower() == "mensuel" else "semaine"
    thinking = await update.message.reply_text(f"📊 Rapport {periode}...")
    try:
        result = await generate_report(periode)
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

async def cmd_finances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thinking = await update.message.reply_text("💰 Calcul...")
    try:
        result = await get_financial_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return
    session = get_session(user_id)
    text = update.message.text.strip()

    if session["mode"] == "attente_prix_source":
        if ";" in text:
            parts = text.split(";", 1)
            try:
                prix = float(parts[0].strip().replace("€","").replace(",","."))
                source = parts[1].strip()
                thinking = await update.message.reply_text("📝 Création fiche produit...")
                description = " | ".join(session.get("descriptions_buffer", []))
                result = await create_product(
                    photos=session["photos_buffer"],
                    prix_achat=prix,
                    source=source,
                    description=description
                )
                session["mode"] = None
                session["photos_buffer"] = []
                session["descriptions_buffer"] = []
                await thinking.edit_text(result, parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("⚠️ Format : `prix;source` — Ex: `45;Brocante Lyon`", parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ Format : `prix;source` — Ex: `45;Brocante Lyon`", parse_mode="Markdown")
        return

    t = text.lower()
    if any(w in t for w in ["rapport", "bilan"]):
        periode = "mois" if "mois" in t else "semaine"
        thinking = await update.message.reply_text("📊 Génération...")
        result = await generate_report(periode)
        await thinking.edit_text(result, parse_mode="Markdown")
    elif any(w in t for w in ["stock", "inventaire"]):
        thinking = await update.message.reply_text("📦 Chargement...")
        result = await get_stock_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    elif any(w in t for w in ["finance", "marge", "argent"]):
        thinking = await update.message.reply_text("💰 Calcul...")
        result = await get_financial_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    elif "ok publier" in t or t == "publier":
        if session.get("pending_listing_ref"):
            ref = session["pending_listing_ref"]
            thinking = await update.message.reply_text("📡 Publication...")
            result = await publish_listing(ref)
            await thinking.edit_text(result, parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ Utilise `/annonce [ref]` d'abord.", parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "💡 *Que puis-je faire ?*\n\n"
            "📸 Photo → analyse sourcing\n"
            "🛒 /acheter → enregistrer achat\n"
            "📦 /stock → inventaire\n"
            "📊 /rapport → chiffres\n"
            "❓ /aide → toutes les commandes",
            parse_mode="Markdown"
        )

# ─── LANCEMENT COMPATIBLE TOUTES VERSIONS PYTHON ─────────
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN manquant")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aide", aide))
    app.add_handler(CommandHandler("help", aide))
    app.add_handler(CommandHandler("acheter", cmd_acheter))
    app.add_handler(CommandHandler("terminer_photos", cmd_terminer_photos))
    app.add_handler(CommandHandler("stock", cmd_stock))
    app.add_handler(CommandHandler("chercher", cmd_chercher))
    app.add_handler(CommandHandler("annonce", cmd_annonce))
    app.add_handler(CommandHandler("rapport", cmd_rapport))
    app.add_handler(CommandHandler("finances", cmd_finances))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🤖 Bot démarré avec succès !")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
