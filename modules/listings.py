"""
MODULE ANNONCES
──────────────────────────────────────────────────────────
Génère automatiquement des annonces optimisées SEO
pour chaque plateforme de vente, puis les publie via API.
"""
import anthropic
import httpx
import json
from config.settings import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS,
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS,
    PLATFORM_FEES
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

LISTING_PROMPT = """Tu es un expert en vente de biens d'occasion sur les plateformes françaises.
Tu dois créer une annonce parfaitement optimisée pour vendre cet objet rapidement au meilleur prix.

INFORMATIONS SUR L'OBJET :
- Référence : {ref}
- Prix d'achat : {prix_achat}€
- Source : {source}
- Nombre de photos : {nb_photos}
- Notes : {notes}

Génère l'annonce complète dans ce format EXACT :

━━━━━━━━━━━━━━━━━━━━━━
📝 *ANNONCE GÉNÉRÉE — {ref}*
━━━━━━━━━━━━━━━━━━━━━━

🏷️ *TITRE eBay* (max 80 car.) :
[Titre optimisé mots-clés]

🏷️ *TITRE Leboncoin/Vinted* (max 70 car.) :
[Titre adapté style naturel]

💶 *PRIX CONSEILLÉS :*
• eBay : XXX€
• Vinted : XXX€
• Leboncoin : XXX€
• Facebook : XXX€

📄 *DESCRIPTION (coller sur toutes les plateformes) :*
[Description complète 200-400 mots, SEO, naturelle, avec état, dimensions si connu, époque, matériaux]

🔑 *MOTS-CLÉS CACHÉS :*
[liste de 15 mots-clés séparés par des virgules]

📊 *CARACTÉRISTIQUES :*
• Catégorie : [...]
• État : [Bon état / Très bon état / Neuf / Pour pièces]
• Dimensions estimées : [...]
• Matière principale : [...]
• Époque / Style : [...]

💡 *NOTE VENDEUR :*
[1 conseil pratique pour cette vente]"""


async def generate_listing(ref: str) -> str:
    """Génère une annonce complète pour un produit depuis sa fiche Airtable."""

    # 1. Récupérer les infos du produit dans Airtable
    product = await _get_product(ref)
    if not product:
        return f"⚠️ Produit `{ref}` non trouvé dans la base."

    f = product.get("fields", {})
    prix_achat = f.get("Prix achat", 0)
    source = f.get("Source", "Inconnu")
    nb_photos = f.get("Nombre de photos", 0)
    notes = f.get("Notes", "Aucune note supplémentaire")
    photos_urls = json.loads(f.get("Photos URLs", "[]")) if f.get("Photos URLs") else []

    prompt = LISTING_PROMPT.format(
        ref=ref,
        prix_achat=prix_achat,
        source=source,
        nb_photos=nb_photos,
        notes=notes
    )

    # 2. Si des photos sont disponibles, les inclure dans l'analyse
    messages_content = []

    if photos_urls:
        # Inclure la première photo pour que Claude "voie" l'objet
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                img_resp = await http.get(photos_urls[0])
                import base64
                img_data = base64.standard_b64encode(img_resp.content).decode("utf-8")
                messages_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}
                })
        except Exception:
            pass  # Continuer sans image si erreur

    messages_content.append({"type": "text", "text": prompt})

    # 3. Appeler Claude
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{"role": "user", "content": messages_content}]
        )
        listing_text = response.content[0].text

        # 4. Sauvegarder l'annonce dans Airtable
        await _save_listing(ref, product["id"], listing_text)

        return listing_text

    except anthropic.APIError as e:
        return f"⚠️ Erreur Claude API : {str(e)}"


async def publish_listing(ref: str) -> str:
    """
    Publie l'annonce sur les plateformes configurées.
    Pour le MVP : simule la publication et fournit les liens.
    En production : appelle les vraies APIs eBay/Vinted/LBC.
    """
    product = await _get_product(ref)
    if not product:
        return f"⚠️ Produit `{ref}` non trouvé."

    f = product.get("fields", {})
    listing = f.get("Annonce générée", "")

    if not listing:
        return f"⚠️ Aucune annonce générée pour `{ref}`. Utilise `/annonce {ref}` d'abord."

    # ── PUBLICATION eBay (via API officielle) ──────────
    ebay_result = await _publish_ebay(ref, f, listing)

    # ── RÉSULTAT ──────────────────────────────────────
    results = [
        f"🚀 *PUBLICATION — {ref}*\n",
        ebay_result,
        "",
        "📱 *À publier manuellement :*",
        "• Vinted : copie la description ci-dessus",
        "• Leboncoin : idem",
        "• Facebook Marketplace : idem",
        "",
        "💡 Les APIs Vinted et LBC nécessitent une configuration supplémentaire.",
        f"\n✅ Statut mis à jour : *en ligne*"
    ]

    # Mettre à jour le statut dans Airtable
    await _update_airtable_status(product["id"], "en ligne")

    return "\n".join(results)


