"""
ASSISTANT IA — CENTRE DE GESTION
Bot Telegram principal — compatible Python 3.11/3.12/3.13
"""
import os
import json
import asyncio
import httpx
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


async def smart_reply(update: Update, context, text: str, parse_mode: str = "Markdown", **kwargs):
    """Répond intelligemment — gère les supergroupes avec topics (message_thread_id)."""
    msg = update.effective_message
    chat_id = update.effective_chat.id
    thread_id = msg.message_thread_id if msg else None
    try:
        return await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            message_thread_id=thread_id,
            **kwargs
        )
    except Exception as e:
        logger.error(f"smart_reply error: {e}")
        # Fallback sans thread_id
        return await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)


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
            "flux_prix_total": 0,
            "flux_quantite": 1,
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
    chat_id = update.effective_chat.id
    thread_id = update.effective_message.message_thread_id if update.effective_message else None
    logger.info(f"🔑 /start — chat_id={chat_id}, thread_id={thread_id}, user_id={user.id}")
    if not is_authorized(user.id):
        await smart_reply(update, context, "⛔ Accès non autorisé.")
        return
    await smart_reply(update, context,
        f"👋 Bonjour {user.first_name} !\n\n"
        "🤖 Assistant IA — Gestion Business\n\n"
        "📌 Commandes :\n"
        "📸 Photo → Analyse sourcing automatique\n"
        "🛒 /acheter → Enregistrer un achat\n"
        "📦 /stock → Résumé du stock\n"
        "📊 /rapport → Rapport hebdomadaire\n"
        "💰 /finances → Bilan financier\n"
        "🔍 /chercher nom → Trouver un produit\n"
        "📝 /annonce ref → Générer une annonce\n"
        "❓ /aide → Toutes les commandes",
        parse_mode=None
    )

async def aide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texte = (
        "🤖 *Cashbert — Commandes*\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🛒 *BUY & SEARCH*\n"
        "  📸 Photo + légende → Analyse marché immédiate\n"
        "  🔍 `/recherche [article]` → Analyse texte libre\n"
        "  🔍 `/recherche` + plusieurs lignes → Multi-articles\n"
        "  📦 `/lot` → Lot de photos (jusqu\'à 50)\n"
        "  📦 `/lot analyser` → Lancer l\'analyse du lot\n"
        "  📦 `/lot stop` → Annuler le lot\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 *INVENTORY*\n"
        "  📦 `/stock` → État global du stock\n"
        "  📦 `/stock [REF ou mot-clé]` → Chercher un article\n"
        "  🔄 `/statut [REF] [statut]` → Changer le statut\n"
        "  Statuts : `acheté` `en ligne` `expedition` `livre` `vendu` `stockage` `renovation`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🏪 *POST & SELL*\n"
        "  📝 `/listing` → Sélectionner articles + créer annonces\n"
        "  📝 `/listing [REF]` → Annonce directe\n"
        "  🚀 `/post` → Publier sur eBay\n"
        "  ✅ `/vendre` → Marquer vendu manuellement\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 *RAPPORTS*\n"
        "  📊 `/rapport` → Menu (jour/semaine/mois/année)\n"
        "  📊 `/rapport live` → Dashboard temps réel\n"
        "  📊 `/rapport bilan` → Bilan financier complet\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ *CONFIG*\n"
        "  🔔 `/alertes` → Config alertes opportunités\n"
        "  🔔 `/alertes seuil 7` → Changer le seuil\n"
    )
    await update.message.reply_text(texte, parse_mode="Markdown")


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
            + "\nEnvoie d'autres photos ou tape /terminer_photos",
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

    thinking_msg = await msg.reply_text("🔍 Identification...\n⏳ ~5 secondes")
    try:
        session["last_analysis_time"] = _time.time()
        session["flux_photo_url"] = file_url
        session["flux_caption"] = caption

        # ── PHASE 1 : Analyse rapide sans web search (<5s) ────────────────────
        from modules.flux import analyser_marche_rapide, analyser_marche, formater_analyse
        data = await analyser_marche_rapide(file_url, caption)
        session["flux_data"] = data

        await thinking_msg.delete()

        score = data.get("score", 5.0)
        score_bar = "🟢" if score >= 7 else ("🟡" if score >= 5 else "🔴")
        achat_max_net = data.get("achat_max_net", data.get("achat_max", 0))
        demande = data.get("demande", "?").capitalize()
        vitesse = data.get("vitesse", "?").capitalize()
        prix_rev = data.get("prix_revente", 0)
        confiance = data.get("confiance", "MOYENNE")
        confiance_emoji = "✅" if confiance == "HAUTE" else ("⚠️" if confiance == "MOYENNE" else "❓")

        from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        kb = IKM([[
            IKB("✅ ACHETER", callback_data="flux_acheter"),
            IKB("❌ IGNORER", callback_data="flux_ignorer"),
        ]])

        msg_decision = (
            f"{score_bar} *Score opportunité : {score}/10*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Prix revente estimé : *{prix_rev}€*\n"
            f"🛒 Prix achat max brut : *{data.get('achat_max', 0)}€*\n"
            f"📉 Prix achat max net (~13% frais) : *{achat_max_net}€*\n"
            f"📈 Demande : *{demande}* | Vitesse : *{vitesse}*\n"
            f"{confiance_emoji} Fiabilité estimation : *{confiance}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Tu veux acheter ?"
        )
        await msg.reply_text(msg_decision, parse_mode="Markdown", reply_markup=kb)
        session["mode"] = "flux_attente_achat"

        # ── PHASE 2 : Enrichissement web en arrière-plan ─────────────────────
        # Lance la recherche web APRÈS avoir répondu — ne bloque pas l'utilisateur
        asyncio.create_task(
            _enrichir_analyse_web(msg, session, data, file_url, caption)
        )

    except Exception as e:
        logger.error(f"Erreur flux: {e}")
        await thinking_msg.edit_text(f"⚠️ Erreur : {str(e)[:200]}")



async def _enrichir_analyse_web(msg, session: dict, data_rapide: dict, photo_url: str, caption: str):
    """
    Phase 2 (arrière-plan) : enrichit l'analyse rapide avec une recherche web.
    Envoie un second message uniquement si les prix trouvés diffèrent significativement.
    """
    try:
        await asyncio.sleep(2)  # Laisser le temps à l'utilisateur de lire la Phase 1
        from modules.flux import analyser_marche, formater_analyse
        data_web = await analyser_marche(photo_url, caption)

        if not data_web or not isinstance(data_web, dict):
            return

        prix_rev_web = data_web.get("prix_revente", 0)
        prix_rev_rapide = data_rapide.get("prix_revente", 0)
        score_web = data_web.get("score", 0)

        # N'envoyer le second message que si les données web apportent quelque chose
        if prix_rev_web == 0:
            return  # Recherche web sans résultat — inutile d'envoyer

        # Mettre à jour session avec les données enrichies
        session["flux_data"] = data_web

        # Calculer l'écart entre estimation rapide et données web
        if prix_rev_rapide > 0:
            ecart_pct = abs(prix_rev_web - prix_rev_rapide) / prix_rev_rapide * 100
        else:
            ecart_pct = 100

        score_bar = "🟢" if score_web >= 7 else ("🟡" if score_web >= 5 else "🔴")
        achat_max_net = data_web.get("achat_max_net", data_web.get("achat_max", 0))

        # Construire le message d'enrichissement
        lines = [
            f"📊 *Données marché en temps réel*",
            f"━━━━━━━━━━━━━━━━━━━━",
        ]

        if ecart_pct > 20:
            diff = prix_rev_web - prix_rev_rapide
            sign = "+" if diff > 0 else ""
            lines.append(f"⚡ Prix révisé : *{prix_rev_web}€* ({sign}{diff:.0f}€ vs estimation)")
        else:
            lines.append(f"✅ Estimation confirmée : *{prix_rev_web}€*")

        lines += [
            f"{score_bar} Score révisé : *{score_web}/10*",
            f"🛒 Achat max net : *{achat_max_net}€*",
        ]

        # Ajouter les annonces web si trouvées
        annonces = data_web.get("annonces", [])
        if annonces:
            lines.append("")
            lines.append("🔍 *Annonces trouvées :*")
            for a in annonces[:4]:
                pf = a.get("plateforme", "")
                prix = a.get("prix", "")
                statut = "✅" if "VENDU" in a.get("statut", "").upper() else "🏪"
                lines.append(f"  {statut} {pf} — {prix}")

        await msg.reply_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.info(f"Enrichissement web ignoré (non bloquant): {e}")


