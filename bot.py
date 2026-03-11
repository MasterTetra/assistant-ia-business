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
            "vendre_data": None,
            "vendre_ref": None,
            "vendre_photos": [],
            "vendre_caption": "",
            "vendre_prix_achat": 0,
            "vendre_source": "",
            "last_photo_url": "",
            "last_caption": "",
            "flux_data": None,
            "flux_photo_url": "",
            "flux_caption": "",
            "flux_prix_achat": 0,
            "flux_source": "",
            "lot_photos": [],
            "lot_resultats": [],
            "lot_index_courant": 0,
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
        "💰 */finances* → Bilan financier complet\n""🔄 */statut [ref] [statut] [plateforme]* → Mettre à jour un statut\n\n"
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

    # ── MODE LOT ──────────────────────────────────────────
    if session.get("mode") == "lot_collecte":
        session["lot_photos"].append({"url": file_url, "caption": caption})
        nb = len(session["lot_photos"])
        from modules.lot import MAX_LOT
        await msg.reply_text(
            f"✅ Photo {nb}/{MAX_LOT} ajoutée."
            + (f"\n📝 {caption}" if caption else "\n💡 Ajoute une légende pour une meilleure analyse.")
            + "\n\nEnvoie d'autres photos ou tape /lot_analyser"
        )
        return

    # ── MODE VENDRE — attente photos supplémentaires ──────
    if session.get("mode") == "vendre_attente_photos":
        session["vendre_photos"].append(file_url)
        if caption and not session["vendre_caption"]:
            session["vendre_caption"] = caption
        nb = len(session["vendre_photos"])
        await msg.reply_text(
            f"✅ Photo {nb} ajoutée."
            + (f"\n📝 {caption}" if caption else "")
            + "\n\nEnvoie d'autres photos ou tape /analyser"
        )
        return

    # ── MODE ACHAT CLASSIQUE ──────────────────────────────
    if session.get("mode") == "enregistrer_achat":
        session["photos_buffer"].append(file_url)
        if caption:
            session["descriptions_buffer"].append(caption)
        await msg.reply_text(
            f"📸 Photo {len(session['photos_buffer'])} ajoutée ✅"
            + (f"\n📝 {caption}" if caption else "")
            + "\nEnvoie d'autres photos ou tape /terminer\_photos",
            parse_mode="Markdown"
        )
        return

    # ── FLUX PRINCIPAL — photo + légende = analyse auto ───
    import time as _time
    last_analysis = session.get("last_analysis_time", 0)
    if _time.time() - last_analysis < 30:
        await msg.reply_text(
            "⏳ Une analyse est déjà en cours.\n"
            "Répondez avec ✅ ou ❌ sur l'analyse précédente."
        )
        return

    if not caption:
        await msg.reply_text(
            "📸 Photo reçue !\n\n"
            "💡 Ajoute une légende avec les infos de l'objet :\n"
            "_Exemple : Porte-clé Renault Sport métal neuf sous blister_\n\n"
            "Renvoie la photo avec une légende.",
            parse_mode="Markdown"
        )
        return

    thinking_msg = await msg.reply_text("🔍 Analyse en cours...\n⏳ ~30 secondes")
    try:
        session["last_analysis_time"] = _time.time()
        session["flux_photo_url"] = file_url
        session["flux_caption"] = caption

        from modules.flux import analyser_marche, formater_analyse
        data = await analyser_marche(file_url, caption)
        session["flux_data"] = data

        await thinking_msg.delete()
        await send_long_message(msg, formater_analyse(data), parse_mode=None)

        # Demander le prix d'achat avant de calculer la marge
        session["mode"] = "flux_attente_prix_achat"
        await msg.reply_text(
            f"💶 Combien avez-vous payé cet objet ?\n\n"
            f"Tapez le prix en euros (ex: 45) ou 0 si pas encore acheté.\n"
            f"Prix d'achat maximum conseillé : {data['achat_max']} euros"
        )
    except Exception as e:
        logger.error(f"Erreur flux: {e}")
        await thinking_msg.edit_text(f"⚠️ Erreur : {str(e)[:200]}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = get_session(user_id)
    data = query.data

    if data == "acheter_ok":
        photo_url = session.get("last_photo_url", "")
        caption_saved = session.get("last_caption", "")
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

    elif data.startswith("gen_annonce|"):
        ref = data.split("|", 1)[1]
        thinking = await query.message.reply_text(f"📝 Génération annonce {ref}...")
        try:
            from modules.listings import generate_listing
            result = await generate_listing(ref)
            await thinking.delete()
            await send_long_message(query.message, result, parse_mode="Markdown")
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🚀 Publier", callback_data=f"publier|{ref}"),
                InlineKeyboardButton("❌ Annuler", callback_data=f"annuler_pub|{ref}"),
            ]])
            await query.message.reply_text("👆 Que faire ?", reply_markup=keyboard)
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")
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

    elif data == "vendre_valider":
        session = get_session(query.from_user.id)
        vdata = session.get("vendre_data")
        ref = session.get("vendre_ref")
        if not vdata or not ref:
            await query.edit_message_text("⚠️ Session expirée. Relance /vendre.")
            return
        thinking = await query.message.reply_text("📡 Archivage et publication en cours...")
        try:
            from modules.vendre import archiver_airtable
            photos = session.get("vendre_photos", [])
            caption = session.get("vendre_caption", "")
            prix_achat = session.get("vendre_prix_achat", 0)
            source = session.get("vendre_source", "")
            record_id = await archiver_airtable(vdata, ref, [], caption, prix_achat, source)
            session["mode"] = None
            session["vendre_data"] = None
            await thinking.edit_text(
                f"✅ ARCHIVE ET PUBLIE\n\n"
                f"Référence gestion : {ref}\n"
                f"Titre eBay : {vdata['titre_ebay']}\n"
                f"Prix eBay : {vdata['prix_ebay']} euros\n\n"
                f"Fiche Airtable créée.\n\n"
                f"💡 Une fois vendu :\n"
                f"/statut {ref} vendu eBay"
            )
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")

    elif data == "vendre_modifier":
        await query.edit_message_text(
            "✏️ QUE VOULEZ-VOUS MODIFIER ?\n\n"
            "Tapez ce que vous souhaitez changer, par exemple :\n"
            "• titre: Lampe César Baldaccini Daum Argos cristal fumé\n"
            "• prix: 950\n"
            "• etat: très bon état, manque abat-jour\n"
            "• description: [votre texte]\n\n"
            "Tapez vos modifications :"
        )
        session = get_session(query.from_user.id)
        session["mode"] = "vendre_modification"

    elif data == "vendre_annuler":
        session = get_session(query.from_user.id)
        session["mode"] = None
        session["vendre_data"] = None
        await query.edit_message_text("❌ Vente annulée.")

    elif data == "flux_continuer":
        session = get_session(query.from_user.id)
        data_flux = session.get("flux_data")
        if not data_flux:
            await query.edit_message_text("⚠️ Session expirée. Renvoie une photo.")
            return
        thinking = await query.message.reply_text("📝 Génération de l'annonce...")
        try:
            from modules.flux import generer_annonce, formater_annonce
            data_flux = await generer_annonce(data_flux)
            session["flux_data"] = data_flux
            session["mode"] = "flux_validation"
            await thinking.delete()
            annonce_txt = formater_annonce(data_flux)
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Valider", callback_data="flux_valider"),
                    InlineKeyboardButton("✏️ Modifier prix", callback_data="flux_mod_prix"),
                ],
                [
                    InlineKeyboardButton("✏️ Modifier annonce", callback_data="flux_mod_annonce"),
                    InlineKeyboardButton("❌ Annuler", callback_data="flux_annuler"),
                ]
            ])
            if len(annonce_txt) > 3500:
                await query.message.reply_text(annonce_txt[:3500])
                await query.message.reply_text(annonce_txt[3500:], reply_markup=keyboard)
            else:
                await query.message.reply_text(annonce_txt, reply_markup=keyboard)
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")

    elif data == "flux_valider":
        session = get_session(query.from_user.id)
        data_flux = session.get("flux_data")
        if not data_flux:
            await query.edit_message_text("⚠️ Session expirée.")
            return
        thinking = await query.message.reply_text("💾 Archivage...")
        try:
            from modules.flux import generer_ref, archiver
            ref = await generer_ref()
            prix_achat = session.get("flux_prix_achat", 0)
            source = session.get("flux_source", "")
            ok = await archiver(data_flux, ref, prix_achat, source)
            session["mode"] = None
            session["flux_data"] = None
            if ok:
                await thinking.edit_text(
                    f"✅ ARCHIVE\n\n"
                    f"Référence : {ref}\n"
                    f"Titre eBay : {data_flux['titre_ebay']}\n"
                    f"Prix : {data_flux['prix_revente']} euros\n\n"
                    f"📋 Annonce LBC/Vinted :\n"
                    f"{data_flux['titre_lbc']}\n\n"
                    f"Quand vendu → /statut {ref} vendu eBay"
                )
            else:
                await thinking.edit_text("⚠️ Erreur Airtable — vérifiez la base.")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")

    elif data == "flux_mod_prix":
        session = get_session(query.from_user.id)
        session["mode"] = "flux_attente_prix"
        await query.edit_message_text(
            "💶 Nouveau prix de vente eBay ?\n"
            "Tapez juste le chiffre, ex: 45"
        )

    elif data == "flux_mod_annonce":
        session = get_session(query.from_user.id)
        session["mode"] = "flux_attente_modif"
        await query.edit_message_text(
            "✏️ Que voulez-vous modifier ?\n\n"
            "Exemples :\n"
            "• titre: Porte-clé Renault Sport damier métal neuf\n"
            "• description: [votre texte complet]\n"
            "• etat: neuf sous blister d'origine"
        )

    elif data == "flux_annuler":
        session = get_session(query.from_user.id)
        session["mode"] = None
        session["flux_data"] = None
        await query.edit_message_text("❌ Annulé.")

    elif data.startswith("lot_ok|"):
        index = int(data.split("|")[1])
        session = get_session(query.from_user.id)
        resultats = session.get("lot_resultats", [])
        if index < len(resultats):
            resultats[index]["valide"] = True
            # Archiver dans Airtable
            from modules.lot import archiver_airtable_lot, generer_ref_gestion
            ref = await generer_ref_gestion()
            ok = await archiver_airtable_lot(resultats[index], ref)
            status = f"✅ Archivé — {ref}" if ok else "⚠️ Erreur Airtable"
            await query.edit_message_text(
                f"✅ VALIDE — Objet {index + 1}\n"
                f"{resultats[index]['titre_ebay']}\n"
                f"Prix eBay : {resultats[index]['prix_ebay']} euros\n"
                f"{status}"
            )
        # Passer au suivant
        next_index = index + 1
        session["lot_index_courant"] = next_index
        await asyncio.sleep(1)
        await _envoyer_fiche_lot(query, session, next_index)

    elif data.startswith("lot_non|"):
        index = int(data.split("|")[1])
        session = get_session(query.from_user.id)
        resultats = session.get("lot_resultats", [])
        if index < len(resultats):
            resultats[index]["refuse"] = True
            await query.edit_message_text(
                f"❌ REFUSE — Objet {index + 1}\n"
                f"{resultats[index]['objet']}"
            )
        next_index = index + 1
        session["lot_index_courant"] = next_index
        await asyncio.sleep(1)
        await _envoyer_fiche_lot(query, session, next_index)

    elif data.startswith("lot_prix|"):
        index = int(data.split("|")[1])
        session = get_session(query.from_user.id)
        session["lot_modif_index"] = index
        session["mode"] = "lot_modif_prix"
        await query.edit_message_text(
            f"✏️ Objet {index + 1} — Nouveau prix eBay ?\n\n"
            f"Tapez le prix en euros (ex: 45)"
        )

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
                # Extraire la référence du résultat
                import re as _re
                ref_match = _re.search(r'REF-\d{4}-\d+', result)
                ref = ref_match.group(0) if ref_match else None
                session["mode"] = None
                session["photos_buffer"] = []
                session["descriptions_buffer"] = []
                if ref:
                    session["pending_listing_ref"] = ref
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Générer l'annonce", callback_data=f"gen_annonce|{ref}"),
                        InlineKeyboardButton("❌ Plus tard", callback_data="passer"),
                    ]])
                    await thinking.edit_text(result, parse_mode="Markdown")
                    await update.message.reply_text(
                        "Voulez-vous générer l'annonce maintenant ?",
                        reply_markup=keyboard
                    )
                else:
                    await thinking.edit_text(result, parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("⚠️ Format : `prix;source` — Ex: `45;Brocante Lyon`", parse_mode="Markdown")
        else:
            await update.message.reply_text("⚠️ Format : `prix;source` — Ex: `45;Brocante Lyon`", parse_mode="Markdown")
        return

    t = text.lower()

    # ── MODES ACTIFS EN PRIORITÉ ─────────────────────────
    if session.get("mode") == "flux_attente_prix_achat":
        try:
            prix_achat = float(re.findall(r'[\d.,]+', update.message.text)[0].replace(',', '.'))
            session["flux_prix_achat"] = prix_achat
            session["mode"] = "flux_validation_achat"
            data_flux = session.get("flux_data", {})

            from modules.flux import formater_rentabilite
            rentabilite = formater_rentabilite(data_flux, prix_achat)
            achat_max = data_flux.get("achat_max", 0)
            marge_ok = prix_achat <= achat_max or prix_achat == 0

            if marge_ok or prix_achat == 0:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Générer l'annonce", callback_data="flux_continuer"),
                    InlineKeyboardButton("❌ Annuler", callback_data="flux_annuler"),
                ]])
                await update.message.reply_text(rentabilite + "\n✅ Dans les marges — on génère l'annonce ?", reply_markup=keyboard)
            else:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("⚠️ Continuer quand même", callback_data="flux_continuer"),
                    InlineKeyboardButton("❌ Abandonner", callback_data="flux_annuler"),
                ]])
                await update.message.reply_text(rentabilite + "\n⚠️ Au-dessus du seuil — continuer quand même ?", reply_markup=keyboard)
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Tapez juste un nombre, ex: 45")

    elif any(w in t for w in ["rapport", "bilan"]):
        periode = "mois" if "mois" in t else "semaine"
        thinking = await update.message.reply_text("📊 Génération...")
        result = await generate_report(periode)
        await thinking.edit_text(result, parse_mode="Markdown")
    elif any(w in t for w in ["stock", "inventaire"]):
        thinking = await update.message.reply_text("📦 Chargement...")
        result = await get_stock_summary()
        await thinking.edit_text(result, parse_mode="Markdown")
    elif session.get("mode") == "flux_attente_prix":
        try:
            nouveau_prix = int(re.findall(r'\d+', update.message.text)[0])
            data_flux = session.get("flux_data", {})
            data_flux["prix_revente"] = nouveau_prix
            session["flux_data"] = data_flux
            session["mode"] = "flux_validation"
            from modules.flux import formater_annonce
            annonce_txt = formater_annonce(data_flux)
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Valider", callback_data="flux_valider"),
                    InlineKeyboardButton("✏️ Modifier prix", callback_data="flux_mod_prix"),
                ],
                [
                    InlineKeyboardButton("✏️ Modifier annonce", callback_data="flux_mod_annonce"),
                    InlineKeyboardButton("❌ Annuler", callback_data="flux_annuler"),
                ]
            ])
            await update.message.reply_text(annonce_txt, reply_markup=keyboard)
        except:
            await update.message.reply_text("⚠️ Tapez juste un nombre, ex: 45")

    elif session.get("mode") == "flux_attente_modif":
        modif = update.message.text.strip()
        data_flux = session.get("flux_data", {})
        if modif.lower().startswith("titre:"):
            data_flux["titre_ebay"] = modif[6:].strip()
            data_flux["titre_lbc"] = modif[6:].strip()[:70]
        elif modif.lower().startswith("description:"):
            data_flux["description"] = modif[12:].strip()
        elif modif.lower().startswith("etat:"):
            data_flux["caption"] = modif[5:].strip()
        session["flux_data"] = data_flux
        session["mode"] = "flux_validation"
        from modules.flux import formater_annonce
        annonce_txt = formater_annonce(data_flux)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Valider", callback_data="flux_valider"),
                InlineKeyboardButton("✏️ Modifier prix", callback_data="flux_mod_prix"),
            ],
            [
                InlineKeyboardButton("✏️ Modifier annonce", callback_data="flux_mod_annonce"),
                InlineKeyboardButton("❌ Annuler", callback_data="flux_annuler"),
            ]
        ])
        await update.message.reply_text(annonce_txt, reply_markup=keyboard)

    elif session.get("mode") == "flux_attente_source":
        session["flux_source"] = update.message.text.strip()
        session["mode"] = "flux_validation"
        await update.message.reply_text(f"✅ Source : {session['flux_source']}")

    elif session.get("mode") == "lot_modif_prix":
        try:
            nouveau_prix = int(re.findall(r'\d+', update.message.text)[0])
            index = session.get("lot_modif_index", 0)
            resultats = session.get("lot_resultats", [])
            if index < len(resultats):
                resultats[index]["prix_ebay"] = nouveau_prix
                resultats[index]["prix_lbc"] = int(nouveau_prix * 0.85)
                resultats[index]["prix_vinted"] = int(nouveau_prix * 0.80)
            session["mode"] = "lot_validation"
            await update.message.reply_text(f"✅ Prix mis à jour : {nouveau_prix} euros")
            await asyncio.sleep(1)
            await _envoyer_fiche_lot(update, session, index)
        except:
            await update.message.reply_text("⚠️ Format invalide. Tapez juste un nombre, ex: 45")

    elif session.get("mode") == "vendre_modification":
        # Appliquer les modifications demandées par l'utilisateur
        vdata = session.get("vendre_data", {})
        ref = session.get("vendre_ref", "")
        modif = update.message.text.strip()

        # Modifier les champs selon ce que l'utilisateur écrit
        if modif.lower().startswith("titre:"):
            vdata["titre_ebay"] = modif[6:].strip()
            vdata["titre_lbc"] = modif[6:].strip()[:70]
        elif modif.lower().startswith("prix:"):
            try:
                vdata["prix_ebay"] = int(re.findall(r'\d+', modif)[0])
            except:
                pass
        elif modif.lower().startswith("etat:"):
            vdata["etat"] = modif[5:].strip()
        elif modif.lower().startswith("description:"):
            vdata["description"] = modif[12:].strip()
        else:
            # Modification libre → régénérer avec Claude
            vdata["conseil"] = modif  # Stocker la demande

        session["vendre_data"] = vdata
        session["mode"] = "vendre_attente_validation"

        from modules.vendre import formater_fiche
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        fiche = formater_fiche(vdata, ref)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Valider et publier", callback_data="vendre_valider"),
                InlineKeyboardButton("✏️ Modifier encore", callback_data="vendre_modifier"),
            ],
            [InlineKeyboardButton("❌ Annuler", callback_data="vendre_annuler")]
        ])
        await update.message.reply_text(fiche, reply_markup=keyboard)
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



