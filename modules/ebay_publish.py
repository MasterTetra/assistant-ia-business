"""
MODULE EBAY PUBLISH
# VERSION 24 — Fix ShippingDetails, description parsing, Site supprimé
Publication d'annonces sur eBay via API Trading XML.
Gère les lots (1 annonce multi-stock) et la mise à jour des quantités.
"""
import httpx
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from config.settings import (
    EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, EBAY_USER_TOKEN,
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS
)

logger = logging.getLogger(__name__)

EBAY_API_URL = "https://api.ebay.com/ws/api.dll"
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

EBAY_HEADERS = {
    "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
    "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
    "X-EBAY-API-APP-NAME": EBAY_APP_ID,
    "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
    "X-EBAY-API-SITEID": "71",  # 71 = eBay France
    "Content-Type": "text/xml",
}

# Catégories eBay France fréquentes (à affiner selon les articles)
CATEGORIES_DEFAUT = {
    "default": "99",        # Divers
    "vetement": "11450",    # Vêtements
    "electronique": "293",  # Électronique
    "maison": "11700",      # Maison
    "sport": "888",         # Sport
    "jouet": "220",         # Jouets
    "accessoire": "169291", # Accessoires mode
}

# Frais eBay selon prix de vente
FRAIS_EBAY_PCT = 0.13  # 13%


