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

async def notifier_telegram(message: str):
    """Envoie une notification Telegram au propriétaire."""
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID non défini, notification ignorée")
        return
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(
                f"{TELEGRAM_URL}/sendMessage",
                json={"chat_id": OWNER_CHAT_ID, "text": message}
            )
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

        articles = await trouver_article_en_ligne(description=item_title)
        if articles:
            rec = articles[0]
            record_id = rec["id"]
            prix_achat = float(rec.get("fields", {}).get("Prix achat unitaire", 0))
            ref = rec.get("fields", {}).get("Référence gestion", "?")

            ok = await mettre_a_jour_statut(
                record_id, "vendu",
                prix_reel=prix_vente,
                plateforme="eBay"
            )

            if ok:
                marge = calculer_marge(prix_achat, prix_vente, "ebay")
                msg = (
                    f"🛒 VENTE EBAY DÉTECTÉE\n"
                    f"Article : {item_title[:50]}\n"
                    f"Référence : {ref}\n"
                    f"Acheteur : {buyer}\n"
                    f"Prix vente : {prix_vente}€\n"
                    f"Frais eBay : -{marge['frais_plateforme']}€\n"
                    f"Marge nette : +{marge['marge_nette']}€ ({marge['marge_pct']}%)\n"
                    f"Order ID : {order_id}\n\n"
                    f"Statut mis à jour → vendu ✅"
                )
                await notifier_telegram(msg)
        return "ok"

    # Expédition confirmée
    if "SHIPPING" in notif_type or "SHIPPED" in notif_type:
        item_title = data.get("item", {}).get("title", "")
        articles = await trouver_article_en_ligne(description=item_title)
        for art in articles[:1]:
            await mettre_a_jour_statut(art["id"], "expédié", plateforme="eBay")
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
        "expedie": "expédié",
        "expédié": "expédié",
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

    if ok and statut == "vendu":
        marge = calculer_marge(prix_achat, prix, source)
        plateforme_label = "LBC" if "lbc" in source else "Vinted"
        msg = (
            f"🛒 VENTE {plateforme_label.upper()} DÉTECTÉE\n"
            f"Article : {titre_brut[:50]}\n"
            f"Référence : {ref_trouvee}\n"
            f"Prix vente : {prix}€\n"
            f"Frais {plateforme_label} : -{marge['frais_plateforme']}€\n"
            f"Marge nette : +{marge['marge_nette']}€ ({marge['marge_pct']}%)\n\n"
            f"Statut mis à jour → {statut} ✅"
        )
        await notifier_telegram(msg)
    elif ok:
        await notifier_telegram(
            f"📦 {source.upper()} — {titre[:40]}\n"
            f"Référence : {ref_trouvee}\n"
            f"Statut → {statut} ✅"
        )

    return "ok"


# ─── SERVEUR AIOHTTP ──────────────────────────────────────────────────────────

async def handle_webhook(request: web.Request) -> web.Response:
    """Point d'entrée unique pour tous les webhooks."""
    try:
        body = await request.read()
        logger.info(f"📥 Webhook POST reçu — headers: {dict(request.headers)}")
        logger.info(f"📥 Webhook body: {body[:500]}")
        payload = json.loads(body)
    except Exception as e:
        logger.error(f"Webhook parse error: {e}, body: {body[:200]}")
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
