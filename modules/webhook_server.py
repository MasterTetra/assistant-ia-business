"""
MODULE WEBHOOK SERVER
Reçoit les notifications de vente de :
- eBay (API Notifications)
- Make.com (LBC / Vinted)

Endpoint unique : POST /webhook
Identifie la source via header ou payload, met à jour Airtable, notifie Telegram.
"""
import asyncio
import hashlib
import hmac
import httpx
import json
import logging
import os
from aiohttp import web
from datetime import datetime

from config.settings import (
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS,
    TELEGRAM_TOKEN, EBAY_APP_ID
)

logger = logging.getLogger(__name__)

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Sera injecté depuis bot.py au démarrage
OWNER_CHAT_ID = None
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cashbert-secret-2026")

# ─── AIRTABLE : recherche et mise à jour ──────────────────────────────────────

async def trouver_article_en_ligne(description: str = None, ref: str = None) -> list:
    """Cherche les articles 'en ligne' par référence ou description."""
    try:
        if ref:
            formula = f"AND({{Référence gestion}}='{ref}', {{Statut}}='en ligne')"
        elif description:
            mots = description.lower().split()[:3]
            conditions = " ".join([f"SEARCH('{m}', LOWER({{Description}}))" for m in mots])
            formula = f"AND(OR({conditions}), {{Statut}}='en ligne')"
        else:
            return []

        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={
                    "filterByFormula": formula,
                    "fields[]": ["Référence gestion", "Description", "Statut", "Prix vente", "Prix achat unitaire"],
                    "maxRecords": 100
                }
            )
        return resp.json().get("records", [])
    except Exception as e:
        logger.error(f"trouver_article_en_ligne error: {e}")
        return []


async def mettre_a_jour_statut(record_id: str, statut: str, prix_reel: float = None,
                                plateforme: str = None, date_vente: str = None) -> bool:
    """Met à jour le statut d'un article dans Airtable."""
    try:
        fields = {"Statut": statut}
        if prix_reel:
            fields["Prix vente"] = round(prix_reel, 2)
        if plateforme:
            fields["Plateforme vente"] = plateforme
        if date_vente:
            fields["Date vente"] = date_vente
        elif statut == "vendu":
            fields["Date vente"] = datetime.now().strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS_AT,
                json={"fields": fields}
            )
        return resp.status_code in (200, 201)
    except Exception as e:
        logger.error(f"mettre_a_jour_statut error: {e}")
        return False


# ─── TELEGRAM : notification ─────────────────────────────────────────────────

async def notifier_telegram(message: str, topic: str = None):
    """Envoie une notification dans le supergroupe, dans le bon topic si précisé."""
    from config.settings import SUPERGROUP_ID, TOPICS
    chat_id = SUPERGROUP_ID if SUPERGROUP_ID else OWNER_CHAT_ID
    if not chat_id:
        logger.warning("Aucun chat_id disponible pour la notification")
        return
    thread_id = TOPICS.get(topic) if topic else None
    payload = {"chat_id": chat_id, "text": message}
    if thread_id:
        payload["message_thread_id"] = thread_id
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(f"{TELEGRAM_URL}/sendMessage", json=payload)
    except Exception as e:
        logger.error(f"notifier_telegram error: {e}")


# ─── CALCUL MARGE ────────────────────────────────────────────────────────────

def calculer_marge(prix_achat: float, prix_vente: float, plateforme: str) -> dict:
    from config.settings import PLATFORM_FEES
    taux = PLATFORM_FEES.get(plateforme.lower(), 0) / 100
    frais = round(prix_vente * taux, 2)
    marge_nette = round(prix_vente - prix_achat - frais, 2)
    marge_pct = round(marge_nette / prix_achat * 100) if prix_achat > 0 else 0
    return {
        "prix_vente": prix_vente,
        "prix_achat": prix_achat,
        "frais_plateforme": frais,
        "marge_nette": marge_nette,
        "marge_pct": marge_pct
    }


# ─── HANDLERS EBAY ───────────────────────────────────────────────────────────