async def _afficher_annonce_avec_boutons(msg, data_flux: dict, ref: str):
    """Affiche l'annonce générée avec les boutons de modification."""
    from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
    from modules.flux import formater_annonce
    annonce_txt = formater_annonce(data_flux)
    keyboard = IKM([
        [IKB("✅ Valider annonce", callback_data=f"listing_valider|{ref}")],
        [IKB("💶 Modifier prix", callback_data=f"listing_mod_prix|{ref}"),
         IKB("✏️ Modifier titre", callback_data=f"listing_mod_titre|{ref}")],
        [IKB("📝 Modifier annonce", callback_data=f"listing_mod_annonce|{ref}")],
        [IKB("❌ Annuler", callback_data=f"listing_annuler|{ref}")],
    ])
    if len(annonce_txt) > 3500:
        await msg.reply_text(annonce_txt[:3500])
        await msg.reply_text(annonce_txt[3500:], reply_markup=keyboard)
    else:
        await msg.reply_text(annonce_txt, reply_markup=keyboard)


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

    elif data.startswith("rapport_"):
        periode = data.replace("rapport_", "")  # jour / semaine / mois / annee
        await _generer_rapport(query, periode)

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

    # ── POST : publication depuis /post ──────────────────
    if data == "post_cancel":
        session.pop("post_lots", None)
        await query.edit_message_text("❌ Publication annulée.")
        return

    elif data == "post_all":
        lots = session.get("post_lots", [])
        if not lots:
            await query.edit_message_text("⚠️ Plus rien à publier.")
            return
        await query.edit_message_text(f"🚀 Publication de {len(lots)} annonce(s) en cours...")
        resultats = []
        for lot in lots:
            res = await _publier_lot(lot, context.application)
            resultats.append((lot["titre"][:35], lot["quantite"], res))
        # Résumé
        lignes = ["📊 RÉSUMÉ PUBLICATION\n━━━━━━━━━━━━━━━━━━━━"]
        ok_count = 0
        for titre, qte, res in resultats:
            if res["success"]:
                ok_count += 1
                lignes.append(f"✅ {titre}{'× '+str(qte) if qte > 1 else ''}\n   {res['url']}")
            else:
                lignes.append(f"❌ {titre}\n   {res['error'][:80]}")
        lignes.append(f"\n━━━━━━━━━━━━━━━━━━━━\n{ok_count}/{len(lots)} publiées")
        await query.message.reply_text("\n".join(lignes))
        return

    elif data.startswith("post_step|"):
        idx = int(data.split("|")[1])
        lots = session.get("post_lots", [])
        if idx >= len(lots):
            await query.edit_message_text("✅ Tous les lots ont été traités.")
            return
        lot = lots[idx]
        qte_txt = f" (lot × {lot['quantite']})" if lot['quantite'] > 1 else ""
        photos_count = len([u for u in lot['photos'].split(',') if u.strip()])
        from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        buttons = [
            [IKB(f"✅ Publier{qte_txt}", callback_data=f"post_confirm|{idx}")],
            [IKB("💶 Modifier prix", callback_data=f"post_edit_prix|{idx}")],
            [IKB("⏭ Passer", callback_data=f"post_step|{idx+1}")],
            [IKB("❌ Arrêter", callback_data="post_cancel")],
        ]
        await query.edit_message_text(
            f"[{idx+1}/{len(lots)}] {lot['titre']}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💶 Prix : {lot['prix']:.2f}€  📸 {photos_count} photo(s){qte_txt}\n"
            f"Refs : {', '.join(lot['refs'][:5])}",
            reply_markup=IKM(buttons)
        )
        return

    elif data.startswith("post_confirm|"):
        idx = int(data.split("|")[1])
        lots = session.get("post_lots", [])
        lot = lots[idx]
        await query.edit_message_text(f"⏳ Publication : {lot['titre'][:40]}...")
        res = await _publier_lot(lot, context.application)
        if res["success"]:
            await query.message.reply_text(f"✅ {lot['titre'][:40]}\n🔗 {res['url']}")
        else:
            await query.message.reply_text(f"❌ {lot['titre'][:40]}\n{res['error'][:150]}")
        # Passer au suivant auto
        next_idx = idx + 1
        if next_idx < len(lots):
            from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
            next_lot = lots[next_idx]
            buttons = [
                [IKB(f"✅ Publier", callback_data=f"post_confirm|{next_idx}")],
                [IKB("💶 Modifier prix", callback_data=f"post_edit_prix|{next_idx}")],
                [IKB("⏭ Passer", callback_data=f"post_step|{next_idx+1}")],
            ]
            await query.message.reply_text(
                f"[{next_idx+1}/{len(lots)}] {next_lot['titre']}\n"
                f"💶 {next_lot['prix']:.2f}€  Refs: {', '.join(next_lot['refs'][:3])}",
                reply_markup=IKM(buttons)
            )
        else:
            await query.message.reply_text("🎉 Tous les lots publiés !")
        return

    elif data.startswith("post_edit_prix|"):
        idx = int(data.split("|")[1])
        session["post_edit_idx"] = idx
        session["mode"] = "post_attente_prix"
        await query.edit_message_text(
            f"💶 Nouveau prix pour lot [{idx+1}] ?\nEx: 12.50"
        )
        return

    # ── LISTING : sélection depuis liste ─────────────────
    if data.startswith("listing_select|"):
        ref = data.split("|", 1)[1]
        session["listing_queue"] = [ref]
        session["listing_queue_index"] = 0
        await _lancer_listing_article(query.message, session, ref)
        return

    elif data.startswith("listing_all|"):
        refs_str = data.split("|", 1)[1]
        refs = refs_str.split()
        session["listing_queue"] = refs
        session["listing_queue_index"] = 0
        await _lancer_listing_article(query.message, session, refs[0])
        return

    # ── LISTING : sélection état ──────────────────────────
    if data.startswith("etat|"):
        etat_choisi = data.split("|", 1)[1]
        session["listing_etat"] = etat_choisi
        if etat_choisi == "Neuf":
            # Pas de détails nécessaires, on génère directement
            session["listing_details"] = ""
            session["mode"] = "listing_generation"
            ref = session.get("listing_ref", "?")
            thinking = await query.message.reply_text(f"📝 Génération annonce {ref}...")
            try:
                from modules.flux import generer_annonce, formater_annonce
                from modules.stock import get_product_by_ref
                prod = await get_product_by_ref(ref)
                data_flux = prod if prod else {"objet": ref, "prix_revente": 0, "demande": "?", "prix_moyen": 0, "vitesse": "?"}
                data_flux = await generer_annonce(data_flux, etat=etat_choisi, details="")
                session["listing_data"] = data_flux
                session["mode"] = "listing_validation"
                await thinking.delete()
                await _afficher_annonce_avec_boutons(query.message, data_flux, ref)
            except Exception as e:
                await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")
        else:
            session["mode"] = "listing_attente_details"
            await query.edit_message_text(
                f"État sélectionné : {etat_choisi}\n\n"
                f"Décris les défauts ou particularités (mots-clés ou phrases) :\n"
                f"Ex: rayure sur le couvercle, bouton droit cassé, légères traces d'usure\n\n"
                f"Ou tape - pour ignorer."
            )
        return

    elif data.startswith("listing_valider|"):
        ref = data.split("|", 1)[1]
        session["mode"] = None
        data_flux = session.get("listing_data", {})
        etat = session.get("listing_etat", "Bon etat")
        titre = data_flux.get("titre") or data_flux.get("objet", ref)
        description = data_flux.get("description", "")
        mots_cles = data_flux.get("mots_cles", "")
        prix_vente = float(data_flux.get("prix_vente") or data_flux.get("prix_revente") or 0)
        poids = int(data_flux.get("poids") or 500)

        thinking = await query.message.reply_text("💾 Sauvegarde + publication eBay en cours...")
        try:
            from modules.stock import update_annonce, get_produits_en_ligne_similaires
            from modules.ebay_publish import publier_sur_ebay, sauvegarder_ebay_item_id

            annonce_txt = f"TITRE: {titre}\n\n{description}\n\nMOTS-CLES: {mots_cles}"
            ok = await update_annonce(ref, annonce_txt, etat, prix_vente=prix_vente)
            if not ok:
                await thinking.edit_text("⚠️ Erreur sauvegarde Airtable.")
                return

            # ── Détection lot : articles identiques en ligne ──
            similaires = await get_produits_en_ligne_similaires(titre)
            quantite_lot = len(similaires) if similaires else 1
            ref_principale = similaires[0]["ref"] if similaires else ref

            # ── Récupérer les photos depuis Airtable et convertir les liens Drive ──
            photo_urls = []
            if similaires:
                photos_raw = similaires[0].get("photos_urls", "")
                if photos_raw:
                    from modules.ebay_publish import convertir_liens_drive
                    photo_urls = convertir_liens_drive(photos_raw)
            logger.info(f"📸 Photos pour {ref} : {len(photo_urls)} URL(s) converties")

            # ── Vérifier si une annonce eBay existe déjà pour ce lot ──
            ebay_item_id_existant = similaires[0].get("ebay_item_id", "") if similaires else ""

            if ebay_item_id_existant and quantite_lot > 1:
                # Annonce déjà existante — juste incrémenter la quantité
                from modules.ebay_publish import modifier_quantite_ebay
                await modifier_quantite_ebay(ebay_item_id_existant, quantite_lot)
                await thinking.edit_text(
                    f"✅ ANNONCE VALIDÉE — {ref}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📝 {titre}\n"
                    f"🏷 État : {etat}\n"
                    f"📦 Lot : {quantite_lot} unités sur eBay (quantité mise à jour)\n"
                    f"📍 Statut → en ligne\n"
                    f"━━━━━━━━━━━━━━━━━━━━"
                )
            else:
                # Nouvelle annonce eBay
                result = await publier_sur_ebay(
                    titre=titre,
                    description=f"{description}\n\n{mots_cles}",
                    prix=prix_vente if prix_vente > 0 else 20.0,
                    quantite=quantite_lot,
                    etat=etat,
                    photo_urls=photo_urls,
                    poids_grammes=poids,
                    ref_principale=ref_principale
                )

                if result["success"]:
                    # Sauvegarder l'Item ID sur toutes les refs du lot
                    for s in (similaires or [{"ref": ref}]):
                        await sauvegarder_ebay_item_id(s["ref"], result["item_id"], result["url"])
                    lot_txt = f"\n📦 Lot : {quantite_lot} unités" if quantite_lot > 1 else ""
                    await thinking.edit_text(
                        f"✅ ANNONCE VALIDÉE & PUBLIÉE SUR EBAY\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"📝 {titre}\n"
                        f"🏷 État : {etat}\n"
                        f"💶 Prix : {prix_vente:.2f}€{lot_txt}\n"
                        f"🔗 {result['url']}\n"
                        f"📍 Statut → en ligne\n"
                        f"━━━━━━━━━━━━━━━━━━━━"
                    )
                else:
                    # Publication eBay échouée — statut sauvegardé quand même
                    await thinking.edit_text(
                        f"✅ Annonce sauvegardée — {ref}\n"
                        f"⚠️ Publication eBay échouée :\n"
                        f"{result['error'][:200]}\n\n"
                        f"Vérifie le token eBay dans Railway."
                    )

            # ── Passer au suivant dans la queue ──
            queue = session.get("listing_queue", [])
            idx = session.get("listing_queue_index", 0) + 1
            session["listing_queue_index"] = idx
            if idx < len(queue):
                next_ref = queue[idx]
                await query.message.reply_text(f"➡️ Article suivant : {next_ref}")
                await _lancer_listing_article(query.message, session, next_ref)
            elif len(queue) > 1:
                await query.message.reply_text(f"🎉 Tous les articles listés ! ({len(queue)}/{len(queue)})")

        except Exception as e:
            logger.error(f"listing_valider error: {e}", exc_info=True)
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:300]}")
        return

    elif data.startswith("listing_mod_prix|"):
        ref = data.split("|", 1)[1]
        session["mode"] = "listing_attente_prix"
        session["listing_ref"] = ref
        await query.edit_message_text(
            f"💶 Nouveau prix de vente pour {ref} ?\nEx: 45"
        )
        return

    elif data.startswith("listing_mod_titre|"):
        ref = data.split("|", 1)[1]
        session["mode"] = "listing_attente_titre"
        session["listing_ref"] = ref
        data_flux = session.get("listing_data", {})
        titre_actuel = data_flux.get("titre") or data_flux.get("objet", "")
        await query.edit_message_text(
            f"✏️ Titre actuel :\n{titre_actuel}\n\nTape le nouveau titre :"
        )
        return

    elif data.startswith("listing_mod_annonce|"):
        ref = data.split("|", 1)[1]
        session["mode"] = "listing_attente_modif_annonce"
        session["listing_ref"] = ref
        data_flux = session.get("listing_data", {})
        desc_actuelle = data_flux.get("description", "")
        await query.message.reply_text(
            f"📝 ANNONCE ACTUELLE — copie, modifie et renvoie :\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{desc_actuelle}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Renvoie le texte corrigé tel que tu le veux."
        )
        return

    elif data.startswith("listing_annuler|"):
        session["mode"] = None
        session["listing_data"] = None
        await query.edit_message_text("❌ Génération annulée.")
        return

    elif data == "flux_acheter":
        session = get_session(query.from_user.id)
        data_flux = session.get("flux_data")
        if not data_flux:
            await query.edit_message_text("⚠️ Session expirée. Renvoie une photo.")
            return
        session["mode"] = "flux_attente_prix_achat"
        achat_max = data_flux.get("achat_max", 0)
        await query.edit_message_text(
            f"🛒 ACHAT CONFIRME\n\n"
            f"Prix total payé + quantité :\n"
            f"  6;60   → 6€ pour 60 unités\n"
            f"  400;1  → 400€ pour 1 exemplaire\n\n"
            f"Prix d'achat maximum conseillé : {achat_max}€/unité"
        )

    elif data == "flux_ignorer":
        session = get_session(query.from_user.id)
        session["mode"] = None
        session["flux_data"] = None
        await query.edit_message_text("❌ Opportunité ignorée.")

    elif data == "flux_continuer":
        # Rétrocompat — redirige vers flux_acheter
        session = get_session(query.from_user.id)
        data_flux = session.get("flux_data")
        if not data_flux:
            await query.edit_message_text("⚠️ Session expirée. Renvoie une photo.")
            return
        session["mode"] = "flux_attente_prix_achat"
        await query.edit_message_text("🛒 Format : prix_total;quantite\nEx: 40;1")

    elif data == "flux_valider":
        # Rétrocompat — ce bouton n'est plus dans le flux principal
        # mais reste actif pour les sessions en cours
        session = get_session(query.from_user.id)
        await query.edit_message_text("ℹ️ Utilise le nouveau flux : renvoie une photo et clique ACHETER.")

    elif data == "flux_mod_prix":
        session = get_session(query.from_user.id)
        session["mode"] = "flux_attente_prix"
        await query.edit_message_text(
            "💶 Nouveau prix de vente eBay ?\n"
            "Tapez juste le chiffre, ex: 45"
        )

    elif data == "flux_mod_titre":
        session = get_session(query.from_user.id)
        session["mode"] = "flux_attente_titre"
        await query.message.reply_text(
            "✏️ MODIFIER LE TITRE\n\nTapez le nouveau titre (sera utilisé sur toutes les plateformes) :\nEx: Lampe Argos César Baldaccini Daum France"
        )

    elif data == "flux_mod_ajouter":
        session = get_session(query.from_user.id)
        session["mode"] = "flux_attente_ajout"
        await query.message.reply_text(
            "➕ AJOUTER / RETIRER UNE INFORMATION\n\n"
            "Décrivez ce que vous voulez ajouter ou enlever.\n"
            "Le reste de l\'annonce sera conservé intact.\n\n"
            "Exemples :\n"
            "• ajouter: dimensions 29x12x8 cm, abat-jour non inclus\n"
            "• retirer: la phrase sur la remise en main propre\n"
            "• préciser: système électrique défaillant à réviser"
        )

    elif data == "flux_mod_annonce":
        session = get_session(query.from_user.id)
        session["mode"] = "flux_attente_modif"
        await query.message.reply_text(
            "📝 MODIFIER L\'ANNONCE\n\n"
            "Décrivez ce que vous voulez changer.\n"
            "Le bot reformulera uniquement ce que vous indiquez, sans toucher au reste.\n\n"
            "Exemples :\n"
            "• changer \'neuf\' par \'bon état\'\n"
            "• remplacer la partie livraison par : expédition Mondial Relay\n"
            "• reformuler le premier paragraphe en mettant en avant le sculpteur César"
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
    """
    /stock        → état global du stock par statut
    /stock REF    → chercher un article par référence ou mot-clé
    """
    if context.args:
        q = " ".join(context.args)
        thinking = await update.message.reply_text(f"🔍 Recherche *{q}*...", parse_mode="Markdown")
        try:
            result = await find_product(q)
            await thinking.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur: {e}")
    else:
        thinking = await update.message.reply_text("📦 Analyse du stock...")
        try:
            from modules.reports import generate_stock_report
            result = await generate_stock_report()
            await thinking.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur stock: {e}")




async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /post — Analyse Airtable, regroupe les articles prêts (statut acheté + photos)
    en lots, et publie sur eBay en demandant confirmation.
    """
    session = get_session(update.effective_user.id)
    thinking = await update.message.reply_text("🔍 Analyse du stock en attente...")

    from modules.stock import get_articles_prets_a_poster, grouper_en_lots
    articles = await get_articles_prets_a_poster()

    if not articles:
        await thinking.edit_text(
            "✅ Aucun article prêt à poster.\n"
            "Vérifie que le statut est 'acheté' ET que les Photos URLs sont renseignées dans Airtable."
        )
        return

    lots = grouper_en_lots(articles)
    session["post_lots"] = lots
    session["post_lots_confirmes"] = []

    # Afficher le résumé des lots détectés
    lines = [f"📦 {len(articles)} article(s) → {len(lots)} annonce(s) à publier\n━━━━━━━━━━━━━━━━━━━━"]
    for i, lot in enumerate(lots):
        qte_txt = f" × {lot['quantite']}" if lot['quantite'] > 1 else ""
        prix_txt = f"{lot['prix']:.2f}€" if lot['prix'] else "Prix à définir"
        photos_count = len([u for u in lot['photos'].split(',') if u.strip()])
        lines.append(
            f"\n{'[LOT]' if lot['quantite'] > 1 else '[1]'} {lot['titre'][:45]}{qte_txt}\n"
            f"   💶 {prix_txt}  📸 {photos_count} photo(s)\n"
            f"   Refs : {', '.join(lot['refs'][:3])}{'...' if len(lot['refs']) > 3 else ''}"
        )
    lines.append("\n━━━━━━━━━━━━━━━━━━━━")

    from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
    buttons = [
        [IKB(f"🚀 Tout publier ({len(lots)} annonces)", callback_data="post_all")],
        [IKB("⚙️ Vérifier lot par lot", callback_data="post_step|0")],
        [IKB("❌ Annuler", callback_data="post_cancel")],
    ]
    await thinking.edit_text("\n".join(lines), reply_markup=IKM(buttons))


async def _publier_lot(lot: dict, app) -> dict:
    """Publie un lot sur eBay et met à jour Airtable pour toutes les refs du lot."""
    from modules.ebay_publish import publier_sur_ebay, sauvegarder_ebay_item_id, convertir_liens_drive
    from modules.stock import update_annonce
    import re as _re

    titre = lot["titre"]
    annonce = lot["annonce"]
    prix = float(lot["prix"]) if lot["prix"] else 20.0
    quantite = lot["quantite"]
    photo_urls = convertir_liens_drive(lot["photos"])

    # Extraire description et mots-clés de l'annonce (format : DESCRIPTION\n...\nMOTS-CLES\n...)
    desc_match = _re.search(r"DESCRIPTION\s*\n(.+?)\nMOTS-CLES", annonce, _re.DOTALL)
    if desc_match:
        description = desc_match.group(1).strip()
    else:
        # Fallback : tout ce qui est entre PRIX et MOTS-CLES
        desc_match2 = _re.search(r"PRIX[^\n]+\n(.+?)(?:MOTS-CLES|$)", annonce, _re.DOTALL)
        description = desc_match2.group(1).strip() if desc_match2 else annonce[:2000]
    mots_match = _re.search(r"MOTS-CLES\s*:?\s*\n?(.+)", annonce, _re.DOTALL)
    mots_cles = mots_match.group(1).strip()[:500] if mots_match else ""

    # Extraire l'état depuis les Notes (si disponible)
    etat = "Bon etat"

    result = await publier_sur_ebay(
        titre=titre,
        description=f"{description}\n\n{mots_cles}",
        prix=prix,
        quantite=quantite,
        etat=etat,
        photo_urls=photo_urls,
    )

    if result["success"]:
        # Mettre à jour toutes les refs du lot dans Airtable
        for ref in lot["refs"]:
            await update_annonce(ref, annonce, etat, prix_vente=prix)
            await sauvegarder_ebay_item_id(ref, result["item_id"], result["url"])

    return result

async def cmd_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /listing          → liste tous les articles avec statut 'acheté'
    /listing REF      → génère l'annonce pour cette référence
    /listing REF1 REF2 REF3 → génère annonces pour plusieurs références
    """
    session = get_session(update.effective_user.id)

    # ── Sans argument : afficher la liste des articles achetés ──
    if not context.args:
        thinking = await update.message.reply_text("📋 Chargement des articles à lister...")
        try:
            from modules.stock import get_produits_achetes
            produits = await get_produits_achetes()
            if not produits:
                await thinking.edit_text(
                    "✅ Aucun article en attente de listing.\n"
                    "Tous les achats ont déjà une annonce."
                )
                return

            lines = ["📋 ARTICLES À LISTER\n━━━━━━━━━━━━━━━━━━━━"]
            for p in produits:
                lines.append(
                    f"🔖 {p['ref']}\n"
                    f"   {p['description'][:40]}\n"
                    f"   Achat: {p['prix_achat']}€ → Revente: {p['prix_vente']}€"
                )
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("Tape /listing REF pour une annonce")
            lines.append("Tape /listing REF1 REF2 REF3 pour plusieurs")

            # Boutons pour sélection rapide (max 10)
            from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
            buttons = []
            for p in produits[:10]:
                buttons.append([IKB(
                    f"{p['ref']} — {p['description'][:25]}",
                    callback_data=f"listing_select|{p['ref']}"
                )])
            # Bouton "Tout lister"
            if len(produits) > 1:
                all_refs = " ".join(p["ref"] for p in produits[:10])
                buttons.append([IKB(f"📦 Tout lister ({len(produits)} articles)", callback_data=f"listing_all|{all_refs}")])

            await thinking.edit_text("\n".join(lines), reply_markup=IKM(buttons))
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")
        return

    # ── Avec argument(s) : générer les annonces ──
    refs = [r.upper() for r in context.args]
    session["listing_queue"] = refs
    session["listing_queue_index"] = 0

    # Lancer le premier article
    await _lancer_listing_article(update.message, session, refs[0])


async def _lancer_listing_article(msg, session, ref: str):
    """Lance le flux de création d'annonce pour un article."""
    session["listing_ref"] = ref
    session["mode"] = "listing_attente_etat"

    from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
    nb_total = len(session.get("listing_queue", [ref]))
    idx = session.get("listing_queue_index", 0)
    header = f"[{idx+1}/{nb_total}] " if nb_total > 1 else ""

    kb = IKM([
        [IKB("🆕 Neuf", callback_data="etat|Neuf")],
        [IKB("⭐ Très bon état", callback_data="etat|Tres bon etat")],
        [IKB("✅ Bon état", callback_data="etat|Bon etat")],
        [IKB("🟡 Satisfaisant", callback_data="etat|Satisfaisant")],
        [IKB("🔧 Pour pièces", callback_data="etat|Pour pieces")],
    ])
    await msg.reply_text(
        f"📦 {header}Génération annonce — {ref}\n\nQuel est l'état de l'objet ?",
        reply_markup=kb
    )

async def cmd_rapport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /rapport              → menu périodes
    /rapport jour         → journalier
    /rapport semaine      → 7 derniers jours
    /rapport mois         → mois en cours
    /rapport annee        → année en cours
    /rapport live         → dashboard temps réel (ex /dashboard)
    /rapport bilan        → bilan financier complet (ex /finances)
    """
    arg = (context.args[0].lower() if context.args else "").strip()

    # Sous-commandes directes
    if arg == "live":
        thinking = await update.message.reply_text("⚡ Chargement dashboard...")
        try:
            from modules.accounting import get_realtime_dashboard
            result = await get_realtime_dashboard()
            await thinking.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur: {e}")
        return

    if arg == "bilan":
        thinking = await update.message.reply_text("💰 Calcul du bilan financier...")
        try:
            from modules.accounting import get_financial_summary
            result = await get_financial_summary()
            await thinking.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur: {e}")
        return

    periodes = {"jour": "jour", "journalier": "jour", "semaine": "semaine",
                "hebdo": "semaine", "mois": "mois", "mensuel": "mois",
                "annee": "annuel", "annuel": "annuel", "année": "annuel"}

    if arg in periodes:
        thinking = await update.message.reply_text("📊 Génération du rapport...")
        try:
            from modules.reports import generate_report
            result = await generate_report(periodes[arg])
            await thinking.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur: {e}")
        return

    # Menu boutons si aucun argument
    from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
    kb = IKM([
        [IKB("📅 Aujourd'hui", callback_data="rapport_jour"),
         IKB("📅 7 jours",     callback_data="rapport_semaine")],
        [IKB("📅 Ce mois",     callback_data="rapport_mois"),
         IKB("📅 Cette année", callback_data="rapport_annuel")],
        [IKB("⚡ Dashboard live", callback_data="rapport_live"),
         IKB("💰 Bilan complet",  callback_data="rapport_bilan")],
    ])
    await update.message.reply_text("📊 *Quel rapport veux-tu ?*", parse_mode="Markdown", reply_markup=kb)




async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rapport détaillé du stock par statut."""
    thinking = await update.message.reply_text("📦 Analyse du stock...")
    try:
        from modules.reports import generate_stock_report
        result = await generate_stock_report()
        await thinking.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await thinking.edit_text(f"⚠️ Erreur stock: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        logger.info(f"handle_text: UNAUTHORIZED user_id={user_id}")
        return
    session = get_session(user_id)
    text = update.message.text.strip()
    logger.info(f"handle_text: mode={session.get('mode')!r} text={text!r}")

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
    logger.info(f"handle_text: mode={session.get('mode')!r} text={text!r}")
    if session.get("mode") == "flux_attente_prix_achat":
        raw = update.message.text.strip().replace(" ", "").replace("€", "").replace(",", ".")
        try:
            if ";" in raw:
                parts = raw.split(";")
                # Prix total — peut être décimal (ex: 1.50)
                prix_total = float(parts[0].replace(",", "."))
                # Quantité OU prix unitaire décimal
                part2 = parts[1].replace(",", ".")
                if float(part2) < 1 and "." in part2:
                    # Format prix_total;prix_unitaire (ex: 1;0.10)
                    prix_unitaire = float(part2)
                    quantite = round(prix_total / prix_unitaire) if prix_unitaire > 0 else 1
                else:
                    # Format prix_total;quantite (ex: 6;60)
                    quantite = int(float(part2))
                    if quantite <= 0:
                        raise ValueError("quantite invalide")
                    prix_unitaire = round(prix_total / quantite, 4)
            else:
                prix_total = float(raw.replace(",", "."))
                quantite = 1
                prix_unitaire = prix_total
            if prix_total <= 0:
                raise ValueError("prix invalide")
        except (ValueError, IndexError, ZeroDivisionError):
            await update.message.reply_text(
                "Format invalide. Exemples :\n"
                "  `6;60`   → 6€ pour 60 unités (0.10€/u)\n"
                "  `1;0.10` → 1€ total à 0.10€/u (=10 unités)\n"
                "  `400;1`  → 400€ pour 1 exemplaire\n"
                "  `2.50`   → 2.50€ pour 1 article",
                parse_mode="Markdown"
            )
            return

        session["flux_prix_achat"] = prix_unitaire
        session["flux_prix_total"] = prix_total
        session["flux_quantite"] = quantite
        data_flux = session.get("flux_data") or {}
        achat_max = data_flux.get("achat_max", 0)
        prix_revente_estime = data_flux.get("prix_revente", 0)

        # Demander le prix de vente souhaité avant d'archiver
        session["mode"] = "flux_attente_prix_vente"
        marge_estimee = round(prix_revente_estime - prix_unitaire, 2)
        marge_pct = round(marge_estimee / prix_unitaire * 100) if prix_unitaire > 0 else 0

        await update.message.reply_text(
            f"💶 Achat : *{prix_total}€* ({prix_unitaire}€/u × {quantite})\n"
            f"📈 Revente estimée par le bot : *{prix_revente_estime}€* (+{marge_estimee}€ soit +{marge_pct}%)\n\n"
            f"*À quel prix veux-tu le mettre en vente ?*\n"
            f"_Tape le prix en euros (ex: `12` ou `9.90`)_\n"
            f"_Ou tape `ok` pour utiliser l'estimation du bot ({prix_revente_estime}€)_",
            parse_mode="Markdown"
        )
        return

    if session.get("mode") == "flux_attente_prix_vente":
        raw_pv = update.message.text.strip().replace("€", "").replace(",", ".").lower()
        data_flux = session.get("flux_data") or {}
        prix_unitaire = session.get("flux_prix_achat", 0)
        prix_total = session.get("flux_prix_total", 0)
        quantite = session.get("flux_quantite", 1)
        prix_revente_estime = data_flux.get("prix_revente", 0)

        if raw_pv in ("ok", "oui", "yes", ""):
            prix_vente = prix_revente_estime
        else:
            try:
                prix_vente = float(raw_pv)
                if prix_vente <= 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text(
                    "⚠️ Format invalide. Tape un prix (ex: `12`) ou `ok` pour l'estimation.",
                    parse_mode="Markdown"
                )
                return

        # Mettre à jour le prix de vente dans les données
        data_flux["prix_revente"] = prix_vente
        data_flux["prix_vente"] = prix_vente
        session["flux_data"] = data_flux
        session["mode"] = None

        prix_revente = prix_vente
        achat_max = data_flux.get("achat_max", 0)
        marge_u = round(prix_revente - prix_unitaire, 2)
        marge_pct = round(marge_u / prix_unitaire * 100) if prix_unitaire > 0 else 0
        marge_totale = round(marge_u * quantite, 2)
        marge_ok = (achat_max == 0) or (prix_unitaire == 0) or (prix_unitaire <= achat_max)
        statut_icon = "✅" if marge_ok else "⚠️"
        statut_txt = "BON ACHAT" if marge_ok else f"AU-DESSUS du seuil ({achat_max}€/u conseillé)"

        # Archiver dans Airtable
        thinking = await update.message.reply_text("💾 Création fiche produit...")
        try:
            from modules.flux import generer_ref, archiver
            ref = await generer_ref()
            source = session.get("flux_source", "sourcing")
            refs = await archiver(data_flux, ref, prix_total, source, quantite)
            session["flux_data"] = None
            if refs:
                recap = (
                    f"✅ ACHAT CONFIRME\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏷 {data_flux.get('objet', 'Article')}\n"
                    f"🔖 Ref : {refs[0]}"
                    + (f" → {refs[-1]}" if len(refs) > 1 else "") +
                    f"\n💶 {prix_unitaire}€/u × {quantite}"
                    + (f" = {prix_total}€" if quantite > 1 else "") +
                    f"\n\n📊 RENTABILITE\n"
                    f"  Revente estimée : {prix_revente}€\n"
                    f"  Marge/unite     : +{marge_u}€ (+{marge_pct}%)\n"
                    f"  Marge totale    : +{marge_totale}€\n"
                    f"  {statut_icon} {statut_txt}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"📦 Fiche créée → General (Inventory)\n"
                    f"➡️ /listing {refs[0]} dans Post&Sell pour créer l'annonce"
                )
                await thinking.edit_text(recap)
            else:
                # Tenter une insertion de diagnostic pour voir le vrai message Airtable
                import httpx as _httpx
                from config.settings import AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS
                _url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{TABLE_PRODUITS}"
                _h = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}
                _test = {"fields": {"Référence": "TEST-DIAG", "Description": "test"}}
                try:
                    async with _httpx.AsyncClient(timeout=10) as _http:
                        _r = await _http.post(_url, headers=_h, json=_test)
                    await thinking.edit_text(
                        f"⚠️ Erreur Airtable\n"
                        f"Code : {_r.status_code}\n"
                        f"Détail : {_r.text[:300]}"
                    )
                except Exception as _e:
                    await thinking.edit_text(f"⚠️ Erreur Airtable — {str(_e)[:200]}")
        except Exception as e:
            logger.error(f"archiver error: {e}", exc_info=True)
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")
        return

    # ── LISTING : saisie détails état ─────────────────────
    if session.get("mode") == "listing_attente_details":
        details = text if text != "-" else ""
        session["listing_details"] = details
        session["mode"] = "listing_generation"
        ref = session.get("listing_ref", "?")
        etat = session.get("listing_etat", "Bon etat")
        thinking = await update.message.reply_text(f"📝 Génération annonce {ref}...")
        try:
            from modules.flux import generer_annonce
            from modules.stock import get_product_by_ref
            prod = await get_product_by_ref(ref)
            data_flux = prod if prod else {"objet": ref, "prix_revente": 0, "demande": "?", "prix_moyen": 0, "vitesse": "?"}
            data_flux = await generer_annonce(data_flux, etat=etat, details=details)
            session["listing_data"] = data_flux
            session["mode"] = "listing_validation"
            await thinking.delete()
            await _afficher_annonce_avec_boutons(update.message, data_flux, ref)
        except Exception as e:
            logger.error(f"generer_annonce error: {e}", exc_info=True)
            await thinking.edit_text(f"⚠️ Erreur génération : {str(e)[:200]}")
        return

    elif session.get("mode") == "post_attente_prix":
        try:
            nouveau_prix = float(text.replace(",", ".").replace("€", "").strip())
            idx = session.get("post_edit_idx", 0)
            lots = session.get("post_lots", [])
            if idx < len(lots):
                lots[idx]["prix"] = nouveau_prix
                session["post_lots"] = lots
            session["mode"] = None
            lot = lots[idx]
            from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
            buttons = [
                [IKB(f"✅ Publier × {lot['quantite']}", callback_data=f"post_confirm|{idx}")],
                [IKB("⏭ Passer", callback_data=f"post_step|{idx+1}")],
            ]
            await update.message.reply_text(
                f"💶 Prix mis à jour : {nouveau_prix}€\n{lot['titre'][:40]}",
                reply_markup=IKM(buttons)
            )
        except ValueError:
            await update.message.reply_text("⚠️ Format invalide. Ex: 12.50")
        return

    elif session.get("mode") == "listing_attente_prix":
        try:
            nouveau_prix = float(text.replace(",", ".").replace("€", "").strip())
        except ValueError:
            await update.message.reply_text("⚠️ Tapez juste un nombre, ex: 45")
            return
        ref = session.get("listing_ref", "?")
        data_flux = session.get("listing_data", {})
        data_flux["prix_revente"] = nouveau_prix
        session["listing_data"] = data_flux
        session["mode"] = "listing_validation"
        await _afficher_annonce_avec_boutons(update.message, data_flux, ref)
        return

    elif session.get("mode") == "listing_attente_titre":
        ref = session.get("listing_ref", "?")
        data_flux = session.get("listing_data", {})
        data_flux["titre"] = text
        data_flux["titre_ebay"] = text
        data_flux["titre_lbc"] = text
        data_flux["titre_vinted"] = text
        session["listing_data"] = data_flux
        session["mode"] = "listing_validation"
        await _afficher_annonce_avec_boutons(update.message, data_flux, ref)
        return

    elif session.get("mode") == "listing_attente_modif_annonce":
        # L'utilisateur renvoie le texte corrigé — on le réécrit tel quel
        ref = session.get("listing_ref", "?")
        data_flux = session.get("listing_data", {})
        thinking = await update.message.reply_text("✏️ Application de ta correction...")
        try:
            from anthropic import AsyncAnthropic
            from modules.flux import CLAUDE_MODEL
            _client = AsyncAnthropic()
            r = await _client.messages.create(
                model=CLAUDE_MODEL, max_tokens=1000,
                messages=[{"role": "user", "content": (
                    f"Voici une description d'annonce que l'utilisateur a corrigée :\n\n"
                    f"{text}\n\n"
                    f"Réécris-la proprement : corrige les fautes d'orthographe évidentes, "
                    f"améliore la ponctuation si nécessaire, mais CONSERVE exactement le sens, "
                    f"le contenu et la structure. Ne rajoute rien. Ne retire rien.\n\n"
                    f"Réponds UNIQUEMENT avec la description réécrite, sans commentaire."
                )}]
            )
            nouvelle_desc = r.content[0].text.strip() if r.content else text
            data_flux["description"] = nouvelle_desc
            session["listing_data"] = data_flux
            session["mode"] = "listing_validation"
            await thinking.delete()
            await _afficher_annonce_avec_boutons(update.message, data_flux, ref)
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur : {str(e)[:200]}")
        return

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
            nouveau_prix = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("⚠️ Tapez juste un nombre, ex: 45")
            return
        data_flux = session.get("flux_data", {})
        data_flux["prix_revente"] = nouveau_prix
        session["flux_data"] = data_flux
        session["mode"] = "flux_validation"
        from modules.flux import formater_annonce
        from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        annonce_txt = formater_annonce(data_flux)
        keyboard = IKM([
            [IKB("✅ Valider", callback_data="flux_valider")],
            [IKB("💶 Modifier prix", callback_data="flux_mod_prix"), IKB("✏️ Modifier titre", callback_data="flux_mod_titre")],
            [IKB("➕ Ajouter / Retirer", callback_data="flux_mod_ajouter"), IKB("📝 Modifier annonce", callback_data="flux_mod_annonce")],
            [IKB("❌ Annuler", callback_data="flux_annuler")]
        ])
        await update.message.reply_text(annonce_txt, reply_markup=keyboard)

    elif session.get("mode") == "flux_attente_titre":
        titre = update.message.text.strip()
        data_flux = session.get("flux_data", {})
        data_flux["titre"] = titre
        data_flux["titre_ebay"] = titre
        data_flux["titre_lbc"] = titre
        data_flux["titre_vinted"] = titre
        session["flux_data"] = data_flux
        session["mode"] = "flux_validation"
        from modules.flux import formater_annonce
        from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        annonce_txt = formater_annonce(data_flux)
        keyboard = IKM([
            [IKB("✅ Valider", callback_data="flux_valider")],
            [IKB("💶 Modifier prix", callback_data="flux_mod_prix"), IKB("✏️ Modifier titre", callback_data="flux_mod_titre")],
            [IKB("➕ Ajouter / Retirer", callback_data="flux_mod_ajouter"), IKB("📝 Modifier annonce", callback_data="flux_mod_annonce")],
            [IKB("❌ Annuler", callback_data="flux_annuler")]
        ])
        await update.message.reply_text(annonce_txt, reply_markup=keyboard)
        return

    elif session.get("mode") == "flux_attente_ajout":
        ajout = update.message.text.strip()
        ajout_lower = ajout.lower()
        data_flux = session.get("flux_data", {})

        # Retrait direct du CONSEIL ou MOTS-CLES sans appel Claude
        if any(w in ajout_lower for w in ["conseil", "retirer: conseil", "supprimer conseil", "enlever conseil"]):
            data_flux["conseil"] = ""
        elif any(w in ajout_lower for w in ["mots-cles", "mots clés", "keywords"]) and any(w in ajout_lower for w in ["retirer", "supprimer", "enlever"]):
            data_flux["mots_cles"] = ""
        else:
            # Appel Claude pour intégrer l'ajout dans la description
            from anthropic import AsyncAnthropic
            from modules.flux import CLAUDE_MODEL
            _client = AsyncAnthropic()
            desc_actuelle = data_flux.get("description", "")
            conseil_actuel = data_flux.get("conseil", "")
            titre_actuel = data_flux.get("titre") or data_flux.get("titre_ebay", "")
            annonce_complete = f"DESCRIPTION:\n{desc_actuelle}"
            if conseil_actuel:
                annonce_complete += f"\n\nCONSEIL : {conseil_actuel}"
            prompt = (
                f"Voici une annonce de vente en ligne :\n\n"
                f"TITRE: {titre_actuel}\n\n"
                f"{annonce_complete}\n\n"
                f"Instruction : {ajout}\n\n"
                f"Applique cette instruction. Si l\'instruction concerne le CONSEIL, modifie le CONSEIL. "
                f"Si elle concerne la DESCRIPTION, modifie la DESCRIPTION. "
                f"Conserve tout le reste intact.\n"
                f"Réponds avec ce format exact :\n"
                f"DESCRIPTION:\n[texte description]\nFIN_DESC\n"
                f"CONSEIL:\n[texte conseil ou vide si supprimé]\nFIN_CONSEIL"
            )
            think = await update.message.reply_text("✏️ Mise à jour en cours...")
            try:
                r = await _client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=800,
                    messages=[{"role": "user", "content": prompt}]
                )
                raw = r.content[0].text.strip() if r.content else ""
                import re as _re
                desc_m = _re.search(r"DESCRIPTION:\n(.*?)\nFIN_DESC", raw, _re.DOTALL)
                conseil_m = _re.search(r"CONSEIL:\n(.*?)\nFIN_CONSEIL", raw, _re.DOTALL)
                if desc_m:
                    data_flux["description"] = desc_m.group(1).strip()
                if conseil_m:
                    data_flux["conseil"] = conseil_m.group(1).strip()
            except Exception:
                pass
            await think.delete()
        session["flux_data"] = data_flux
        session["mode"] = "flux_validation"
        from modules.flux import formater_annonce
        from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        annonce_txt = formater_annonce(data_flux)
        keyboard = IKM([
            [IKB("✅ Valider", callback_data="flux_valider")],
            [IKB("💶 Modifier prix", callback_data="flux_mod_prix"), IKB("✏️ Modifier titre", callback_data="flux_mod_titre")],
            [IKB("➕ Ajouter / Retirer", callback_data="flux_mod_ajouter"), IKB("📝 Modifier annonce", callback_data="flux_mod_annonce")],
            [IKB("❌ Annuler", callback_data="flux_annuler")]
        ])
        await update.message.reply_text(annonce_txt, reply_markup=keyboard)
        return

    elif session.get("mode") == "flux_attente_modif":
        modif = update.message.text.strip()
        modif_lower = modif.lower()
        data_flux = session.get("flux_data", {})

        # Retrait direct du CONSEIL sans appel Claude
        if any(w in modif_lower for w in ["conseil", "enlever conseil", "supprimer conseil", "retirer conseil"]):
            data_flux["conseil"] = ""
        else:
            from anthropic import AsyncAnthropic
            from modules.flux import CLAUDE_MODEL
            _client = AsyncAnthropic()
            desc_actuelle = data_flux.get("description", "")
            conseil_actuel = data_flux.get("conseil", "")
            titre_actuel = data_flux.get("titre") or data_flux.get("titre_ebay", "")
            annonce_complete = f"DESCRIPTION:\n{desc_actuelle}"
            if conseil_actuel:
                annonce_complete += f"\n\nCONSEIL : {conseil_actuel}"
            prompt = (
                f"Voici une annonce de vente en ligne :\n\n"
                f"TITRE: {titre_actuel}\n\n"
                f"{annonce_complete}\n\n"
                f"Modification demandée : {modif}\n\n"
                f"Applique UNIQUEMENT cette modification. Ne change rien d\'autre.\n"
                f"Réponds avec ce format exact :\n"
                f"DESCRIPTION:\n[texte]\nFIN_DESC\n"
                f"CONSEIL:\n[texte ou vide]\nFIN_CONSEIL"
            )
            think = await update.message.reply_text("📝 Reformulation en cours...")
            try:
                r = await _client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=800,
                    messages=[{"role": "user", "content": prompt}]
                )
                raw = r.content[0].text.strip() if r.content else ""
                import re as _re
                desc_m = _re.search(r"DESCRIPTION:\n(.*?)\nFIN_DESC", raw, _re.DOTALL)
                conseil_m = _re.search(r"CONSEIL:\n(.*?)\nFIN_CONSEIL", raw, _re.DOTALL)
                if desc_m:
                    data_flux["description"] = desc_m.group(1).strip()
                if conseil_m:
                    data_flux["conseil"] = conseil_m.group(1).strip()
            except Exception:
                pass
            await think.delete()
        session["flux_data"] = data_flux
        session["mode"] = "flux_validation"
        from modules.flux import formater_annonce
        from telegram import InlineKeyboardMarkup as IKM, InlineKeyboardButton as IKB
        annonce_txt = formater_annonce(data_flux)
        keyboard = IKM([
            [IKB("✅ Valider", callback_data="flux_valider")],
            [IKB("💶 Modifier prix", callback_data="flux_mod_prix"), IKB("✏️ Modifier titre", callback_data="flux_mod_titre")],
            [IKB("➕ Ajouter / Retirer", callback_data="flux_mod_ajouter"), IKB("📝 Modifier annonce", callback_data="flux_mod_annonce")],
            [IKB("❌ Annuler", callback_data="flux_annuler")]
        ])
        await update.message.reply_text(annonce_txt, reply_markup=keyboard)

    elif session.get("mode") == "flux_attente_source":
        session["flux_source"] = update.message.text.strip()
        session["mode"] = "flux_validation"
        await update.message.reply_text(f"✅ Source : {session['flux_source']}")

    elif session.get("mode") == "lot_modif_prix":
        try:
            import re as _re
            nouveau_prix = int(_re.findall(r'\d+', update.message.text)[0])
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



