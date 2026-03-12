"""
MODULE EBAY SETUP
S'abonne automatiquement aux notifications de vente eBay au démarrage du bot.
Utilise l'API Trading eBay (SetNotificationPreferences).
"""
import httpx
import logging
import os

logger = logging.getLogger(__name__)

TRADING_API_URL = "https://api.ebay.com/ws/api.dll"
WEBHOOK_URL = "https://assistant-ia-business-production.up.railway.app/webhook"


def _get_headers(call_name: str) -> dict:
    return {
        "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        "X-EBAY-API-DEV-NAME": os.getenv("EBAY_DEV_ID", ""),
        "X-EBAY-API-APP-NAME": os.getenv("EBAY_APP_ID", ""),
        "X-EBAY-API-CERT-NAME": os.getenv("EBAY_CERT_ID", ""),
        "X-EBAY-API-CALL-NAME": call_name,
        "X-EBAY-API-SITEID": "71",  # eBay France
        "Content-Type": "text/xml",
    }


async def setup_notifications():
    """
    S'abonne aux notifications eBay :
    - FixedPriceTransaction (vente immédiate)
    - AuctionCheckoutComplete (enchère terminée)
    - ItemShipped (expédition)
    Appelé une fois au démarrage du bot.
    """
    user_token = os.getenv("EBAY_USER_TOKEN", "")
    cert_id = os.getenv("EBAY_CERT_ID", "")

    if not user_token or not cert_id:
        logger.warning("⚠️ EBAY_USER_TOKEN ou EBAY_CERT_ID manquant — notifications eBay désactivées")
        return

    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<SetNotificationPreferencesRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{user_token}</eBayAuthToken>
    </RequesterCredentials>
    <ApplicationDeliveryPreferences>
        <ApplicationURL>{WEBHOOK_URL}</ApplicationURL>
        <ApplicationEnable>Enable</ApplicationEnable>
        <AlertEmail>mailto://none@none.com</AlertEmail>
        <AlertEnable>Disable</AlertEnable>
        <DeviceType>Platform</DeviceType>
    </ApplicationDeliveryPreferences>
    <UserDeliveryPreferenceArray>
        <NotificationEnable>
            <EventType>FixedPriceTransaction</EventType>
            <EventEnable>Enable</EventEnable>
        </NotificationEnable>
        <NotificationEnable>
            <EventType>AuctionCheckoutComplete</EventType>
            <EventEnable>Enable</EventEnable>
        </NotificationEnable>
        <NotificationEnable>
            <EventType>ItemShipped</EventType>
            <EventEnable>Enable</EventEnable>
        </NotificationEnable>
    </UserDeliveryPreferenceArray>
</SetNotificationPreferencesRequest>"""

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                TRADING_API_URL,
                headers=_get_headers("SetNotificationPreferences"),
                content=xml.encode("utf-8")
            )
        
        if "Success" in resp.text:
            logger.info("✅ Notifications eBay activées (FixedPriceTransaction, ItemShipped)")
        elif "Failure" in resp.text:
            logger.error(f"❌ eBay SetNotificationPreferences échoué: {resp.text[:300]}")
        else:
            logger.warning(f"eBay setup réponse inattendue: {resp.text[:200]}")

    except Exception as e:
        logger.error(f"eBay setup error: {e}")