async def cmd_vendre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /vendre — Active le mode vente complet
    Envoyer ensuite les photos avec légende
    """
    session = get_session(update.effective_user.id)
    session["mode"] = "vendre_attente_photos"
    session["vendre_photos"] = []
    session["vendre_caption"] = ""
    session["vendre_data"] = None
    session["vendre_ref"] = None
    await update.message.reply_text(
        "🛍️ MODE VENTE ACTIVE\n\n"
        "Envoie maintenant les photos de l'objet.\n"
        "Ajoute en légende les infos que tu connais :\n\n"
        "Exemple : César Baldaccini lampe cristal Daum - très bon état - manque abat-jour\n\n"
        "Quand toutes les photos sont envoyées → /analyser"
    )

async def cmd_analyser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /analyser — Déclenche l'analyse complète après photos
    """
    session = get_session(update.effective_user.id)

    if session.get("mode") != "vendre_attente_photos":
        await update.message.reply_text("⚠️ Utilise /vendre d'abord pour activer le mode vente.")
        return

    if not session.get("vendre_photos"):
        await update.message.reply_text("⚠️ Aucune photo reçue. Envoie au moins une photo.")
        return

    thinking = await update.message.reply_text(
        "🔍 Analyse en cours...\n"
        "• Identification de l'objet\n"
        "• Recherche des prix sur eBay\n"
        "• Génération de l'annonce\n\n"
        "⏳ Cela peut prendre 30 secondes..."
    )

    try:
        from modules.vendre import analyser_et_generer, formater_fiche, generer_ref_gestion
        from modules.sourcing import analyze_sourcing

        photos = session["vendre_photos"]
        caption = session["vendre_caption"]

        # Lancer l'analyse + mettre à jour le message toutes les 10s
        objet_id = caption or "objet a identifier sur photo"

        import asyncio as _aio

        async def _progress():
            steps = [
                "🔍 Identification de l'objet...",
                "🌐 Recherche des prix sur eBay...",
                "📝 Génération de l'annonce...",
                "⏳ Finalisation...",
            ]
            for step in steps:
                await thinking.edit_text(step)
                await _aio.sleep(12)

        progress_task = _aio.create_task(_progress())
        try:
            data = await analyser_et_generer(photos, caption, objet_id)
        finally:
            progress_task.cancel()

        # Étape 3 : générer référence de gestion
        ref = await generer_ref_gestion()
        session["vendre_data"] = data
        session["vendre_ref"] = ref
        session["mode"] = "vendre_attente_validation"

        # Afficher la fiche complète
        from modules.vendre import formater_fiche
        fiche = formater_fiche(data, ref)

        await thinking.delete()

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Valider et publier", callback_data="vendre_valider"),
                InlineKeyboardButton("✏️ Modifier", callback_data="vendre_modifier"),
            ],
            [InlineKeyboardButton("❌ Annuler", callback_data="vendre_annuler")]
        ])

        # Découper si trop long
        if len(fiche) > 3500:
            await update.message.reply_text(fiche[:3500])
            await update.message.reply_text(fiche[3500:], reply_markup=keyboard)
        else:
            await update.message.reply_text(fiche, reply_markup=keyboard)

    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur analyse : {str(e)[:200]}")



