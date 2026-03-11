"""
MODULE /vendre — Flux complet en une commande
Photo(s) + infos → Analyse → Annonce → Validation → Publication → Archivage
"""
import anthropic
import httpx
import httpx
import base64
import re
import asyncio
import json
from datetime import datetime
from config.settings import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS
)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

# ─── PROMPT ANALYSE COMPLÈTE ─────────────────────────────
PROMPT_VENDRE = """Tu es un expert en vente d'objets d'occasion sur les plateformes françaises.

OBJET : {objet}
INFORMATIONS FOURNIES : {caption}

MISSION :
1. Fais 2 recherches web :
   - "{objet_court} prix eBay" → annonces actives et vendues
   - "{objet_court} vendu sold" → ventes réellement conclues

2. Reponds avec ce format exact, sans markdown :

TITRE_EBAY: [titre optimise SEO max 80 caracteres avec mots-cles principaux]
TITRE_LBC: [titre naturel max 70 caracteres style leboncoin]
TITRE_VINTED: [titre simple max 60 caracteres style vinted]
PRIX_EBAY: [chiffre entier]
PRIX_LBC: [chiffre entier]
PRIX_VINTED: [chiffre entier]
PRIX_FACEBOOK: [chiffre entier]
PRIX_MARCHE_BAS: [chiffre]
PRIX_MARCHE_MOYEN: [chiffre]
PRIX_MARCHE_HAUT: [chiffre]
CATEGORIE: [categorie principale]
ETAT: [Neuf / Tres bon etat / Bon etat / Etat correct / Pour pieces]
DIMENSIONS: [{dimensions}]
MATIERE: [matiere principale]
EPOQUE: [epoque ou style]
MOTS_CLES: [15 mots-cles separes par virgules]
DESCRIPTION:
[description complete 200-400 mots, naturelle, SEO, avec etat, caracteristiques, points forts]
FIN_DESCRIPTION
CONSEIL: [1 conseil pratique pour maximiser la vente]"""


async def analyser_et_generer(photos: list, caption: str, objet_identifie: str) -> dict:
    """
    Analyse complète + génération annonce.
    Retourne un dict avec toutes les infos.
    """
    # Version courte pour recherche
    mots = objet_identifie.split()
    objet_court = " ".join(mots[:4])

    # Extraire dimensions depuis caption
    dims = _extraire_dims(caption)

    # Télécharger première photo
    image_content = []
    if photos:
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                resp = await http.get(photos[0])
                img_data = base64.standard_b64encode(resp.content).decode("utf-8")
                media_type = "image/png" if "png" in resp.headers.get("content-type", "") else "image/jpeg"
                image_content = [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_data}}]
        except:
            pass

    await asyncio.sleep(2)

    # Appel Claude avec recherche web
    messages_content = image_content + [{
        "type": "text",
        "text": PROMPT_VENDRE.format(
            objet=objet_identifie,
            objet_court=objet_court,
            caption=caption or "aucune",
            dimensions=dims or "a estimer"
        )
    }]

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": messages_content}]
    )

    raw = ""
    for block in response.content:
        if hasattr(block, "text") and block.text:
            raw = block.text

    # Nettoyer markdown
    raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
    raw = re.sub(r'\*(.+?)\*', r'\1', raw)

    return _parser_reponse(raw, objet_identifie, dims)


