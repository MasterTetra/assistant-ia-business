"""
╔══════════════════════════════════════════════════════════╗
║          ASSISTANT IA — CENTRE DE GESTION                ║
║          Bot Telegram principal                          ║
╚══════════════════════════════════════════════════════════╝
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
from modules.sourcing import analyze_sourcing
from modules.stock import create_product, get_stock_summary, find_product
from modules.listings import generate_listing, publish_listing
from modules.reports import generate_report
from modules.accounting import get_financial_summary
from config.settings import TELEGRAM_TOKEN, AUTHORIZED_USERS

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  ÉTAT CONVERSATIONNEL (session par utilisateur)
# ─────────────────────────────────────────────
user_sessions = {}

def get_session(user_id: int) -> dict:
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "mode": None,           # sourcing / stock / listing / None
            "pending_product": None,
            "pending_listing": None,
            "photos_buffer": [],
        }
    return user_sessions[user_id]

def is_authorized(user_id: int) -> bool:
    """Vérifie que l'utilisateur est autorisé (optionnel si usage perso)"""
    if not AUTHORIZED_USERS:
        return True
    return user_id in AUTHORIZED_USERS

# ─────────────────────────────────────────────
#  COMMANDES DE BASE
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_authorized(user.id):
        await update.message.reply_text("⛔ Accès non autorisé.")
        return

    menu_text = (
        f"👋 Bonjour {user.first_name} !\n\n"
        "🤖 *Assistant IA — Gestion Business*\n\n"
        "📌 *Commandes rapides :*\n"
        "📸 Envoie une photo → Analyse sourcing automatique\n"
        "🛒 /acheter → Enregistrer un achat\n"
        "📦 /stock → Résumé du stock\n"
        "📊 /rapport → Rapport hebdomadaire\n"
        "💰 /finances → Bilan financier\n"
        "🔍 /chercher [nom] → Trouver un produit\n"
        "❓ /aide → Toutes les commandes\n\n"
        "💡 *Astuce :* Envoie directement une photo pour commencer !"
    )
    await update.message.reply_text(menu_text, parse_mode="Markdown")

async def aide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 *GUIDE COMPLET DES COMMANDES*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 *SOURCING*\n"
        "• Photo seule → Analyse prix marché automatique\n"
        "• Photo + texte → Analyse avec contexte\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📦 *STOCK*\n"
        "• /acheter → Créer une fiche produit\n"
        "• /stock → Vue d'ensemble du stock\n"
        "• /chercher [nom] → Localiser un objet\n"
        "• /statut [ref] [nouveau_statut] → Changer le statut\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📝 *ANNONCES*\n"
        "• /annonce [ref] → Générer une annonce pour un produit\n"
        "• Répondre 'OK publier' → Publier sur les plateformes\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *RAPPORTS*\n"
        "• /rapport → Rapport des 7 derniers jours\n"
        "• /rapport mensuel → Bilan du mois\n"
        "• /finances → Résumé financier\n"
        "• /tendances → Analyse du marché\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ *ADMINISTRATION*\n"
        "• /emplacements → Liste des emplacements libres\n"
        "• /vendus → Objets vendus ce mois\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