async def cmd_lot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /lot          → démarrer la collecte de photos en lot
    /lot analyser → lancer l'analyse (alias de /lot_analyser)
    /lot stop     → annuler le lot en cours
    """
    arg = (context.args[0].lower() if context.args else "")
    if arg in ("analyser", "analyse", "go", "lancer"):
        await cmd_lot_analyser(update, context)
    elif arg in ("stop", "annuler", "cancel", "non"):
        await cmd_lot_annuler(update, context)
    else:
        await cmd_lot_debut(update, context)

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

async def cmd_alertes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /alertes          → affiche la config actuelle
    /alertes seuil 7  → change le seuil minimum de score
    /alertes test     → simule une alerte pour tester
    """
    from modules.webhook_server import SCORE_MINIMUM_ALERTE
    import modules.webhook_server as ws

    if not context.args:
        await update.message.reply_text(
            f"🔔 *Configuration des alertes*\n\n"
            f"Seuil actuel : *{ws.SCORE_MINIMUM_ALERTE}/10*\n"
            f"_(seules les annonces avec score ≥ {ws.SCORE_MINIMUM_ALERTE} déclenchent une notification)_\n\n"
            f"*Commandes :*\n"
            f"`/alertes seuil 7` — notifier à partir de 7/10\n"
            f"`/alertes seuil 8` — notifier à partir de 8/10 (strict)\n"
            f"`/alertes seuil 6` — notifier à partir de 6/10 (large)\n"
            f"`/alertes test` — envoyer une alerte test\n\n"
            f"*Comment configurer les alertes plateformes :*\n"
            f"1️⃣ LeBonCoin → Mes alertes → activer les notifications mail\n"
            f"2️⃣ Vinted → Mes recherches sauvegardées → notifications mail\n"
            f"3️⃣ eBay → Recherches sauvegardées → alerte par mail\n"
            f"4️⃣ Make.com parse le mail et envoie au webhook\n"
            f"5️⃣ Le bot score et notifie si ≥ seuil",
            parse_mode="Markdown"
        )
        return

    cmd = context.args[0].lower()

    if cmd == "seuil" and len(context.args) >= 2:
        try:
            nouveau_seuil = float(context.args[1])
            if not 1 <= nouveau_seuil <= 10:
                await update.message.reply_text("⚠️ Le seuil doit être entre 1 et 10.")
                return
            ws.SCORE_MINIMUM_ALERTE = nouveau_seuil
            emoji = "🟢" if nouveau_seuil <= 6 else ("🟡" if nouveau_seuil <= 8 else "🔴")
            await update.message.reply_text(
                f"{emoji} Seuil mis à jour : *{nouveau_seuil}/10*\n"
                f"_Tu recevras une notification pour toute annonce avec score ≥ {nouveau_seuil}_",
                parse_mode="Markdown"
            )
        except ValueError:
            await update.message.reply_text("⚠️ Valeur invalide. Ex: `/alertes seuil 7`", parse_mode="Markdown")

    elif cmd == "test":
        # Simuler une alerte pour vérifier que tout fonctionne
        from modules.webhook_server import analyser_alerte_achat
        test_payload = {
            "secret": "cashbert-secret-2026",
            "event": "alerte_achat",
            "source": "lbc",
            "titre": "iPhone 14 Pro 256Go Noir - Très bon état",
            "prix": 450.0,
            "vendeur": "test_vendeur",
            "localisation": "Paris 75001",
            "lien": "https://www.leboncoin.fr/ad/test",
            "description": "iPhone 14 Pro 256Go Noir, batterie 89%, avec boîte et accessoires."
        }
        thinking = await update.message.reply_text("🔔 Simulation d'alerte en cours...")
        try:
            result = await analyser_alerte_achat(test_payload)
            if result == "filtered":
                await thinking.edit_text(
                    f"✅ Test OK — alerte reçue mais filtrée\n"
                    f"_(score inférieur au seuil de {ws.SCORE_MINIMUM_ALERTE}/10)_\n\n"
                    f"Essaie `/alertes seuil 5` pour abaisser le seuil et recevoir la notification de test.",
                    parse_mode="Markdown"
                )
            elif result == "ok":
                await thinking.edit_text("✅ Test OK — notif envoyée dans le canal Buy & Search !")
            else:
                await thinking.edit_text(f"⚠️ Résultat: {result}")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur test: {e}")
    else:
        await update.message.reply_text("Usage: `/alertes`, `/alertes seuil 7`, `/alertes test`", parse_mode="Markdown")


