"""
MODULE SOURCING — 1 seul appel optimise
Coût : ~0.01$ par analyse
"""
import anthropic
import httpx
import base64
import re
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

PROMPT = """Tu es un expert en achat-revente d'objets d'occasion.
Infos supplementaires : {caption}

MISSION :
1. Identifie l'objet sur la photo
2. Fais 2 recherches web : une sur ebay.fr/leboncoin.fr, une sur catawiki.com/interencheres.com
3. Reponds UNIQUEMENT avec ce bloc, sans rien avant ni apres, sans markdown :

---DEBUT---
OBJET: [description precise]
ANNONCES:
[site | prix euros | VENDU ou EN VENTE ou ADJUGE | etat]
PRIX_BAS: [chiffre entier]
PRIX_MOYEN: [chiffre entier]
PRIX_HAUT: [chiffre entier]
PRIX_REVENTE: [chiffre entier conseille]
PRIX_ACHAT_MAX: [chiffre = PRIX_REVENTE divise par 2]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
RAISON: [une phrase]
PLATEFORMES: [liste]
CONSEIL: [une phrase]
---FIN---"""


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")
        media_type = "image/png" if "png" in resp.headers.get("content-type", "") else "image/jpeg"

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": PROMPT.format(caption=caption or "aucune")}
                ]
            }]
        )

        # Extraire le dernier texte
        raw = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                raw = block.text

        # Nettoyer markdown
        raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
        raw = re.sub(r'\*(.+?)\*', r'\1', raw)

        # Extraire le bloc
        match = re.search(r'---DEBUT---(.*?)---FIN---', raw, re.DOTALL)
        data = match.group(1).strip() if match else raw

        return _build_message(data)

    except anthropic.APIError as e:
        return f"Erreur API Claude : {str(e)}"
    except Exception as e:
        return f"Erreur : {str(e)}"


def _extract(text, key):
    m = re.search(rf'{key}\s*:\s*(.+)', text, re.IGNORECASE)
    return m.group(1).strip() if m else ""

def _num(text, key):
    val = _extract(text, key)
    nums = re.findall(r'\d+', val.replace(" ", ""))
    return float(nums[0]) if nums else 0.0

def _annonces(text):
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=PRIX_BAS|\Z)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return "  • Voir plateformes recommandees"
    lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
    result = []
    for line in lines[:5]:
        lu = line.upper()
        if "VENDU" in lu:
            result.append(f"  ✅ {line}")
        elif "ADJUGE" in lu:
            result.append(f"  🔨 {line}")
        else:
            result.append(f"  🔵 {line}")
    return "\n".join(result) if result else "  • Voir plateformes recommandees"

def _build_message(data):
    objet      = _extract(data, "OBJET") or "Objet non identifie"
    annonces   = _annonces(data)
    plateformes = _extract(data, "PLATEFORMES") or "eBay, Leboncoin, Catawiki"
    conseil    = _extract(data, "CONSEIL") or "Verifiez l'etat avant achat."
    demande    = _extract(data, "DEMANDE") or "MOYENNE"
    raison     = _extract(data, "RAISON") or ""

    prix_bas     = _num(data, "PRIX_BAS")
    prix_moyen   = _num(data, "PRIX_MOYEN")
    prix_haut    = _num(data, "PRIX_HAUT")
    prix_revente = _num(data, "PRIX_REVENTE")
    achat_max    = _num(data, "PRIX_ACHAT_MAX")

    if prix_bas == 0 and prix_moyen == 0:
        return f"Aucune annonce trouvee pour : {objet}\nEssayez d'ajouter des details en legende (marque, materiaux, epoque)."

    if prix_revente == 0:
        prix_revente = round(prix_moyen * 0.85)

    # Forcer achat_max = 50% prix_revente si marge < 30%
    if achat_max == 0 or (prix_revente - achat_max) / max(achat_max, 1) * 100 < 30:
        achat_max = round(prix_revente * 0.5)

    marge_euros = round(prix_revente - achat_max)
    marge_pct   = round(marge_euros / achat_max * 100) if achat_max > 0 else 0

    demande_up = demande.upper()
    if "FORTE" in demande_up:   emoji = "🔥 FORTE"
    elif "FAIBLE" in demande_up: emoji = "🔵 FAIBLE"
    else:                        emoji = "🟡 MOYENNE"

    return (
        f"🔎 OBJET IDENTIFIE\n{objet}\n\n"
        f"🌐 ANNONCES TROUVEES\n"
        f"✅ Vendu  🔨 Adjuge  🔵 En vente\n"
        f"{annonces}\n\n"
        f"💰 PRIX DU MARCHE\n"
        f"• Prix bas    : {int(prix_bas)} euros\n"
        f"• Prix moyen  : {int(prix_moyen)} euros\n"
        f"• Prix haut   : {int(prix_haut)} euros\n\n"
        f"✅ RECOMMANDATION\n"
        f"• Prix de revente conseille : {int(prix_revente)} euros\n"
        f"• Prix achat maximum        : {int(achat_max)} euros\n"
        f"• Marge brute               : +{marge_euros} euros (+{marge_pct}%)\n\n"
        f"📈 DEMANDE\n{emoji} — {raison}\n\n"
        f"🏆 MEILLEURES PLATEFORMES\n{plateformes}\n\n"
        f"⚡ CONSEIL\n{conseil}\n\n"
        f"💡 Seuil decision : {int(achat_max)} euros maximum"
    )