def echapper_xml(texte: str) -> str:
    """Échappe les caractères spéciaux pour le XML eBay."""
    if not texte:
        return ""
    return (texte
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def convertir_liens_drive(photos_raw: str) -> list:
    """
    Convertit les liens Google Drive partagés en URLs directes compatibles eBay.
    Utilise le format /thumbnail?id=FILE_ID&sz=s1600 (sans redirection, stable).
    Formats acceptés :
      - https://drive.google.com/file/d/FILE_ID/view?...
      - https://drive.google.com/open?id=FILE_ID
      - https://drive.google.com/uc?export=view&id=FILE_ID
    """
    import re as _re
    if not photos_raw:
        return []
    urls = []
    for raw in photos_raw.split(","):
        raw = raw.strip()
        if not raw:
            continue
        # Extraire le FILE_ID depuis n'importe quel format Drive
        file_id = None
        m = _re.search(r"/file/d/([a-zA-Z0-9_-]+)", raw)
        if m:
            file_id = m.group(1)
        else:
            m = _re.search(r"[?&]id=([a-zA-Z0-9_-]+)", raw)
            if m:
                file_id = m.group(1)
        if file_id:
            # Format thumbnail — direct, sans redirection, sans & problématique
            urls.append(f"https://drive.google.com/thumbnail?id={file_id}&sz=s1600")
        elif raw.startswith("http"):
            urls.append(raw)
    return urls


def _ebay_call(call_name: str, xml_body: str) -> str:
    """Effectue un appel synchrone à l'API eBay Trading."""
    headers = {**EBAY_HEADERS, "X-EBAY-API-CALL-NAME": call_name}
    full_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<{call_name}Request xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  {xml_body}
</{call_name}Request>"""
    import httpx as _httpx
    import asyncio
    # Version synchrone pour compatibilité
    response = _httpx.post(EBAY_API_URL, headers=headers, content=full_xml, timeout=30)
    return response.text


async def _ebay_call_async(call_name: str, xml_body: str) -> str:
    """Effectue un appel asynchrone à l'API eBay Trading."""
    headers = {**EBAY_HEADERS, "X-EBAY-API-CALL-NAME": call_name}
    full_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<{call_name}Request xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  {xml_body}
</{call_name}Request>"""
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(EBAY_API_URL, headers=headers, content=full_xml.encode("utf-8"))
    return resp.text


def _parse_xml(xml_text: str) -> ET.Element:
    try:
        # Supprimer le namespace pour simplifier
        xml_clean = re.sub(r' xmlns="[^"]+"', '', xml_text)
        return ET.fromstring(xml_clean)
    except Exception as e:
        logger.error(f"XML parse error: {e}\n{xml_text[:300]}")
        return None


def _get_xml_val(root: ET.Element, path: str) -> str:
    if root is None:
        return ""
    el = root.find(path)
    return el.text.strip() if el is not None and el.text else ""


def _build_photos_xml(photo_urls: list) -> str:
    """Génère le XML pour les photos eBay (max 12). Échappe les & dans les URLs."""
    if not photo_urls:
        return ""
    pics = "\n".join(
        f"<PictureURL>{url.strip().replace('&', '&amp;')}</PictureURL>"
        for url in photo_urls[:12]
        if url.strip()
    )
    return f"<PictureDetails>{pics}</PictureDetails>" if pics else ""


def _detect_categorie(titre: str, description: str) -> str:
    """Détecte automatiquement la catégorie eBay selon le contenu."""
    texte = (titre + " " + description).lower()
    if any(w in texte for w in ["t-shirt", "veste", "pantalon", "robe", "casquette", "chaussure", "vêtement"]):
        return CATEGORIES_DEFAUT["vetement"]
    if any(w in texte for w in ["téléphone", "smartphone", "laptop", "ordinateur", "écran", "câble", "console"]):
        return CATEGORIES_DEFAUT["electronique"]
    if any(w in texte for w in ["canapé", "chaise", "table", "lampe", "meuble", "cuisine", "maison"]):
        return CATEGORIES_DEFAUT["maison"]
    if any(w in texte for w in ["vélo", "tennis", "ski", "foot", "sport", "fitness"]):
        return CATEGORIES_DEFAUT["sport"]
    if any(w in texte for w in ["jouet", "lego", "figurine", "jeu", "enfant"]):
        return CATEGORIES_DEFAUT["jouet"]
    if any(w in texte for w in ["sac", "ceinture", "bijou", "montre", "porte-clé", "portefeuille"]):
        return CATEGORIES_DEFAUT["accessoire"]
    return CATEGORIES_DEFAUT["default"]


async def publier_sur_ebay(
    titre: str,
    description: str,
    prix: float,
    quantite: int,
    etat: str,
    photo_urls: list,
    poids_grammes: int = 500,
    ref_principale: str = ""
) -> dict:
    """
    Publie une annonce sur eBay.
    Retourne {"success": bool, "item_id": str, "url": str, "error": str}
    """
    # Mapper l'état vers les valeurs eBay
    etat_ebay_map = {
        "Neuf": "New",
        "Tres bon etat": "Like New",
        "Très bon état": "Like New",
        "Bon etat": "Used",
        "Bon état": "Used",
        "Satisfaisant": "Acceptable",
        "Pour pieces": "For parts or not working",
        "Pour pièces": "For parts or not working",
    }
    condition_id_map = {
        "New": "1000",
        "Like New": "3000",
        "Used": "4000",
        "Acceptable": "5000",
        "For parts or not working": "7000",
    }
    etat_ebay = etat_ebay_map.get(etat, "Used")
    condition_id = condition_id_map.get(etat_ebay, "4000")

    categorie = _detect_categorie(titre, description)
    # Convertir les liens Drive en URLs directes si nécessaire
    photo_urls_direct = convertir_liens_drive(",".join(photo_urls)) if photo_urls else []
    photos_xml = _build_photos_xml(photo_urls_direct)

    # Échapper tout le contenu texte pour XML
    # Nettoyer titre et description : supprimer tout tag XML parasite
    import re as _re
    def _nettoyer(texte):
        # Supprimer balises XML
        texte = _re.sub(r'<[^>]+>', '', texte)
        # Supprimer lignes parasites (TITRE:, PRIX:, MOTS-CLES:)
        texte = _re.sub(r'^(TITRE|PRIX|MOTS.CLES)\s*:.*$', '', texte, flags=_re.MULTILINE)
        # Nettoyer lignes vides multiples
        texte = _re.sub('\\n{3,}', '\\n\\n', texte).strip()
        return texte

    titre_safe = echapper_xml(_nettoyer(titre)[:80])
    desc_safe = echapper_xml(_nettoyer(description))

    # Durée de l'annonce (GTC = Good Till Cancelled)
    xml_body = f"""
  <Item>
    <Title>{titre_safe}</Title>
    <Description>{desc_safe}</Description>
    <PrimaryCategory><CategoryID>{categorie}</CategoryID></PrimaryCategory>
    <StartPrice currencyID="EUR">{prix:.2f}</StartPrice>
    <ConditionID>{condition_id}</ConditionID>
    <Country>FR</Country>
    <Currency>EUR</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <Quantity>{quantite}</Quantity>
    <ReturnPolicy>
      <ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>
      <RefundOption>MoneyBack</RefundOption>
      <ReturnsWithinOption>Days_30</ReturnsWithinOption>
      <ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>
    </ReturnPolicy>
    <ShippingDetails>
      <ShippingType>Flat</ShippingType>
      <ShippingServiceOptions>
        <ShippingServicePriority>1</ShippingServicePriority>
        <ShippingService>FR_LaPosteColissimo</ShippingService>
        <ShippingServiceCost currencyID="EUR">0.00</ShippingServiceCost>
        <FreeShipping>true</FreeShipping>
      </ShippingServiceOptions>
    </ShippingDetails>
    <ShipToLocations>FR</ShipToLocations>
    {photos_xml}
  </Item>
"""

    try:
        logger.info(f"📤 XML COMPLET envoyé à eBay:\n{xml_body}")
        resp_xml = await _ebay_call_async("AddFixedPriceItem", xml_body)
        logger.info(f"eBay AddFixedPriceItem réponse: {resp_xml[:500]}")
        root = _parse_xml(resp_xml)
        ack = _get_xml_val(root, "Ack")
        item_id = _get_xml_val(root, "ItemID")
        errors = root.findall("Errors") if root is not None else []

        if ack in ("Success", "Warning") and item_id:
            url = f"https://www.ebay.fr/itm/{item_id}"
            logger.info(f"✅ eBay publié : {item_id} — {url}")
            return {"success": True, "item_id": item_id, "url": url, "error": ""}
        else:
            err_msgs = []
            for e in errors:
                code = _get_xml_val(e, "ErrorCode")
                msg = _get_xml_val(e, "LongMessage") or _get_xml_val(e, "ShortMessage")
                err_msgs.append(f"[{code}] {msg}")
            error = " | ".join(err_msgs) or f"Ack={ack}"
            logger.error(f"❌ eBay ECHEC: {error}")
            return {"success": False, "item_id": "", "url": "", "error": error}
    except Exception as e:
        logger.error(f"ebay publish error: {e}", exc_info=True)
        return {"success": False, "item_id": "", "url": "", "error": str(e)}


async def modifier_quantite_ebay(item_id: str, nouvelle_quantite: int) -> bool:
    """Décrémente la quantité d'une annonce eBay active."""
    if nouvelle_quantite <= 0:
        # Terminer l'annonce
        xml_body = f"<ItemID>{item_id}</ItemID><EndingReason>NotAvailable</EndingReason>"
        resp = await _ebay_call_async("EndFixedPriceItem", xml_body)
    else:
        xml_body = f"""
  <Item>
    <ItemID>{item_id}</ItemID>
    <Quantity>{nouvelle_quantite}</Quantity>
  </Item>"""
        resp = await _ebay_call_async("ReviseFixedPriceItem", xml_body)

    root = _parse_xml(resp)
    ack = _get_xml_val(root, "Ack")
    ok = ack in ("Success", "Warning")
    if not ok:
        logger.error(f"modifier_quantite_ebay ECHEC: {resp[:300]}")
    return ok


async def get_refs_lot(titre: str, statut: str = "en ligne") -> list:
    """
    Retourne toutes les refs Airtable avec le même titre et le statut donné.
    Utilisé pour détecter les lots et gérer les ventes.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={
                    "filterByFormula": f"AND({{Statut}}='{statut}', FIND('{titre[:30]}', {{Description}})>0)",
                    "fields[]": ["Référence gestion", "Description", "eBay Item ID", "Prix achat unitaire"],
                    "maxRecords": 200,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc"
                }
            )
        return resp.json().get("records", [])
    except Exception as e:
        logger.error(f"get_refs_lot error: {e}")
        return []


async def sauvegarder_ebay_item_id(ref: str, item_id: str, url: str) -> bool:
    """Sauvegarde l'eBay Item ID dans Airtable pour la référence donnée."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            # Trouver le record
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={"filterByFormula": f"{{Référence gestion}}='{ref}'", "maxRecords": 1}
            )
        records = resp.json().get("records", [])
        if not records:
            return False
        record_id = records[0]["id"]
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS_AT,
                json={"fields": {
                    "eBay Item ID": item_id,
                    "Notes": f"eBay: {url}",
                    "Plateforme vente": "eBay"
                }}
            )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"sauvegarder_ebay_item_id error: {e}")
        return False