async def handle_ebay_notification(payload: dict) -> str:
    """
    Traite une notification eBay.
    Types gérés : FIXED_PRICE_TRANSACTION (vente), ITEM_SHIPPED, ITEM_DELIVERED
    """
    notif_type = payload.get("metadata", {}).get("topic", "")
    data = payload.get("data", {})

    logger.info(f"eBay notification: {notif_type}")

    if notif_type == "MARKETPLACE_ACCOUNT_DELETION":
        return "ok"

    # Vente confirmée
    if "FIXED_PRICE_TRANSACTION" in notif_type or "CHECKOUT_BUYER_OPTED_IN" in notif_type:
        transaction = data.get("transaction", data)
        item_title = transaction.get("item", {}).get("title", "")
        prix_vente = float(transaction.get("priceSummary", {}).get("total", {}).get("value", 0))
        order_id = transaction.get("orderId", "")
        buyer = transaction.get("buyer", {}).get("username", "acheteur")

        from modules.ebay_publish import traiter_vente_ebay
        quantite_vendue = int(data.get("quantitySold", 1))
        result = await traiter_vente_ebay(item_title, quantite_vendue, prix_vente)

        if result["ok"]:
            refs_txt = ", ".join(result["refs_vendues"])
            restant = result["restant"]
            marge = result["marge"]
            marge_pct = result["marge_pct"]
            lot_txt = f" (lot x{quantite_vendue})" if quantite_vendue > 1 else ""
            restant_txt = f"\n📦 Restant en stock : {restant}" if restant > 0 else "\n📦 Stock épuisé"
            msg = (
                f"🛒 VENTE EBAY{lot_txt}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 {item_title[:50]}\n"
                f"👤 Acheteur : {buyer}\n"
                f"💶 Prix vente : {prix_vente}€\n"
                f"📉 Frais eBay : -{result['frais']}€\n"
                f"💰 Marge nette : +{marge}€ ({marge_pct}%)\n"
                f"🔖 Refs : {refs_txt[:80]}\n"
                f"{restant_txt}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Statut → vendu ✅"
            )
        else:
            msg = (
                f"⚠️ VENTE EBAY — traitement partiel\n"
                f"Article : {item_title[:50]}\n"
                f"Erreur : {result.get('error', '?')[:150]}\n"
                f"Prix : {prix_vente}€ — Order : {order_id}"
            )
        await notifier_telegram(msg, topic="sales_notifications")
        return "ok"

    # Expédition confirmée
    if "SHIPPING" in notif_type or "SHIPPED" in notif_type:
        item_title = data.get("item", {}).get("title", "")
        articles = await trouver_article_en_ligne(description=item_title)
        for art in articles[:1]:
            await mettre_a_jour_statut(art["id"], "en cours d'expédition", plateforme="eBay")
            ref = art.get("fields", {}).get("Référence gestion", "?")
            await notifier_telegram(
                f"📦 EXPÉDIÉ — {item_title[:40]}\nRéférence : {ref}\nStatut → expédié ✅"
            )
        return "ok"

    # Livraison confirmée
    if "DELIVERED" in notif_type:
        item_title = data.get("item", {}).get("title", "")
        articles = await trouver_article_en_ligne(description=item_title)
        for art in articles[:1]:
            await mettre_a_jour_statut(art["id"], "livré", plateforme="eBay")
            ref = art.get("fields", {}).get("Référence gestion", "?")
            await notifier_telegram(
                f"✅ LIVRÉ — {item_title[:40]}\nRéférence : {ref}\nStatut → livré ✅"
            )
        return "ok"

    return "ignored"


# ─── HANDLER MAKE.COM (LBC / VINTED) ─────────────────────────────────────────



# ── ANALYSE D'ALERTE ACHAT ────────────────────────────────────────────────────
# Flux : Alerte mail plateforme → Make.com parse le mail → webhook bot → score IA → notification si >=7

SCORE_MINIMUM_ALERTE = 7.0   # Seuil en dessous duquel on n'envoie PAS de notification

