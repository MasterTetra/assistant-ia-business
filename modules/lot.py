"""
MODULE LOT — Traitement en masse d'objets
/lot_debut → envoyer photos + légendes → /lot_analyser → valider un par un
Max 50 objets par lot. 1 appel Claude par objet.
"""
import anthropic
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
MAX_LOT = 50

PROMPT_LOT = """Tu es un expert en vente d'objets d'occasion sur les plateformes françaises.

OBJET : {objet}
INFORMATIONS : {caption}

Fais 1 recherche web : "{objet_court} prix eBay"

Reponds avec ce format exact, sans markdown :

TITRE_EBAY: [titre SEO max 80 caracteres]
TITRE_LBC: [titre max 70 caracteres]
TITRE_VINTED: [titre max 60 caracteres]
PRIX_EBAY: [chiffre entier]
PRIX_LBC: [chiffre entier]
PRIX_VINTED: [chiffre entier]
PRIX_BAS: [chiffre]
PRIX_MOYEN: [chiffre]
PRIX_HAUT: [chiffre]
ETAT: [Neuf / Tres bon etat / Bon etat / Etat correct / Pour pieces]
CATEGORIE: [categorie]
MATIERE: [matiere principale]
MOTS_CLES: [10 mots-cles separes par virgules]
DESCRIPTION:
[description 150-250 mots, naturelle, points forts, etat, caracteristiques]
FIN_DESCRIPTION
CONSEIL: [conseil court pour maximiser la vente]"""


async def _claude_call_with_retry(func, *args, **kwargs):
    for attempt in range(4):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e) and attempt < 3:
                await asyncio.sleep(20 * (attempt + 1))
            else:
                raise


async def analyser_objet(photo_url: str, caption: str, index: int) -> dict:
    """Analyse un seul objet. Retourne un dict avec toutes les infos."""
    objet = caption or f"objet {index}"
    mots = objet.split()
    objet_court = " ".join(mots[:4])

    # Télécharger photo
    image_content = []
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.get(photo_url)
            img_data = base64.standard_b64encode(resp.content).decode("utf-8")
            media_type = "image/png" if "png" in resp.headers.get("content-type", "") else "image/jpeg"
            image_content = [{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_data}}]
    except:
        pass

    # Appel Claude unique
    messages_content = image_content + [{
        "type": "text",
        "text": PROMPT_LOT.format(
            objet=objet,
            objet_court=objet_court,
            caption=caption or "aucune"
        )
    }]

    try:
        response = await _claude_call_with_retry(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=1200,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": messages_content}]
        )

        raw = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                raw = block.text

        raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
        raw = re.sub(r'\*(.+?)\*', r'\1', raw)

        return _parser(raw, objet, photo_url, caption, index)

    except Exception as e:
        return {
            "index": index,
            "objet": objet,
            "photo_url": photo_url,
            "caption": caption,
            "erreur": str(e)[:100],
            "titre_ebay": objet,
            "prix_ebay": 0,
            "prix_lbc": 0,
            "prix_vinted": 0,
            "prix_bas": 0,
            "prix_moyen": 0,
            "prix_haut": 0,
            "etat": "Bon etat",
            "categorie": "",
            "matiere": "",
            "mots_cles": "",
            "description": "",
            "conseil": "",
            "titre_lbc": objet,
            "titre_vinted": objet,
        }


def _parser(raw: str, objet: str, photo_url: str, caption: str, index: int) -> dict:
    def get(key):
        m = re.search(rf'^{key}\s*:\s*(.+)', raw, re.IGNORECASE | re.MULTILINE)
        return m.group(1).strip() if m else ""

    def num(key):
        val = get(key)
        nums = re.findall(r'\d+', val)
        return int(nums[0]) if nums else 0

    desc_match = re.search(r'DESCRIPTION:\s*\n(.*?)FIN_DESCRIPTION', raw, re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else ""

    prix_ebay = num("PRIX_EBAY")
    achat_max, label = _calcul_achat_max(prix_ebay)

    return {
        "index": index,
        "objet": objet,
        "photo_url": photo_url,
        "caption": caption,
        "erreur": None,
        "titre_ebay": get("TITRE_EBAY") or objet,
        "titre_lbc": get("TITRE_LBC") or objet,
        "titre_vinted": get("TITRE_VINTED") or objet,
        "prix_ebay": prix_ebay,
        "prix_lbc": num("PRIX_LBC"),
        "prix_vinted": num("PRIX_VINTED"),
        "prix_bas": num("PRIX_BAS"),
        "prix_moyen": num("PRIX_MOYEN"),
        "prix_haut": num("PRIX_HAUT"),
        "achat_max": achat_max,
        "label_regle": label,
        "etat": get("ETAT"),
        "categorie": get("CATEGORIE"),
        "matiere": get("MATIERE"),
        "mots_cles": get("MOTS_CLES"),
        "description": description,
        "conseil": get("CONSEIL"),
    }


def _calcul_achat_max(prix_revente: int):
    PALIERS = [
        (0,    10,   3.0, "x3 min"),
        (10,   50,   2.5, "x2.5 min"),
        (50,   100,  2.0, "x2 min"),
        (100,  300,  1.8, "x1.8 min"),
        (300,  1000, 1.6, "x1.6 min"),
        (1000, 9999, 1.4, "x1.4 min"),
    ]
    for (amin, amax, mult, label) in PALIERS:
        achat = int(prix_revente / mult)
        if amin <= achat < amax:
            return achat, label
    return int(prix_revente / 1.4), "x1.4 min"


def formater_fiche_lot(data: dict) -> str:
    """Formate une fiche courte pour affichage lot."""
    if data.get("erreur"):
        return (
            f"⚠️ OBJET {data['index']} — Erreur analyse\n"
            f"{data['objet']}\n"
            f"Erreur : {data['erreur']}"
        )

    marge = data["prix_ebay"] - data["achat_max"]
    marge_pct = round(marge / data["achat_max"] * 100) if data["achat_max"] > 0 else 0

    return (
        f"📦 OBJET {data['index']}/{data.get('total', '?')}\n"
        f"{'='*30}\n\n"
        f"🔎 {data['objet']}\n\n"
        f"🏷️ Titre eBay :\n{data['titre_ebay']}\n\n"
        f"💰 Prix conseillés\n"
        f"• eBay     : {data['prix_ebay']} euros\n"
        f"• LBC      : {data['prix_lbc']} euros\n"
        f"• Vinted   : {data['prix_vinted']} euros\n\n"
        f"📈 Marché\n"
        f"• Bas / Moyen / Haut : {data['prix_bas']} / {data['prix_moyen']} / {data['prix_haut']} euros\n\n"
        f"✅ Rentabilité ({data['label_regle']})\n"
        f"• Achat max     : {data['achat_max']} euros\n"
        f"• Marge estimée : +{marge} euros (+{marge_pct}%)\n\n"
        f"📄 {data['description'][:300]}...\n\n"
        f"💡 {data['conseil']}"
    )


async def generer_ref_gestion() -> str:
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


async def archiver_airtable_lot(data: dict, ref_gestion: str) -> bool:
    try:
        fields = {
            "Référence gestion": ref_gestion,
            "Référence": ref_gestion,
            "Description": data["caption"] or data["objet"],
            "Prix vente": data["prix_ebay"],
            "Statut": "en ligne",
            "Photos URLs": json.dumps([data["photo_url"]]),
            "Nombre de photos": 1,
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
        return resp.status_code in (200, 201)
    except:
        return False