async def traiter_vente_ebay(titre_vendu: str, quantite_vendue: int, prix_vente: float) -> dict:
    """
    Traite une vente eBay détectée via Make.com :
    1. Trouve les refs "en ligne" correspondant au titre
    2. Passe les X premières à "vendu"
    3. Décrémente la quantité sur eBay
    4. Retourne le résumé pour notification
    """
    try:
        records = await get_refs_lot(titre_vendu, statut="en ligne")
        if not records:
            return {"ok": False, "error": f"Aucun article '{titre_vendu[:30]}' en ligne trouvé"}

        refs_a_vendre = records[:quantite_vendue]
        item_id = records[0]["fields"].get("eBay Item ID", "")
        date_vente = datetime.now().strftime("%Y-%m-%d")
        refs_vendues = []

        async with httpx.AsyncClient(timeout=30) as http:
            for rec in refs_a_vendre:
                record_id = rec["id"]
                ref = rec["fields"].get("Référence gestion", "?")
                prix_achat = rec["fields"].get("Prix achat unitaire", 0)
                await http.patch(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                    headers=HEADERS_AT,
                    json={"fields": {
                        "Statut": "vendu",
                        "Date vente": date_vente,
                        "Plateforme vente": "eBay",
                        "Prix vente": prix_vente,
                    }}
                )
                refs_vendues.append({"ref": ref, "prix_achat": prix_achat})

        # Décrémenter quantité eBay
        nouvelle_qte = len(records) - quantite_vendue
        if item_id:
            await modifier_quantite_ebay(item_id, nouvelle_qte)

        # Calcul marge
        prix_achat_moy = sum(r["prix_achat"] for r in refs_vendues) / len(refs_vendues) if refs_vendues else 0
        frais = round(prix_vente * FRAIS_EBAY_PCT, 2)
        marge = round(prix_vente - prix_achat_moy - frais, 2)
        marge_pct = round(marge / prix_achat_moy * 100) if prix_achat_moy > 0 else 0

        return {
            "ok": True,
            "refs_vendues": [r["ref"] for r in refs_vendues],
            "quantite": quantite_vendue,
            "restant": nouvelle_qte,
            "prix_vente": prix_vente,
            "prix_achat_moy": prix_achat_moy,
            "frais": frais,
            "marge": marge,
            "marge_pct": marge_pct,
        }
    except Exception as e:
        logger.error(f"traiter_vente_ebay error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}