async def analyser_alerte_achat(payload: dict) -> str:
    """
    Reçoit une alerte d'achat potentiel depuis Make.com (mail de plateforme parsé).

    Payload attendu :
    {
      "secret": "cashbert-secret-2026",
      "event": "alerte_achat",
      "source": "lbc" | "vinted" | "ebay" | "leboncoin",
      "titre": "iPhone 14 Pro 256Go - Très bon état",
      "prix": 450.0,
      "vendeur": "dupont75" (optionnel),
      "localisation": "Paris 75" (optionnel),
      "lien": "https://www.leboncoin.fr/ad/...",
      "description": "..." (optionnel, extrait du mail)
    }
    """
    secret = payload.get("secret", "")
    if secret != WEBHOOK_SECRET:
        logger.warning("Alerte achat: secret invalide")
        return "unauthorized"

    source    = payload.get("source", "inconnu").lower()
    titre     = payload.get("titre", "")
    prix      = float(payload.get("prix") or 0)
    lien      = payload.get("lien", "")
    vendeur   = payload.get("vendeur", "")
    localisa  = payload.get("localisation", "")
    descr     = payload.get("description", "")[:300]

    if not titre or prix <= 0:
        logger.warning(f"Alerte achat incomplète: titre={titre}, prix={prix}")
        return "incomplete"

    logger.info(f"📨 Alerte achat reçue: [{source}] {titre} — {prix}€")

    # ── Analyse IA rapide : score l'opportunité ───────────────────────────────
    score_data = await _scorer_opportunite(titre, prix, source, descr)
    score = score_data.get("score", 0.0)
    prix_revente = score_data.get("prix_revente", 0)
    marge_estimee = prix_revente - prix if prix_revente > prix else 0
    raison = score_data.get("raison", "")

    logger.info(f"Score alerte [{titre[:30]}]: {score}/10 (seuil={SCORE_MINIMUM_ALERTE})")

    # ── Filtre : ignorer si score insuffisant ─────────────────────────────────
    if score < SCORE_MINIMUM_ALERTE:
        logger.info(f"⛔ Alerte ignorée (score {score} < {SCORE_MINIMUM_ALERTE})")
        return "filtered"

    # ── Notification Telegram ─────────────────────────────────────────────────
    score_emoji = "🟢" if score >= 9 else ("🟡" if score >= 7 else "🔴")
    source_label = {"lbc": "LeBonCoin", "leboncoin": "LeBonCoin",
                    "vinted": "Vinted", "ebay": "eBay"}.get(source, source.upper())

    msg_lines = [
        f"🚨 *OPPORTUNITÉ DÉTECTÉE — {source_label}*",
        f"{score_emoji} *Score : {score:.1f}/10*",
        f"",
        f"📦 *{titre[:60]}*",
        f"💶 Prix demandé : *{prix:.2f}€*",
        f"💰 Prix revente estimé : *{prix_revente:.2f}€*",
        f"📈 Marge estimée : *+{marge_estimee:.2f}€*",
    ]
    if vendeur:
        msg_lines.append(f"👤 Vendeur : {vendeur}")
    if localisa:
        msg_lines.append(f"📍 {localisa}")
    if raison:
        msg_lines.append(f"")
        msg_lines.append(f"📝 {raison}")
    if lien:
        msg_lines.append(f"")
        msg_lines.append(f"🔗 [Voir l'annonce]({lien})")
    msg_lines.append(f"")
    msg_lines.append(f"_⚡ Réponds vite — les bonnes affaires partent vite_")

    msg = "\n".join(msg_lines)
    await notifier_telegram(msg, topic="buy")
    return "ok"


async def _scorer_opportunite(titre: str, prix: float, source: str, description: str = "") -> dict:
    """
    Score rapide d'une opportunité d'achat via Claude (sans web search pour la vitesse).
    Utilise les connaissances du modèle + les données fournies.
    """
    import anthropic as _anthropic
    from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL
    _client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""Tu es un expert en achat-revente d'occasion. Analyse cette annonce rapidement.

ARTICLE : {titre}
PRIX DEMANDÉ : {prix}€
PLATEFORME : {source}
DESCRIPTION : {description or "non fournie"}

Réponds UNIQUEMENT avec ce format (sans markdown) :

