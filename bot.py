"""
ASSISTANT IA — CENTRE DE GESTION
Bot Telegram principal — version corrigée
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

# Import des modules
from modules.sourcing import analyze_sourcing
from modules.stock import create_product, get_stock_summary, find_product
from modules.listings import generate_listing, publish_listing
from modules.reports import generate_report
from modules.accounting import get_financial_summary
from config.settings import TELEGRAM_TOKEN, AUTHORIZED_USERS

# ─── SESSIONS UTILISATEUR ────────────────────────────────
user_sessions = {}

def get_session(user_id: int) -> dict:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "mode": None,
            "photos_buffer": [],
            "pending_listing_ref": None,
        }
    return user_sessions[user_id]

def is_authorized(user_id: int) -> bool:
    if not AUTHORIZED_USERS:
        return True
    return user_id in AUTHORIZED_USERS

# ─── /start ──────────────────────────────────────────────
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

# ─── /aide ───────────────────────────────────────────────
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

# ─── GESTION PHOTOS ──────────────────────────────────────
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

    # Mode achat : accumulation de photos
    if session["mode"] == "enregistrer_achat":
        session["photos_buffer"].append(file_url)
        await msg.reply_text(
            f"📸 Photo {len(session['photos_buffer'])} ajoutée ✅\n"
            f"Envoie d'autres photos ou tape /terminer\\_photos",
            parse_mode="Markdown"
        )
        return

    # Mode par défaut : SOURCING
    thinking_msg = await msg.reply_text(
        "🔍 *Analyse en cours...*\n⏳ Identification + recherche prix\n_~20 secondes_",
        parse_mode="Markdown"
    )
    try:
        result = await analyze_sourcing(file_url, caption)
        await thinking_msg.delete()
        await msg.reply_text(result, parse_mode="Markdown")

        # Boutons achat/pass
        keyboard = [[
            InlineKeyboardButton("✅ J'achète — Enregistrer", callback_data=f"acheter|{file_url}"),
            InlineKeyboardButton("❌ Je passe", callback_data="passer"),
        ]]
        await msg.reply_text("👆 Décision ?", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Erreur sourcing: {e}")
        await thinking_msg.edit_text(f"⚠️ Erreur : {str(e)}")

# ─── BOUTONS INLINE ───────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)
    data = query.data

    if data.startswith("acheter|"):
        photo_url = data.split("|", 1)[1]
        session["mode"] = "attente_prix_source"
        session["photos_buffer"] = [photo_url]
        await query.edit_message_text(
            "🛒 *Parfait ! On enregistre l'achat.*\n\n"
            "Donne-moi le prix d'achat et la source :\n\n"
            "Format : `prix;source`\n"
            "Exemple : `25;Brocante Lyon`",
            parse_mode="Markdown"
        )

    elif data == "passer":
        await query.edit_message_text("✅ OK, on passe. Envoie une autre photo quand tu veux !")

    elif data.startswith("publier|"):
        ref = data.split("|", 1)[1]
        thinking = await query.message.reply_text("📡 *Publication en cours...*", parse_mode="Markdown")
        try:
            result = await publish_listing(ref)
            await thinking.delete()
            await query.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {e}")

    elif data.startswith("annuler_pub|"):
        await query.edit_message_text("❌ Publication annulée.")

# ─── /acheter ────────────────────────────────────────────
async def cmd_acheter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    session["mode"] = "enregistrer_achat"
    session["photos_buffer"] = []
    await update.message.reply_text(
        "📸 *Mode achat activé !*\n\n"
        "Envoie maintenant les photos de l'objet.\n"
        "Quand tu as fini, tape /terminer\\_photos",
        parse_mode="Markdown"
    )

# ─── /terminer_photos ────────────────────────────────────
async def cmd_terminer_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if not session["photos_buffer"]:
        await update.message.reply_text(
            "⚠️ Aucune photo reçue.\n"
            "Tape /acheter puis envoie tes photos."
        )
        return

    session["mode"] = "attente_prix_source"
    nb = len(session["photos_buffer"])
    await update.message.reply_text(
        f"✅ *{nb} photo(s) enregistrée(s) !*\n\n"
        "Maintenant donne-moi :\n"
        "Format : `prix;source`\n\n"
        "Exemple : `45;Brocante Lyon`\n"
        "Exemple : `12;Emmaüs Paris`\n"
        "Exemple : `80;Enchères en ligne`",
        parse_mode="Markdown"
    )

# ─── /stock ──────────────────────────────────────────────
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thinking = await update.message.reply_text("📦 Chargement du stock...")
    try:
        result = await get_stock_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─── /chercher ───────────────────────────────────────────
async def cmd_chercher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage : `/chercher [nom ou ref]`", parse_mode="Markdown")
        return
    query_text = " ".join(context.args)
    thinking = await update.message.reply_text(f"🔍 Recherche *{query_text}*...", parse_mode="Markdown")
    try:
        result = await find_product(query_text)
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─── /annonce ────────────────────────────────────────────
async def cmd_annonce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage : `/annonce [référence]`\nEx: `/annonce REF-2025-0001`", parse_mode="Markdown")
        return
    ref = context.args[0].upper()
    session = get_session(update.effective_user.id)
    thinking = await update.message.reply_text(f"✍️ Génération annonce *{ref}*...", parse_mode="Markdown")
    try:
        result = await generate_listing(ref)
        await thinking.delete()
        session["pending_listing_ref"] = ref
        await update.message.reply_text(result, parse_mode="Markdown")
        keyboard = [
            [InlineKeyboardButton("🚀 Publier sur toutes les plateformes", callback_data=f"publier|{ref}")],
            [InlineKeyboardButton("❌ Annuler", callback_data=f"annuler_pub|{ref}")]
        ]
        await update.message.reply_text("👆 Que faire ?", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─── /rapport ────────────────────────────────────────────
async def cmd_rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    periode = "mois" if context.args and context.args[0].lower() == "mensuel" else "semaine"
    thinking = await update.message.reply_text(f"📊 Génération rapport ({periode})...")
    try:
        result = await generate_report(periode)
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─── /finances ───────────────────────────────────────────
async def cmd_finances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thinking = await update.message.reply_text("💰 Calcul finances...")
    try:
        result = await get_financial_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─── MESSAGES TEXTE ──────────────────────────────────────
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    session = get_session(user_id)
    text = update.message.text.strip()

    # ── Flow achat : attente prix;source ──
    if session["mode"] == "attente_prix_source":
        if ";" in text:
            parts = text.split(";", 1)
            try:
                prix_achat = float(parts[0].strip().replace("€", "").replace(",", "."))
                source = parts[1].strip()
                thinking = await update.message.reply_text("📝 Création fiche produit en cours...")
                result = await create_product(
                    photos=session["photos_buffer"],
                    prix_achat=prix_achat,
                    source=source
                )
                session["mode"] = None
                session["photos_buffer"] = []
                await thinking.edit_text(result, parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text(
                    "⚠️ Prix invalide.\nFormat : `prix;source`\nEx: `45;Brocante Lyon`",
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(
                "⚠️ Format incorrect.\n\n"
                "Il faut : `prix;source`\n"
                "Exemple : `45;Brocante Lyon`",
                parse_mode="Markdown"
            )
        return

    # ── Commandes naturelles ──
    t = text.lower()

    if any(w in t for w in ["rapport", "bilan"]):
        periode = "mois" if "mois" in t else "semaine"
        thinking = await update.message.reply_text("📊 Génération du rapport...")
        result = await generate_report(periode)
        await thinking.edit_text(result, parse_mode="Markdown")

    elif any(w in t for w in ["stock", "inventaire"]):
        thinking = await update.message.reply_text("📦 Chargement...")
        result = await get_stock_summary()
        await thinking.edit_text(result, parse_mode="Markdown")

    elif any(w in t for w in ["finance", "marge", "argent", "chiffre"]):
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
            await update.message.reply_text(
                "⚠️ Aucune annonce en attente.\nUtilise `/annonce [ref]` d'abord.",
                parse_mode="Markdown"
            )
    else:
        await update.message.reply_text(
            "💡 *Que puis-je faire ?*\n\n"
            "📸 Envoie une *photo* → analyse sourcing\n"
            "🛒 /acheter → enregistrer un achat\n"
            "📦 /stock → ton inventaire\n"
            "📊 /rapport → tes chiffres\n"
            "❓ /aide → toutes les commandes",
            parse_mode="Markdown"
        )

# ─── LANCEMENT ───────────────────────────────────────────
async def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN manquant dans les variables d'environnement")

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
    await app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