# ─────────────────────────────────────────────
#  GESTION DES PHOTOS (SOURCING)
# ─────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    session = get_session(user_id)
    msg = update.message

    # Récupérer le fichier photo (meilleure qualité)
    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_url = file.file_path
    caption = msg.caption or ""

    # Mode : si l'utilisateur est en train d'enregistrer un achat
    if session["mode"] == "enregistrer_achat":
        session["photos_buffer"].append(file_url)
        await msg.reply_text(
            f"📸 Photo {len(session['photos_buffer'])} ajoutée.\n"
            "Envoie d'autres photos ou tape /terminer_photos pour continuer.",
            parse_mode="Markdown"
        )
        return

    # Mode par défaut : SOURCING
    thinking_msg = await msg.reply_text(
        "🔍 *Analyse en cours...*\n"
        "⏳ Identification de l'objet + recherche des prix marché\n"
        "_(environ 15-30 secondes)_",
        parse_mode="Markdown"
    )

    try:
        result = await analyze_sourcing(file_url, caption)
        await thinking_msg.delete()
        await msg.reply_text(result, parse_mode="Markdown")

        # Proposer d'enregistrer l'achat
        keyboard = [
            [
                InlineKeyboardButton("✅ J'achète — Enregistrer", callback_data=f"acheter|{file_url}"),
                InlineKeyboardButton("❌ Je passe", callback_data="passer"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await msg.reply_text(
            "👆 Décision ?",
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Erreur sourcing: {e}")
        await thinking_msg.edit_text(
            f"⚠️ Erreur lors de l'analyse : {str(e)}\n"
            "Réessaie ou contacte le support."
        )

# ─────────────────────────────────────────────
#  GESTION DES BOUTONS INLINE
# ─────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)
    data = query.data

    if data.startswith("acheter|"):
        photo_url = data.split("|", 1)[1]
        session["mode"] = "enregistrer_achat"
        session["photos_buffer"] = [photo_url]
        await query.edit_message_text(
            "🛒 *Enregistrement de l'achat*\n\n"
            "Réponds à ces questions :\n"
            "1️⃣ Quel est le prix d'achat ? (ex: 25)\n"
            "2️⃣ Où l'as-tu acheté ? (ex: Brocante Lyon, Emmaüs, eBay)\n\n"
            "Format : `prix;source` — ex: `25;Brocante Lyon`",
            parse_mode="Markdown"
        )

    elif data == "passer":
        await query.edit_message_text("✅ OK, on passe à autre chose. Envoie une autre photo quand tu veux !")

    elif data.startswith("publier|"):
        ref = data.split("|", 1)[1]
        thinking = await query.message.reply_text("📡 *Publication en cours...*", parse_mode="Markdown")
        try:
            result = await publish_listing(ref)
            await thinking.delete()
            await query.message.reply_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur publication: {e}")

    elif data.startswith("annuler_pub|"):
        await query.edit_message_text("❌ Publication annulée.")

    elif data == "rapport_semaine":
        result = await generate_report("semaine")
        await query.message.reply_text(result, parse_mode="Markdown")

    elif data == "rapport_mois":
        result = await generate_report("mois")
        await query.message.reply_text(result, parse_mode="Markdown")

# ─────────────────────────────────────────────
#  COMMANDE /acheter
# ─────────────────────────────────────────────

async def cmd_acheter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)
    session["mode"] = "enregistrer_achat"
    session["photos_buffer"] = []

    await update.message.reply_text(
        "📸 *Enregistrement d'un achat*\n\n"
        "Envoie les photos de l'objet (autant que tu veux).\n"
        "Quand c'est bon, tape /terminer_photos",
        parse_mode="Markdown"
    )

async def cmd_terminer_photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = get_session(user_id)

    if not session["photos_buffer"]:
        await update.message.reply_text("⚠️ Aucune photo reçue. Envoie d'abord les photos.")
        return

    session["mode"] = "attente_prix_source"
    nb = len(session["photos_buffer"])
    await update.message.reply_text(
        f"✅ {nb} photo(s) enregistrée(s).\n\n"
        "Maintenant, donne-moi le *prix d'achat* et la *source* :\n"
        "Format : `prix;source`\n"
        "Exemple : `45;Brocante de Lyon`",
        parse_mode="Markdown"
    )

# ─────────────────────────────────────────────
#  COMMANDE /stock
# ─────────────────────────────────────────────

async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thinking = await update.message.reply_text("📦 Chargement du stock...")
    try:
        result = await get_stock_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─────────────────────────────────────────────
#  COMMANDE /chercher
# ─────────────────────────────────────────────

async def cmd_chercher(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage : `/chercher [nom ou référence]`\n"
            "Ex: `/chercher lampe` ou `/chercher REF-2025-0012`",
            parse_mode="Markdown"
        )
        return

    query_text = " ".join(context.args)
    thinking = await update.message.reply_text(f"🔍 Recherche : *{query_text}*...", parse_mode="Markdown")
    try:
        result = await find_product(query_text)
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─────────────────────────────────────────────
#  COMMANDE /annonce
# ─────────────────────────────────────────────

async def cmd_annonce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage : `/annonce [référence]`\n"
            "Ex: `/annonce REF-2025-0012`",
            parse_mode="Markdown"
        )
        return

    ref = context.args[0].upper()
    thinking = await update.message.reply_text(f"✍️ Génération de l'annonce pour *{ref}*...", parse_mode="Markdown")

    try:
        result = await generate_listing(ref)
        await thinking.delete()
        await update.message.reply_text(result, parse_mode="Markdown")

        # Boutons de publication
        keyboard = [
            [
                InlineKeyboardButton("🚀 Publier sur toutes les plateformes", callback_data=f"publier|{ref}"),
            ],
            [
                InlineKeyboardButton("❌ Annuler", callback_data=f"annuler_pub|{ref}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("👆 Que faire ?", reply_markup=reply_markup)

    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─────────────────────────────────────────────
#  COMMANDE /rapport
# ─────────────────────────────────────────────

async def cmd_rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    periode = "mois" if context.args and context.args[0].lower() == "mensuel" else "semaine"
    thinking = await update.message.reply_text(f"📊 Génération du rapport ({periode})...")

    try:
        result = await generate_report(periode)
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─────────────────────────────────────────────
#  COMMANDE /finances
# ─────────────────────────────────────────────

async def cmd_finances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thinking = await update.message.reply_text("💰 Calcul des finances...")
    try:
        result = await get_financial_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

# ─────────────────────────────────────────────
#  GESTION DES MESSAGES TEXTE (flow conversationnel)
# ─────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    session = get_session(user_id)
    text = update.message.text.strip()

    # ── Flow : attente prix + source après photos ──
    if session["mode"] == "attente_prix_source":
        if ";" in text:
            parts = text.split(";", 1)
            try:
                prix_achat = float(parts[0].strip().replace("€", "").replace(",", "."))
                source = parts[1].strip()
                thinking = await update.message.reply_text("📝 Création de la fiche produit...")
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
                    "⚠️ Format invalide. Exemple : `45;Brocante Lyon`",
                    parse_mode="Markdown"
                )
        else:
            await update.message.reply_text(
                "⚠️ Utilise le format : `prix;source`\nEx: `45;Brocante Lyon`",
                parse_mode="Markdown"
            )
        return

    # ── Commandes naturelles ──
    text_lower = text.lower()

    if any(w in text_lower for w in ["rapport", "bilan", "résumé"]):
        if "mois" in text_lower or "mensuel" in text_lower:
            result = await generate_report("mois")
        else:
            result = await generate_report("semaine")
        await update.message.reply_text(result, parse_mode="Markdown")

    elif any(w in text_lower for w in ["stock", "inventaire"]):
        result = await get_stock_summary()
        await update.message.reply_text(result, parse_mode="Markdown")

    elif any(w in text_lower for w in ["finances", "argent", "marge"]):
        result = await get_financial_summary()
        await update.message.reply_text(result, parse_mode="Markdown")

    elif text_lower.startswith("ok publier") or text_lower == "publier":
        if session.get("pending_listing_ref"):
            ref = session["pending_listing_ref"]
            thinking = await update.message.reply_text("📡 Publication en cours...")
            result = await publish_listing(ref)
            await thinking.edit_text(result, parse_mode="Markdown")
        else:
            await update.message.reply_text(
                "⚠️ Aucune annonce en attente. Utilise `/annonce [ref]` d'abord.",
                parse_mode="Markdown"
            )

    else:
        # Réponse générique utile
        await update.message.reply_text(
            "💡 Je n'ai pas compris. Voici ce que je sais faire :\n\n"
            "📸 Envoie une *photo* → analyse sourcing\n"
            "📦 `/stock` → ton inventaire\n"
            "📊 `/rapport` → tes chiffres\n"
            "❓ `/aide` → toutes les commandes",
            parse_mode="Markdown"
        )

# ─────────────────────────────────────────────
#  LANCEMENT DU BOT
# ─────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN manquant dans .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Commandes
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

    # Messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🤖 Bot démarré avec succès !")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