async def cmd_lot_debut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /lot_debut — Active la collecte de photos en lot
    """
    session = get_session(update.effective_user.id)
    session["mode"] = "lot_collecte"
    session["lot_photos"] = []
    session["lot_resultats"] = []
    session["lot_index_courant"] = 0
    await update.message.reply_text(
        "📦 MODE LOT ACTIVE\n\n"
        "Envoie jusqu'à 50 photos, une par une.\n"
        "Ajoute une légende à chaque photo avec les infos de l'objet.\n\n"
        "Exemple de légende :\n"
        "César Baldaccini lampe cristal Daum - très bon état\n\n"
        "Quand toutes les photos sont envoyées → /lot_analyser\n"
        "Pour annuler → /lot_annuler"
    )


async def cmd_lot_analyser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /lot_analyser — Lance l'analyse de tous les objets du lot
    """
    session = get_session(update.effective_user.id)

    if session.get("mode") != "lot_collecte":
        await update.message.reply_text("⚠️ Utilise /lot_debut d'abord.")
        return

    photos = session.get("lot_photos", [])
    if not photos:
        await update.message.reply_text("⚠️ Aucune photo reçue. Envoie des photos d'abord.")
        return

    nb = len(photos)
    cout_estime = round(nb * 0.01, 2)

    thinking = await update.message.reply_text(
        f"🔍 Analyse de {nb} objet(s) en cours...\n"
        f"Coût estimé : ~{cout_estime}$\n\n"
        f"⏳ Environ {nb * 15} secondes...\n"
        f"Je vous enverrai les fiches une par une."
    )

    from modules.lot import analyser_objet, MAX_LOT
    resultats = []

    for i, item in enumerate(photos[:MAX_LOT], 1):
        try:
            await thinking.edit_text(
                f"🔍 Analyse {i}/{nb}...\n"
                f"⏳ Environ {(nb - i) * 15} secondes restantes..."
            )
            data = await analyser_objet(item["url"], item["caption"], i)
            data["total"] = nb
            resultats.append(data)
            # Pause entre chaque appel pour éviter le rate limit
            if i < nb:
                await asyncio.sleep(3)
        except Exception as e:
            resultats.append({
                "index": i, "total": nb,
                "objet": item["caption"] or f"Objet {i}",
                "photo_url": item["url"],
                "caption": item["caption"],
                "erreur": str(e)[:100],
                "titre_ebay": item["caption"] or f"Objet {i}",
                "prix_ebay": 0, "prix_lbc": 0, "prix_vinted": 0,
                "prix_bas": 0, "prix_moyen": 0, "prix_haut": 0,
                "achat_max": 0, "label_regle": "",
                "etat": "", "categorie": "", "matiere": "",
                "mots_cles": "", "description": "", "conseil": "",
                "titre_lbc": "", "titre_vinted": "",
            })

    session["lot_resultats"] = resultats
    session["lot_index_courant"] = 0
    session["mode"] = "lot_validation"

    await thinking.delete()
    await update.message.reply_text(
        f"✅ Analyse terminée — {nb} objet(s)\n\n"
        f"Je vais vous présenter chaque fiche.\n"
        f"Validez ou refusez un par un.\n\n"
        f"C'est parti !"
    )
    await asyncio.sleep(1)

    # Envoyer la première fiche
    await _envoyer_fiche_lot(update, session, 0)