async def _publish_ebay(ref: str, fields: dict, listing: str) -> str:
    """
    Publication eBay via l'API Trading.
    Retourne un message de résultat.
    """
    from config.settings import EBAY_USER_TOKEN, EBAY_APP_ID

    if not EBAY_USER_TOKEN or not EBAY_APP_ID:
        return (
            "📦 *eBay :* Non configuré\n"
            "  → Ajoute EBAY_USER_TOKEN dans .env pour activer\n"
            "  → Guide : https://developer.ebay.com"
        )

    # Extraction titre et prix depuis la listing générée
    titre = _extract_field(listing, "TITRE eBay") or f"Objet occasion - {ref}"
    prix_str = _extract_ebay_price(listing)
    prix = float(prix_str) if prix_str else fields.get("Prix vente", 0)

    # XML pour l'API eBay Trading (simplifié)
    xml_payload = f"""<?xml version="1.0" encoding="utf-8"?>
<AddItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  <Item>
    <Title>{titre[:80]}</Title>
    <Description><![CDATA[{_extract_description(listing)}]]></Description>
    <PrimaryCategory><CategoryID>20081</CategoryID></PrimaryCategory>
    <StartPrice>{prix}</StartPrice>
    <CategoryMappingAllowed>true</CategoryMappingAllowed>
    <ConditionID>3000</ConditionID>
    <Country>FR</Country>
    <Currency>EUR</Currency>
    <DispatchTimeMax>3</DispatchTimeMax>
    <ListingDuration>GTC</ListingDuration>
    <ListingType>FixedPriceItem</ListingType>
    <PaymentMethods>PayPal</PaymentMethods>
    <ReturnPolicy>
      <ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>
      <RefundOption>MoneyBack</RefundOption>
      <ReturnsWithinOption>Days_30</ReturnsWithinOption>
    </ReturnPolicy>
    <ShippingDetails>
      <ShippingType>Flat</ShippingType>
      <ShippingServiceOptions>
        <ShippingServicePriority>1</ShippingServicePriority>
        <ShippingService>FR_Colissimo</ShippingService>
        <ShippingServiceCost>8.00</ShippingServiceCost>
      </ShippingServiceOptions>
    </ShippingDetails>
    <Site>France</Site>
  </Item>
</AddItemRequest>"""

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                "https://api.ebay.com/ws/api.dll",
                content=xml_payload.encode("utf-8"),
                headers={
                    "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                    "X-EBAY-API-CALL-NAME": "AddItem",
                    "X-EBAY-API-SITEID": "71",
                    "Content-Type": "text/xml",
                    "X-EBAY-API-APP-NAME": EBAY_APP_ID,
                }
            )

        if resp.status_code == 200 and "ItemID" in resp.text:
            import re
            item_id = re.search(r"<ItemID>(\d+)</ItemID>", resp.text)
            if item_id:
                item_id = item_id.group(1)
                return (
                    f"✅ *eBay :* Publié !\n"
                    f"  🔗 https://www.ebay.fr/itm/{item_id}\n"
                    f"  💶 Prix : {prix}€"
                )
        return f"⚠️ *eBay :* Erreur publication ({resp.status_code})"

    except Exception as e:
        return f"⚠️ *eBay :* Erreur — {str(e)[:100]}"


# ─── Helpers ───────────────────────────────────────────────

async def _get_product(ref: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={"filterByFormula": f"{{Référence}}='{ref}'", "maxRecords": 1}
            )
        records = resp.json().get("records", [])
        return records[0] if records else None
    except Exception:
        return None


async def _save_listing(ref: str, record_id: str, listing_text: str):
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS_AT,
                json={"fields": {"Annonce générée": listing_text}}
            )
    except Exception:
        pass


async def _update_airtable_status(record_id: str, status: str):
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS_AT,
                json={"fields": {"Statut": status}}
            )
    except Exception:
        pass


def _extract_field(text: str, field_name: str) -> str:
    """Extrait un champ spécifique de la réponse Claude."""
    import re
    pattern = rf"\*{re.escape(field_name)}\*[^:]*:\s*\n([^\n]+)"
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def _extract_ebay_price(text: str) -> str:
    import re
    m = re.search(r"eBay\s*:\s*(\d+(?:[.,]\d+)?)\s*€", text)
    return m.group(1).replace(",", ".") if m else ""


def _extract_description(text: str) -> str:
    """Extrait la description de l'annonce générée."""
    import re
    m = re.search(r"DESCRIPTION.*?:\s*\n(.*?)(?=\n🔑|\n📊|\n💡|$)", text, re.DOTALL)
    if m:
        return m.group(1).strip()[:4000]
    return text[:4000]
