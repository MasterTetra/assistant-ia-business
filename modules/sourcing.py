"""
MODULE SOURCING — version finale optimisée
- Recherche web sur eBay, Leboncoin, Catawiki
- Calcul de marge correct
- Format parfait garanti
"""
import anthropic
import httpx
import base64
import re
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

STEP1_PROMPT = """Regarde cette photo et identifie l'objet en UNE seule ligne.
Format : [Marque/Artiste] [type objet] [matiere] [epoque] [etat]
Exemple : Vase Daum verre grave Art Nouveau signe bon etat
Informations supplementaires : {caption}
Reponds avec UNE seule ligne, rien d'autre."""

STEP2_PROMPT = """Tu es un expert en achat-revente d'objets d'occasion.
Objet a analyser : {objet}

INSTRUCTIONS :
1. Fais 2 recherches web pour trouver les prix actuels de cet objet
2. Cherche sur ebay.com, leboncoin.fr et catawiki.com en priorite
3. Note tous les prix trouves en euros

Reponds UNIQUEMENT avec ce bloc structure, sans texte avant ni apres :

---DEBUT---
OBJET: {objet}
ANNONCES:
[liste chaque annonce : site | prix en euros | etat]
PRIX_BAS: [nombre entier en euros uniquement, ex: 150]
PRIX_MOYEN: [nombre entier en euros uniquement, ex: 300]
PRIX_HAUT: [nombre entier en euros uniquement, ex: 500]
PRIX_REVENTE: [nombre entier en euros uniquement, ex: 350]
PRIX_ACHAT_MAX: [nombre entier en euros uniquement, ex: 180]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
RAISON: [une phrase courte]
PLATEFORMES: [liste des meilleures plateformes pour vendre]
CONSEIL: [une seule phrase de conseil pratique]
---FIN---"""


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    try:
        # Telecharger la photo
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")
        media_type = "image/png" if "png" in resp.headers.get("content-type", "") else "image/jpeg"

        # ETAPE 1 : Identification rapide de l'objet
        r1 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": STEP1_PROMPT.format(caption=caption or "aucune information")}
                ]
            }]
        )
        objet = r1.content[0].text.strip().split("\n")[0].strip()

        # ETAPE 2 : Recherche web + prix
        r2 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": STEP2_PROMPT.format(objet=objet)
            }]
        )

        # Extraire le dernier texte de la reponse
        raw = ""
        for block in r2.content:
            if hasattr(block, "text") and block.text:
                raw = block.text

        # Nettoyer tout markdown
        raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
        raw = re.sub(r'\*(.+?)\*', r'\1', raw)
        raw = re.sub(r'_(.+?)_', r'\1', raw)
        raw = re.sub(r'#{1,6}\s+', '', raw)

        # Extraire le bloc structure
        match = re.search(r'---DEBUT---(.*?)---FIN---', raw, re.DOTALL)
        data = match.group(1).strip() if match else raw

        return _build_message(data, objet)

    except anthropic.APIError as e:
        return f"Erreur API Claude : {str(e)}"
    except Exception as e:
        return f"Erreur : {str(e)}"


def _extract(text: str, key: str) -> str:
    """Extrait la valeur d'une cle."""
    m = re.search(rf'{key}\s*:\s*(.+)', text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_number(text: str, key: str) -> float:
    """Extrait un nombre depuis une cle."""
    val = _extract(text, key)
    nums = re.findall(r'[\d]+', val.replace(" ", ""))
    return float(nums[0]) if nums else 0.0


def _extract_annonces(text: str) -> str:
    """Extrait le bloc annonces."""
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=PRIX_BAS|\Z)', text, re.DOTALL | re.IGNORECASE)
    if m:
        lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
        return "\n".join(f"  • {l}" for l in lines[:5])
    return "  • Voir plateformes recommandees"


def _build_message(data: str, objet: str) -> str:
    """Construit le message final avec calculs corrects."""

    # Extraction des champs
    objet_final = _extract(data, "OBJET") or objet
    annonces    = _extract_annonces(data)
    plateformes = _extract(data, "PLATEFORMES") or "eBay, Leboncoin, Catawiki"
    conseil     = _extract(data, "CONSEIL") or "Verifiez l'etat avant achat."
    demande     = _extract(data, "DEMANDE") or "MOYENNE"
    raison      = _extract(data, "RAISON") or ""

    # Prix numeriques
    prix_bas    = _extract_number(data, "PRIX_BAS")
    prix_moyen  = _extract_number(data, "PRIX_MOYEN")
    prix_haut   = _extract_number(data, "PRIX_HAUT")
    prix_revente = _extract_number(data, "PRIX_REVENTE")
    achat_max   = _extract_number(data, "PRIX_ACHAT_MAX")

    # Valeurs par defaut si extraction echoue
    if prix_bas == 0 and prix_moyen == 0:
        return f"Analyse incomplete pour : {objet_final}\nReessayez avec une photo plus claire ou des informations supplementaires."

    if prix_revente == 0:
        prix_revente = round(prix_moyen * 0.9)
    if achat_max == 0:
        achat_max = round(prix_revente * 0.5)

    # Calcul de marge correct
    # Marge = (Prix revente - Prix achat) / Prix achat * 100
    if achat_max > 0:
        marge_pct = round((prix_revente - achat_max) / achat_max * 100)
        marge_euros = round(prix_revente - achat_max)
    else:
        marge_pct = 0
        marge_euros = 0

    # Emoji demande
    demande_upper = demande.upper()
    if "FORTE" in demande_upper:
        demande_emoji = "🔥 FORTE"
    elif "FAIBLE" in demande_upper:
        demande_emoji = "🔵 FAIBLE"
    else:
        demande_emoji = "🟡 MOYENNE"

    msg = (
        f"🔎 OBJET IDENTIFIE\n"
        f"{objet_final}\n"
        f"\n"
        f"🌐 ANNONCES TROUVEES (eBay / LBC / Catawiki)\n"
        f"{annonces}\n"
        f"\n"
        f"💰 PRIX DU MARCHE\n"
        f"• Prix bas    : {int(prix_bas)} euros\n"
        f"• Prix moyen  : {int(prix_moyen)} euros\n"
        f"• Prix haut   : {int(prix_haut)} euros\n"
        f"\n"
        f"✅ RECOMMANDATION\n"
        f"• Prix de revente conseille : {int(prix_revente)} euros\n"
        f"• Prix achat maximum        : {int(achat_max)} euros\n"
        f"• Marge brute               : +{marge_euros} euros (+{marge_pct}%)\n"
        f"\n"
        f"📈 DEMANDE\n"
        f"{demande_emoji} — {raison}\n"
        f"\n"
        f"🏆 MEILLEURES PLATEFORMES\n"
        f"{plateformes}\n"
        f"\n"
        f"⚡ CONSEIL\n"
        f"{conseil}\n"
        f"\n"
        f"💡 Seuil decision : {int(achat_max)} euros maximum"
    )

    return msg