async def _envoyer_fiche_lot(update_or_query, session: dict, index: int):
    """Envoie une fiche lot avec boutons valider/modifier/refuser."""
    from modules.lot import formater_fiche_lot
    resultats = session.get("lot_resultats", [])

    if index >= len(resultats):
        # Tout traité
        valides = sum(1 for r in resultats if r.get("valide"))
        refus = sum(1 for r in resultats if r.get("refuse"))
        msg = (
            f"🎉 LOT TERMINE\n\n"
            f"✅ Validés et archivés : {valides}\n"
            f"❌ Refusés : {refus}\n"
            f"📦 Total traité : {len(resultats)}\n\n"
            f"Tous les objets validés sont en ligne dans Airtable."
        )
        if hasattr(update_or_query, 'message'):
            await update_or_query.message.reply_text(msg)
        else:
            await update_or_query.message.reply_text(msg)
        session["mode"] = None
        return

    data = resultats[index]
    fiche = formater_fiche_lot(data)

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Valider", callback_data=f"lot_ok|{index}"),
            InlineKeyboardButton("❌ Refuser", callback_data=f"lot_non|{index}"),
        ],
        [InlineKeyboardButton("✏️ Modifier prix", callback_data=f"lot_prix|{index}")]
    ])

    if hasattr(update_or_query, 'message'):
        await update_or_query.message.reply_text(fiche, reply_markup=keyboard)
    else:
        await update_or_query.message.reply_text(fiche, reply_markup=keyboard)