PRIX_REVENTE: [prix de revente réaliste en euros]
SCORE: [note de 0 à 10 — opportunité d'achat-revente]
RAISON: [1 phrase justifiant le score]

Critères de scoring :
- 9-10 : affaire exceptionnelle, marge >200%, demande forte
- 7-8 : bonne opportunité, marge >100%, marché actif
- 5-6 : opportunité correcte, marge 50-100%
- <5 : peu intéressant, marge faible ou marché saturé
- Pénalise si : prix trop proche du marché, article banal sans plus-value, état inconnu sur article fragile"""

    try:
        r = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = r.content[0].text if r.content else ""

        import re as _re
        def _get(key):
            m = _re.search(rf'^{key}\s*:\s*(.+)', raw, _re.IGNORECASE | _re.MULTILINE)
            return m.group(1).strip() if m else ""

        prix_rev_str = _get("PRIX_REVENTE")
        prix_rev_nums = _re.findall(r'\d+', prix_rev_str.replace(" ", ""))
        prix_rev = float(prix_rev_nums[0]) if prix_rev_nums else prix * 1.5

        score_str = _get("SCORE")
        score_nums = _re.findall(r'[\d.]+', score_str)
        score = float(score_nums[0]) if score_nums else 5.0
        score = max(0.0, min(10.0, score))

        raison = _get("RAISON")
        return {"score": score, "prix_revente": prix_rev, "raison": raison}

    except Exception as e:
        logger.warning(f"Scoring opportunité failed: {e}")
        # Scoring basique sans IA si l'appel échoue
        ratio = prix_rev_fallback = prix * 2
        score_fallback = 5.0
        return {"score": score_fallback, "prix_revente": ratio, "raison": "Scoring IA indisponible"}


async def handle_makecom_notification(payload: dict) -> str:
    """
    Payload Make.com attendu :
    {
      "source": "lbc" | "vinted",
      "event": "vendu" | "expedie" | "livre",
      "titre": "Nom de l'article",
      "prix": 45.0,
      "ref": "AV-20260312-0001" (optionnel),
      "secret": "cashbert-secret-2026"
    }
    """
    # Vérification secret
    if payload.get("secret") != WEBHOOK_SECRET:
        logger.warning("Make.com webhook: secret invalide")
        return "unauthorized"

    source = payload.get("source", "inconnu").lower()
    event = payload.get("event", "").lower()

    # Router vers l'analyseur d'opportunités si c'est une alerte achat
    if event == "alerte_achat":
        return await analyser_alerte_achat(payload)
    titre_brut = payload.get("titre", "")
    prix = float(payload.get("prix", 0))
    ref = payload.get("ref", None)

    # Nettoyer le titre : retirer les préfixes eBay courants
    import re as _re
    prefixes = [
        r"vous avez vendu\s*:\s*",
        r"you sold\s*:\s*",
        r"article vendu\s*:\s*",
        r"commande confirmée\s*:\s*",
        r"sold\s*:\s*",
    ]
    titre = titre_brut
    for p in prefixes:
        titre = _re.sub(p, "", titre, flags=_re.IGNORECASE).strip()

    statut_map = {
        "vendu": "vendu",
        "expedie": "en cours d'expédition",
        "en cours d'expédition": "en cours d'expédition",
        "livre": "livré",
        "livré": "livré"
    }
    statut = statut_map.get(event)
    if not statut:
        return "ignored"

    articles = await trouver_article_en_ligne(description=titre, ref=ref)
    if not articles:
        await notifier_telegram(
            f"⚠️ VENTE {source.upper()} NON MATCHÉE\n"
            f"Article : {titre}\n"
            f"Prix : {prix}€\n"
            f"Aucun article 'en ligne' trouvé — utilisez /vendu pour mettre à jour manuellement."
        )
        return "not_found"

    rec = articles[0]
    record_id = rec["id"]
    prix_achat = float(rec.get("fields", {}).get("Prix achat unitaire", 0))
    ref_trouvee = rec.get("fields", {}).get("Référence gestion", "?")

    ok = await mettre_a_jour_statut(
        record_id, statut,
        prix_reel=prix if statut == "vendu" else None,
        plateforme=source.upper()
    )

    plateforme_label = source.upper() if source else "INCONNU"

    if ok:
        if statut == "vendu":
            marge = calculer_marge(prix_achat, prix, source)
            msg = (
                f"✅ *VENTE FINALISÉE — {plateforme_label}*\n"
                f"📦 {titre[:50]}\n"
                f"🔖 Réf : `{ref_trouvee}`\n"
                f"💶 Prix vente : *{prix}€*\n"
                f"🏷️ Frais {plateforme_label} : -{marge['frais_plateforme']}€\n"
                f"💰 Marge nette : *+{marge['marge_nette']}€* ({marge['marge_pct']}%)\n"
                f"✅ Statut mis à jour → *vendu*"
            )
            await notifier_telegram(msg, topic="sales")

        elif statut == "en cours d'expédition":
            msg = (
                f"📬 *EN COURS D'EXPÉDITION — {plateforme_label}*\n"
                f"📦 {titre[:50]}\n"
                f"🔖 Réf : `{ref_trouvee}`\n"
                f"💶 Prix prévu : *{prix}€*\n"
                f"⏳ En attente de confirmation livraison"
            )
            await notifier_telegram(msg, topic="sales")

        elif statut == "livré":
            marge = calculer_marge(prix_achat, prix, source)
            msg = (
                f"📦 *LIVRÉ — EN ATTENTE CONFIRMATION ACHETEUR*\n"
                f"📦 {titre[:50]}\n"
                f"🔖 Réf : `{ref_trouvee}`\n"
                f"💶 Prix : *{prix}€*\n"
                f"💰 Marge potentielle : *+{marge['marge_nette']}€* ({marge['marge_pct']}%)\n"
                f"⚠️ Confirmer via /statut {ref_trouvee} vendu dès validation acheteur"
            )
            await notifier_telegram(msg, topic="sales")

    return "ok"


# ─── SERVEUR AIOHTTP ──────────────────────────────────────────────────────────

async def handle_webhook(request: web.Request) -> web.Response:
    """Point d'entrée unique pour tous les webhooks."""
    try:
        content_type = request.headers.get("Content-Type", "").lower()
        logger.info(f"📥 Webhook POST reçu — headers: {dict(request.headers)}")

        if "application/x-www-form-urlencoded" in content_type:
            # Make.com form-data
            data = await request.post()
            payload = dict(data)
            logger.info(f"📥 Webhook form-data: {payload}")
        else:
            body = await request.read()
            logger.info(f"📥 Webhook body: {body[:500]}")
            payload = json.loads(body)
    except Exception as e:
        logger.error(f"Webhook parse error: {e}")
        return web.Response(text="bad request", status=400)

    # Identifier la source
    source = request.headers.get("X-Source", "").lower()
    user_agent = request.headers.get("User-Agent", "").lower()

    if "ebay" in source or "ebay" in user_agent or "metadata" in payload:
        result = await handle_ebay_notification(payload)
    elif "make" in source or "secret" in payload:
        result = await handle_makecom_notification(payload)
    else:
        # Essai Make.com par défaut si secret présent
        if payload.get("secret"):
            result = await handle_makecom_notification(payload)
        else:
            logger.warning(f"Webhook source inconnue: {source}, payload keys: {list(payload.keys())}")
            result = "unknown_source"

    return web.Response(text=result, status=200)


async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    logger.info(f"Health check from {request.remote}")
    return web.Response(text="ok", status=200)


async def handle_ebay_challenge(request: web.Request) -> web.Response:
    """
    eBay vérifie l'endpoint avec un challenge GET avant d'envoyer des notifications.
    Doit retourner {"challengeResponse": hash(challengeCode + verificationToken + endpoint)}
    L'URL endpoint doit être exactement celle qu'eBay a appelée.
    """
    challenge_code = request.query.get("challenge_code", "")
    verification_token = os.getenv("EBAY_VERIFICATION_TOKEN", "cashbert-ebay-verify-2026")
    
    # Reconstruire l'URL exacte sans le query string
    host = request.headers.get("X-Forwarded-Host") or request.host
    scheme = request.headers.get("X-Forwarded-Proto", "https")
    endpoint = f"{scheme}://{host}/webhook"
    
    logger.info(f"📨 eBay challenge reçu: code={challenge_code}, endpoint={endpoint}")
    
    h = hashlib.sha256(f"{challenge_code}{verification_token}{endpoint}".encode()).hexdigest()
    logger.info(f"eBay challenge response: {h}")
    return web.json_response({"challengeResponse": h})


def create_webhook_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/webhook", handle_ebay_challenge)
    app.router.add_post("/webhook", handle_webhook)
    return app


async def start_webhook_server(chat_id: int, port: int = 8080):
    """Démarre le serveur webhook en parallèle du bot Telegram."""
    global OWNER_CHAT_ID
    OWNER_CHAT_ID = chat_id

    app = create_webhook_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"✅ Webhook server démarré sur port {port}")
    return runner


