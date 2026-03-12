"""
MODULE ROUTING
Gère l'envoi de messages vers les bons topics du supergroupe Telegram.
"""
import logging
import httpx
from config.settings import TELEGRAM_TOKEN, SUPERGROUP_ID, TOPICS

logger = logging.getLogger(__name__)
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


async def send_to_topic(topic: str, text: str, parse_mode: str = None, **kwargs) -> bool:
    """
    Envoie un message dans le bon topic du supergroupe.
    topic : clé dans TOPICS (ex: "sales_notifications", "post_sell")
    """
    thread_id = TOPICS.get(topic)
    payload = {
        "chat_id": SUPERGROUP_ID,
        "text": text,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    if parse_mode:
        payload["parse_mode"] = parse_mode
    payload.update(kwargs)

    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(f"{TELEGRAM_URL}/sendMessage", json=payload)
        if resp.status_code == 200:
            logger.info(f"✅ Message envoyé dans topic '{topic}' (thread_id={thread_id})")
            return True
        else:
            logger.error(f"❌ Erreur envoi topic '{topic}': {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        logger.error(f"send_to_topic error: {e}")
        return False


async def notify_sale(source: str, titre: str, prix: float, ref: str,
                       frais: float, marge: float, marge_pct: int):
    """Envoie une notification de vente dans Sales Notifications."""
    msg = (
        f"🛒 VENTE {source.upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Article : {titre[:50]}\n"
        f"🏷 Référence : {ref}\n"
        f"💰 Prix vente : {prix}€\n"
        f"📉 Frais {source} : -{frais}€\n"
        f"✅ Marge nette : +{marge}€ ({marge_pct}%)\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await send_to_topic("sales_notifications", msg)


async def notify_new_product(ref: str, titre: str, prix_achat: float,
                              quantite: int, prix_vente_estime: float):
    """Notifie Post&Sell qu'un nouveau produit est prêt à être listé."""
    msg = (
        f"📬 NOUVEAU PRODUIT À LISTER\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 Référence : {ref}\n"
        f"📦 Article : {titre[:50]}\n"
        f"🛒 Prix achat : {prix_achat}€\n"
        f"📦 Quantité : {quantite}\n"
        f"💡 Prix vente estimé : {prix_vente_estime}€\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"➡️ Tape /annonce {ref} pour générer l'annonce"
    )
    await send_to_topic("post_sell", msg)


def get_topic_from_thread_id(thread_id) -> str:
    """Retourne le nom du topic à partir d'un thread_id."""
    for name, tid in TOPICS.items():
        if tid == thread_id:
            return name
    return "general"