async def cmd_lot_annuler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_user.id)
    nb = len(session.get("lot_photos", []))
    session["mode"] = None
    session["lot_photos"] = []
    session["lot_resultats"] = []
    await update.message.reply_text(f"❌ Lot annulé. {nb} photo(s) supprimées.")

async def cmd_statut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /statut REF-2026-0001 vendu eBay
    Met à jour le statut d'un produit + plateforme si vendu
    """
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage : /statut [REF] [statut] [plateforme optionnelle]\n\n"
            "Exemples :\n"
            "/statut REF-2026-0001 vendu eBay\n"
            "/statut REF-2026-0001 vendu Leboncoin\n"
            "/statut REF-2026-0001 expedie\n"
            "/statut REF-2026-0001 en ligne\n\n"
            "Statuts disponibles :\n"
            "achete, en stockage, en renovation, en ligne, vendu, expedie, livre"
        )
        return

    ref = context.args[0].upper()
    new_status = context.args[1].lower()
    plateforme = " ".join(context.args[2:]) if len(context.args) > 2 else ""

    thinking = await update.message.reply_text(f"🔄 Mise à jour {ref}...")
    try:
        from modules.stock import update_status
        result = await update_status(ref, new_status, plateforme)
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur: {e}")

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
    app.add_handler(CommandHandler("statut", cmd_statut))
    app.add_handler(CommandHandler("vendre", cmd_vendre))
    app.add_handler(CommandHandler("lot_debut", cmd_lot_debut))
    app.add_handler(CommandHandler("lot_analyser", cmd_lot_analyser))
    app.add_handler(CommandHandler("lot_annuler", cmd_lot_annuler))
    app.add_handler(CommandHandler("analyser", cmd_analyser))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("🤖 Bot démarré avec succès !")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