async def cmd_recherche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /recherche iPhone 14 Pro 256Go
    /recherche iPhone 14 Pro, Airpods Pro 2, MacBook Air M2
    /recherche
    Casquette Nike Dri-Fit
    Porte-clé Renault Sport
    Veste The North Face

    Analyse le marché pour 1 ou plusieurs articles, retourne prix + liens + score.
    """
    from modules.flux import recherche_texte, recherche_multiple, formater_recherche, formater_rapport_multiple

    # Récupérer le texte brut du message
    texte_complet = update.message.text or ""
    # Enlever la commande (/recherche ou /search)
    for cmd in ["/recherche", "/search"]:
        if texte_complet.startswith(cmd):
            texte_complet = texte_complet[len(cmd):].strip()
            break

    if not texte_complet:
        await update.message.reply_text(
            "🔍 *Recherche marché*\n\n"
            "*1 article :*\n"
            "`/recherche iPhone 14 Pro 256Go`\n\n"
            "*Plusieurs articles (une ligne par article) :*\n"
            "`/recherche`\n"
            "`tamagotchi bleu`\n"
            "`display one piece 1ère édition française`\n"
            "`lampe cesar baldaccini daum`\n\n"
            "Le bot cherche sur eBay, LBC, Vinted et retourne :\n"
            "  • Fourchette de prix réelle + liens annonces actives\n"
            "  • Prix d\'achat max conseillé\n"
            "  • Score opportunité /10",
            parse_mode="Markdown"
        )
        return

    # Parser les requêtes — priorité aux sauts de ligne, puis virgules
    if "\n" in texte_complet:
        queries = [q.strip() for q in texte_complet.split("\n") if q.strip() and len(q.strip()) > 2]
    elif "," in texte_complet:
        queries = [q.strip() for q in texte_complet.split(",") if q.strip() and len(q.strip()) > 2]
    elif ";" in texte_complet:
        queries = [q.strip() for q in texte_complet.split(";") if q.strip() and len(q.strip()) > 2]
    else:
        queries = [texte_complet.strip()]

    if not queries:
        await update.message.reply_text("⚠️ Précise au moins un article à rechercher.")
        return

    # Limiter à 5 articles max
    if len(queries) > 5:
        queries = queries[:5]
        await update.message.reply_text(f"⚠️ Maximum 5 articles — je traite les 5 premiers.")

    nb = len(queries)
    thinking = await update.message.reply_text(
        f"🔍 Recherche en cours pour *{nb}* article(s)...\n"
        f"⏳ Environ {nb * 8}-{nb * 15} secondes",
        parse_mode="Markdown"
    )

    try:
        if nb == 1:
            data = await recherche_texte(queries[0])
            result = formater_recherche(data)
            await thinking.edit_text(result, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            liste = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(queries))
            await thinking.edit_text(
                f"🔍 Analyse de *{nb}* articles en cours...\n{liste}\n\n"
                f"⏳ ~{nb * 20}-{nb * 30}s (séquentiel pour fiabilité)\n"
                f"_Une fiche par article sera envoyée_",
                parse_mode="Markdown"
            )
            results = await recherche_multiple(queries)
            try:
                await thinking.delete()
            except Exception:
                pass
            # Fiches individuelles triées par score (les meilleures d'abord)
            ok = [r for r in results if not r.get("erreur")]
            errors = [r for r in results if r.get("erreur")]
            for data in sorted(ok, key=lambda x: x["score"], reverse=True):
                await update.message.reply_text(
                    formater_recherche(data),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            for data in errors:
                await update.message.reply_text(
                    formater_recherche(data),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            # Résumé final si au moins 2 succès
            if len(ok) >= 2:
                rapport = formater_rapport_multiple(ok)
                await update.message.reply_text(
                    rapport,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
    except Exception as e:
        logger.error(f"Erreur recherche: {e}", exc_info=True)
        err_str = str(e)
        if "529" in err_str or "overloaded" in err_str.lower():
            msg = "⏳ *API surchargée* — réessaie dans 1-2 minutes.\n\n_C'est un problème temporaire côté Anthropic._"
        elif "429" in err_str:
            msg = "⏳ *Trop de requêtes* — réessaie dans 1 minute."
        else:
            msg = f"⚠️ Erreur: {err_str[:150]}"
        await thinking.edit_text(msg, parse_mode="Markdown")


async def cmd_statut(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /statut                         → menu pour choisir article + statut
    /statut AV-20260316-0001 vendu  → mise à jour directe
    /statut AV-20260316-0001 vendu eBay → avec plateforme
    """
    from modules.stock import update_status

    # ── Mode direct : /statut REF STATUT [PLATEFORME] ────────────────────────
    if len(context.args) >= 2:
        ref = context.args[0].upper()
        new_status = context.args[1].lower()
        # Gérer statuts multi-mots : "en cours" "en ligne" etc.
        if len(context.args) >= 3:
            # Distinguer statut multi-mots vs plateforme
            statuts_multi = ["en ligne", "en cours", "en stockage", "en rénovation"]
            two_words = f"{context.args[1].lower()} {context.args[2].lower()}"
            if two_words in statuts_multi or two_words.startswith("en "):
                new_status = two_words
                plateforme = " ".join(context.args[3:]) if len(context.args) > 3 else ""
            else:
                plateforme = " ".join(context.args[2:])
        else:
            plateforme = ""

        thinking = await update.message.reply_text(f"🔄 Mise à jour {ref}...")
        try:
            result = await update_status(ref, new_status, plateforme)
            await thinking.edit_text(result, parse_mode="Markdown")
        except Exception as e:
            await thinking.edit_text(f"⚠️ Erreur: {e}")
        return

    # ── Mode sans args : afficher aide avec exemples ──────────────────────────
    texte = (
        "🔄 *Mise à jour de statut*\n\n"
        "*Usage :*\n"
        "`/statut REF nouveau_statut [plateforme]`\n\n"
        "*Statuts disponibles :*\n"
        "  🛒 `acheté` — acheté, pas encore en vente\n"
        "  🟢 `en ligne` — publié sur une plateforme\n"
        "  📬 `expedition` — en cours d\'expédition\n"
        "  📦 `livre` — livré, attente confirmation\n"
        "  ✅ `vendu` — vente finalisée\n"
        "  🏭 `stockage` — en stockage\n"
        "  🔧 `renovation` — en rénovation\n\n"
        "*Exemples :*\n"
        "`/statut AV-20260316-0001 vendu eBay`\n"
        "`/statut AV-20260316-0001 expedition`\n"
        "`/statut AV-20260316-0001 livre`\n"
        "`/statut AV-20260316-0001 en ligne`"
    )
    await update.message.reply_text(texte, parse_mode="Markdown")