def _parser_reponse(raw: str, objet: str, dims: str) -> dict:
    """Parse la réponse Claude en dict structuré."""
    def get(key):
        m = re.search(rf'^{key}\s*:\s*(.+)', raw, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ""

    def num(key):
        val = get(key)
        nums = re.findall(r'\d+', val)
        return int(nums[0]) if nums else 0

    # Extraire description entre DESCRIPTION: et FIN_DESCRIPTION
    desc_match = re.search(r'DESCRIPTION:\s*\n(.*?)FIN_DESCRIPTION', raw, re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else ""

    # Prix marché
    prix_bas   = num("PRIX_MARCHE_BAS")
    prix_moyen = num("PRIX_MARCHE_MOYEN")
    prix_haut  = num("PRIX_MARCHE_HAUT")
    prix_ebay  = num("PRIX_EBAY")

    # Calcul achat max selon paliers
    achat_max, label = _calcul_achat_max(prix_ebay)

    return {
        "objet": objet,
        "titre_ebay": get("TITRE_EBAY") or objet,
        "titre_lbc": get("TITRE_LBC") or objet,
        "titre_vinted": get("TITRE_VINTED") or objet,
        "prix_ebay": prix_ebay,
        "prix_lbc": num("PRIX_LBC"),
        "prix_vinted": num("PRIX_VINTED"),
        "prix_facebook": num("PRIX_FACEBOOK"),
        "prix_bas": prix_bas,
        "prix_moyen": prix_moyen,
        "prix_haut": prix_haut,
        "achat_max": achat_max,
        "label_regle": label,
        "categorie": get("CATEGORIE"),
        "etat": get("ETAT"),
        "dimensions": dims or get("DIMENSIONS"),
        "matiere": get("MATIERE"),
        "epoque": get("EPOQUE"),
        "mots_cles": get("MOTS_CLES"),
        "description": description,
        "conseil": get("CONSEIL"),
    }


def _calcul_achat_max(prix_revente: int):
    PALIERS = [
        (0,    10,   3.0, "x3 minimum"),
        (10,   50,   2.5, "x2.5 minimum"),
        (50,   100,  2.0, "x2 minimum"),
        (100,  300,  1.8, "x1.8 minimum"),
        (300,  1000, 1.6, "x1.6 minimum"),
        (1000, 9999, 1.4, "x1.4 minimum"),
    ]
    for (amin, amax, mult, label) in PALIERS:
        achat = int(prix_revente / mult)
        if amin <= achat < amax:
            return achat, label
    return int(prix_revente / 1.4), "x1.4 minimum"


def _extraire_dims(caption: str) -> str:
    if not caption:
        return ""
    m = re.search(
        r'(\d+[\.,]?\d*)\s*(?:cm)?\s*[x\*×]\s*(\d+[\.,]?\d*)\s*(?:cm)?\s*[x\*×]\s*(\d+[\.,]?\d*)\s*(cm)?',
        caption, re.IGNORECASE
    )
    if m:
        return f"{m.group(1)} x {m.group(2)} x {m.group(3)} cm"
    return ""


def formater_fiche(data: dict, ref_gestion: str) -> str:
    """Formate la fiche complète pour affichage Telegram."""
    marge = data["prix_ebay"] - data["achat_max"]
    marge_pct = round(marge / data["achat_max"] * 100) if data["achat_max"] > 0 else 0

    return (
        f"📋 FICHE COMPLETE — {ref_gestion}\n"
        f"{'='*35}\n\n"

        f"🔎 OBJET\n{data['objet']}\n\n"

        f"🏷️ TITRES D'ANNONCE\n"
        f"eBay    : {data['titre_ebay']}\n"
        f"LBC     : {data['titre_lbc']}\n"
        f"Vinted  : {data['titre_vinted']}\n\n"

        f"💰 PRIX DE VENTE CONSEILLES\n"
        f"• eBay          : {data['prix_ebay']} euros\n"
        f"• Leboncoin     : {data['prix_lbc']} euros\n"
        f"• Vinted        : {data['prix_vinted']} euros\n"
        f"• Facebook      : {data['prix_facebook']} euros\n\n"

        f"📈 PRIX DU MARCHE\n"
        f"• Bas    : {data['prix_bas']} euros\n"
        f"• Moyen  : {data['prix_moyen']} euros\n"
        f"• Haut   : {data['prix_haut']} euros\n\n"

        f"📦 CARACTERISTIQUES\n"
        f"• Categorie  : {data['categorie']}\n"
        f"• Etat       : {data['etat']}\n"
        f"• Dimensions : {data['dimensions'] or 'Non specifiees'}\n"
        f"• Matiere    : {data['matiere']}\n"
        f"• Epoque     : {data['epoque']}\n\n"

        f"📄 DESCRIPTION\n"
        f"{data['description'][:500]}...\n\n"

        f"🔑 MOTS-CLES\n{data['mots_cles']}\n\n"

        f"✅ RENTABILITE ({data['label_regle']})\n"
        f"• Achat max      : {data['achat_max']} euros\n"
        f"• Marge estimee  : +{marge} euros (+{marge_pct}%)\n\n"

        f"💡 CONSEIL\n{data['conseil']}\n\n"

        f"{'='*35}\n"
        f"Que souhaitez-vous faire ?"
    )


async def generer_ref_gestion() -> str:
    """Génère la prochaine référence de gestion RG-AAAA-NNN."""
    annee = datetime.now().strftime("%Y")
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={
                    "fields[]": ["Référence gestion"],
                    "filterByFormula": f"FIND('{annee}', {{Référence gestion}})",
                    "maxRecords": 1000
                }
            )
        records = resp.json().get("records", [])
        nums = []
        for r in records:
            ref = r.get("fields", {}).get("Référence gestion", "")
            m = re.search(r'RG-\d{4}-(\d+)', ref)
            if m:
                nums.append(int(m.group(1)))
        next_num = max(nums) + 1 if nums else 1
        return f"RG-{annee}-{str(next_num).zfill(3)}"
    except:
        return f"RG-{annee}-001"


async def archiver_airtable(data: dict, ref_gestion: str, photos: list, caption: str, prix_achat: float = 0, source: str = "") -> str:
    """Crée ou met à jour la fiche Airtable complète."""
    try:
        fields = {
            "Référence gestion": ref_gestion,
            "Référence": ref_gestion,
            "Description": caption or data["objet"],
            "Prix achat": prix_achat,
            "Prix vente": data["prix_ebay"],
            "Source": source or "Non renseigné",
            "Statut": "en ligne",
            "Photo URLs": json.dumps(photos),
            "Nombre de photos": len(photos),
            "Annonce générée": (
                f"TITRE EBAY: {data['titre_ebay']}\n"
                f"TITRE LBC: {data['titre_lbc']}\n"
                f"TITRE VINTED: {data['titre_vinted']}\n\n"
                f"{data['description']}\n\n"
                f"MOTS-CLES: {data['mots_cles']}"
            ),
            "Date achat": datetime.now().strftime("%Y-%m-%d"),
            "Notes": data["conseil"],
        }

        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                json={"fields": fields}
            )

        if resp.status_code in (200, 201):
            return resp.json().get("id", "")
        return ""
    except Exception as e:
        return ""