async def traiter_vente_confirmee(payload: dict) -> str:
    """
    Reçoit une vente confirmée depuis Make.com (eBay/LBC/Vinted).
    Déclenche traiter_vente() : archive Sheets + décrément Airtable + suppression si soldé.

    Payload attendu :
    {
      "secret": "cashbert-secret-2026",
      "event": "vente_confirmee",
      "source": "ebay" | "lbc" | "vinted",
      "ref": "AV-20260316-0001",          ← optionnel si titre fourni
      "titre": "Porte-clé Renault Sport",  ← utilisé si pas de ref
      "qte": 1,
      "prix_vente": 8.50,
      "frais_plateforme": 1.10,           ← optionnel (défaut 13%)
      "frais_transport": 0.0              ← optionnel
    }
    """
    secret = payload.get("secret", "")
    if secret != WEBHOOK_SECRET:
        logger.warning("vente_confirmee: secret invalide")
        return "unauthorized"

    source    = payload.get("source", "inconnu").lower()
    ref       = payload.get("ref", "").strip()
    titre     = payload.get("titre", "").strip()
    qte       = int(payload.get("qte") or 1)
    prix_raw  = str(payload.get("prix_vente") or "0").replace(",", ".").replace(" ", "")
    prix      = float(prix_raw) if prix_raw else 0.0
    frais_pf  = float(payload.get("frais_plateforme") or 0)
    frais_tr  = float(payload.get("frais_transport") or 0)

    if prix <= 0:
        logger.warning(f"vente_confirmee: prix invalide ({prix})")
        return "invalid_price"

    if not ref and not titre:
        logger.warning("vente_confirmee: ni ref ni titre fourni")
        return "missing_ref"

    source_label = {"lbc": "LeBonCoin", "leboncoin": "LeBonCoin",
                    "vinted": "Vinted", "ebay": "eBay"}.get(source, source.upper())

    logger.info(f"💰 Vente confirmée [{source_label}]: ref={ref or titre[:30]} — {prix}€ x{qte}")

    try:
        from modules.archive import traiter_vente, _get_lot_by_description

        # Si pas de ref, chercher par titre dans Airtable
        if not ref and titre:
            lot = await _get_lot_by_description(titre)
            if lot:
                ref = lot.get("Référence gestion", "")

        if not ref:
            logger.warning(f"vente_confirmee: lot introuvable pour '{titre}'")
            # Notification d'alerte dans Telegram
            await _notif_telegram(
                f"⚠️ *Vente non traitée*\n"
                f"Plateforme : {source_label}\n"
                f"Article : {titre[:50]}\n"
                f"Prix : {prix}€\n"
                f"_Lot introuvable dans Airtable — traiter manuellement_",
                topic="sales"
            )
            return "lot_not_found"

        result = await traiter_vente(
            ref=ref,
            qte_vendue=qte,
            prix_vente=prix,
            plateforme=source_label,
            frais_plateforme=frais_pf,
            frais_transport=frais_tr,
        )

        if not result.get("ok"):
            logger.error(f"traiter_vente échoué: {result.get('erreur')}")
            return "error"

        # Notification Telegram dans Sales
        lot_solde = result.get("lot_solde", False)
        desc = result.get("description", "?")[:40]
        restant = result.get("qte_restante", 0)
        marge = result.get("marge_brute", 0)
        net = result.get("resultat_net", 0)

        emoji = "🎉" if lot_solde else "✅"
        msg = (
            f"{emoji} *VENTE {source_label.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 {desc}\n"
            f"🔖 {ref} × {qte}\n"
            f"💶 Prix : {prix}€ | Net : {net}€\n"
            f"💰 Marge brute : {marge}€\n"
        )
        if lot_solde:
            msg += f"🏁 *Lot soldé* — archivé + supprimé Airtable\n"
        else:
            msg += f"📊 Restant : {restant} unité(s)\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━"

        await _notif_telegram(msg, topic="sales")
        return "ok"

    except Exception as e:
        logger.error(f"traiter_vente_confirmee: {e}", exc_info=True)
        return "error"


async def _notif_telegram(msg: str, topic: str = "sales"):
    """Envoie une notification Telegram dans le bon topic."""
    from config.settings import SUPERGROUP_ID, TOPICS, TELEGRAM_TOKEN as TG_TOKEN
    # Mapping topic → clé TOPICS
    topic_map = {
        "sales": "sales_notifications",
        "audit": "audit",
        "accounting": "accounting_report",
    }
    topic_key = topic_map.get(topic, topic)
    thread_id = TOPICS.get(topic_key)
    try:
        async with __import__("httpx").AsyncClient(timeout=15) as http:
            await http.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": SUPERGROUP_ID,
                    "message_thread_id": thread_id,
                    "text": msg,
                    "parse_mode": "Markdown"
                }
            )
        logger.info(f"✅ Notif Telegram envoyée — topic={topic_key} thread={thread_id}")
    except Exception as e:
        logger.error(f"_notif_telegram: {e}")