# ─── LANCEMENT COMPATIBLE TOUTES VERSIONS PYTHON ─────────
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("❌ TELEGRAM_TOKEN manquant")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ── Commandes actives ────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("aide", aide))

    # Buy & Search
    app.add_handler(CommandHandler("recherche", cmd_recherche))   # Recherche texte + marché
    app.add_handler(CommandHandler("lot", cmd_lot))               # Lot : /lot | /lot analyser | /lot stop

    # Inventory
    app.add_handler(CommandHandler("stock", cmd_stock))           # /stock | /stock REF
    app.add_handler(CommandHandler("statut", cmd_statut))         # Changer statut d'un article

    # Post & Sell
    app.add_handler(CommandHandler("listing", cmd_listing))       # Sélectionner + créer annonce
    app.add_handler(CommandHandler("post", cmd_post))             # Publier sur eBay
    app.add_handler(CommandHandler("vendre", cmd_vendre))         # Marquer vendu manuellement

    # Reports
    app.add_handler(CommandHandler("rapport", cmd_rapport))       # /rapport | /rapport live | /rapport bilan

    # Config
    app.add_handler(CommandHandler("alertes", cmd_alertes))       # Seuil alertes opportunités

    # ── Rétrocompat (anciens noms redirigent vers les nouvelles commandes) ───
    app.add_handler(CommandHandler("help", aide))
    app.add_handler(CommandHandler("search", cmd_recherche))
    app.add_handler(CommandHandler("chercher", cmd_stock))
    app.add_handler(CommandHandler("annonce", cmd_listing))
    app.add_handler(CommandHandler("finances", cmd_rapport))
    app.add_handler(CommandHandler("dashboard", cmd_rapport))
    app.add_handler(CommandHandler("lot_debut", cmd_lot_debut))
    app.add_handler(CommandHandler("lot_analyser", cmd_lot_analyser))
    app.add_handler(CommandHandler("lot_annuler", cmd_lot_annuler))
    app.add_handler(CommandHandler("analyser", cmd_analyser))
    app.add_handler(CommandHandler("terminer_photos", cmd_terminer_photos))
    app.add_handler(CommandHandler("acheter", cmd_acheter))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # DEBUG TEMPORAIRE — log tous les updates reçus
    async def debug_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"🐛 UPDATE reçu: chat_id={update.effective_chat.id if update.effective_chat else '?'} "
                    f"thread_id={update.message.message_thread_id if update.message else '?'} "
                    f"user_id={update.effective_user.id if update.effective_user else '?'} "
                    f"text={update.message.text[:30] if update.message and update.message.text else '?'}")
    from telegram.ext import TypeHandler
    app.add_handler(TypeHandler(Update, debug_all), group=1)

    logger.info("🤖 Bot démarré avec succès !")

    async def _run_all(application):
        import asyncio as _asyncio
        from modules.webhook_server import start_webhook_server
        port = int(os.getenv("PORT", "8080"))

        # Démarrer webhook server
        webhook_runner = await start_webhook_server(chat_id=2134299043, port=port)

        # S'abonner aux notifications eBay au démarrage
        try:
            from modules.ebay_setup import setup_notifications
            await setup_notifications()
        except Exception as e:
            logger.warning(f"eBay setup skipped: {e}")

        # Démarrer le bot dans la même boucle
        async with application:
            await application.initialize()
            await application.start()
            # Attendre que Telegram expire l'ancienne session (évite 409 Conflict)
            logger.info("⏳ Attente 15s avant polling (anti-409)...")
            await asyncio.sleep(15)
            await application.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            logger.info("✅ Bot + Webhook en ligne")
            # Attendre indéfiniment
            stop_event = _asyncio.Event()
            try:
                await stop_event.wait()
            except (KeyboardInterrupt, SystemExit):
                pass
            finally:
                await application.updater.stop()
                await application.stop()
                await webhook_runner.cleanup()

    import asyncio as _asyncio
    _asyncio.run(_run_all(app))

if __name__ == "__main__":
    main()
