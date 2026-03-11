"""
MODULE SOURCING — Analyse technique + marché complète
Règles de rentabilité par palier + analyse encombrement/liquidité
"""
import anthropic
import httpx
import base64
import re
import asyncio
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─── RÈGLES DE RENTABILITÉ PAR PALIER ────────────────────
PALIERS = [
    # (achat_min, achat_max, multiplicateur, marge_min_pct, label)
    (0,    10,   3.0, 200, "x3 minimum"),
    (10,   50,   2.5, 150, "x2.5 minimum"),
    (50,   100,  2.0, 100, "x2 minimum"),
    (100,  300,  1.8,  80, "x1.8 minimum"),
    (300,  1000, 1.6,  60, "x1.6 minimum"),
    (1000, 9999, 1.4,  40, "x1.4 minimum"),
]

def get_regle(achat: float):
    for (amin, amax, mult, marge_min, label) in PALIERS:
        if amin <= achat < amax:
            return mult, marge_min, label
    return 1.4, 40, "x1.4 minimum"

# ─── PROMPT IDENTIFICATION ────────────────────────────────
PROMPT_ID = """Analyse cet objet en expert en achat-revente.
Info supplementaire : {caption}

Reponds en 1 seule ligne :
[Marque/Artiste] [type] [matiere] [epoque/style] [etat]"""

# ─── PROMPT ANALYSE COMPLÈTE ─────────────────────────────
PROMPT_ANALYSE = """Tu es un expert en achat-revente d'objets d'occasion avec 20 ans d'experience.
Objet : {objet}

MISSION EN 2 ETAPES :

ETAPE 1 - Fais 2 recherches web :
- Recherche 1 : "{objet} prix vente ebay.fr leboncoin.fr"
- Recherche 2 : "{objet} vendu adjuge catawiki interencheres"

ETAPE 2 - Reponds avec ce format exact, sans markdown, chiffres entiers :

OBJET: [nom precis]
ANNONCES:
[site | prix euros | VENDU ou EN VENTE ou ADJUGE | etat]
BAS: [chiffre]
MOYEN: [chiffre]
HAUT: [chiffre]
REVENTE: [chiffre conseille base sur ventes reelles]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
VITESSE: [RAPIDE ou NORMALE ou LENTE]
RAISON: [phrase sur la demande et tendance marche]
POIDS: [estimation en grammes ou kg]
DIMENSIONS: [estimation L x l x H en cm]
ENCOMBREMENT: [PETIT moins de 30cm ou MOYEN 30-60cm ou GRAND plus de 60cm]
FACILITE_ENVOI: [FACILE ou MOYEN ou DIFFICILE]
PLATEFORMES: [liste ordonnee par pertinence]
CONSEIL: [conseil base sur les vraies donnees]"""


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    try:
        # Télécharger photo
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")
        media_type = "image/png" if "png" in resp.headers.get("content-type", "") else "image/jpeg"

        # Appel 1 : identification (photo, léger)
        r1 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=80,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": PROMPT_ID.format(caption=caption or "aucune")}
                ]
            }]
        )
        objet = r1.content[0].text.strip().split("\n")[0]
        objet = re.sub(r'[«»""]', '', objet).strip()

        await asyncio.sleep(2)

        # Appel 2 : recherche web + analyse complète (texte seul)
        r2 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": PROMPT_ANALYSE.format(objet=objet)
            }]
        )

        raw = ""
        for block in r2.content:
            if hasattr(block, "text") and block.text:
                raw = block.text

        raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
        raw = re.sub(r'\*(.+?)\*', r'\1', raw)
        raw = re.sub(r'#{1,6}\s+', '', raw)

        return _build(raw, objet)

    except anthropic.APIError as e:
        return f"Erreur API Claude : {str(e)}"
    except Exception as e:
        return f"Erreur : {str(e)}"


def _get(text, key):
    m = re.search(rf'^{key}\s*:\s*(.+)', text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""

def _num(text, key):
    val = _get(text, key)
    nums = re.findall(r'\d+', val.replace(" ", ""))
    return float(nums[0]) if nums else 0.0

def _annonces(text):
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=\nBAS:|\nMOYEN:|\Z)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return "  • Voir plateformes recommandees"
    lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
    result = []
    for line in lines[:5]:
        lu = line.upper()
        if any(w in lu for w in ["VENDU", "SOLD", "TERMINE"]):
            result.append(f"  ✅ {line}")
        elif any(w in lu for w in ["ADJUGE", "ENCHERE"]):
            result.append(f"  🔨 {line}")
        else:
            result.append(f"  🔵 {line}")
    return "\n".join(result) if result else "  • Voir plateformes recommandees"


def _build(data, objet_fallback):
    objet       = _get(data, "OBJET") or objet_fallback
    annonces    = _annonces(data)
    plateformes = _get(data, "PLATEFORMES") or "eBay, Leboncoin, Catawiki"
    conseil     = _get(data, "CONSEIL") or "Verifiez l'etat avant achat."
    demande     = _get(data, "DEMANDE") or "MOYENNE"
    vitesse     = _get(data, "VITESSE") or "NORMALE"
    raison      = _get(data, "RAISON") or ""
    poids       = _get(data, "POIDS") or "Non estime"
    dimensions  = _get(data, "DIMENSIONS") or "Non estimees"
    encombrement = _get(data, "ENCOMBREMENT") or "MOYEN"
    facilite    = _get(data, "FACILITE_ENVOI") or "MOYEN"

    prix_bas     = _num(data, "BAS")
    prix_moyen   = _num(data, "MOYEN")
    prix_haut    = _num(data, "HAUT")
    prix_revente = _num(data, "REVENTE")

    # Fallback : extraire les montants du texte libre
    if prix_bas == 0 and prix_moyen == 0:
        montants = []
        for val in re.findall(r'(\d[\d\s]*)\s*(?:€|euros?)', data, re.IGNORECASE):
            try:
                n = float(val.replace(" ", ""))
                if 1 < n < 100000:
                    montants.append(n)
            except:
                pass
        if montants:
            montants = sorted(set(montants))
            prix_bas     = int(montants[0])
            prix_haut    = int(montants[-1])
            prix_moyen   = int(sum(montants) / len(montants))
            prix_revente = int(prix_moyen * 0.85)
        else:
            return (
                f"🔎 {objet}\n\n"
                f"⚠️ Aucun prix trouve sur eBay, LBC et Catawiki.\n"
                f"Objet rare ou peu reference en ligne.\n\n"
                f"💡 Cherchez manuellement sur Catawiki et 1stDibs."
            )

    if prix_revente == 0:
        prix_revente = int(prix_moyen * 0.85)

    # ── Calcul achat max selon paliers ───────────────────
    # On estime l'achat max selon le prix de revente et les paliers
    # On teste chaque palier pour trouver le bon multiplicateur
    achat_max = 0
    multiplicateur_applique = 1.4
    label_regle = "x1.4 minimum"

    for (amin, amax, mult, marge_min, label) in PALIERS:
        achat_estime = prix_revente / mult
        if amin <= achat_estime < amax:
            achat_max = int(achat_estime)
            multiplicateur_applique = mult
            label_regle = label
            break

    if achat_max == 0:
        achat_max = int(prix_revente / 1.4)
        multiplicateur_applique = 1.4
        label_regle = "x1.4 minimum"

    marge_euros = int(prix_revente - achat_max)
    marge_pct   = round(marge_euros / achat_max * 100) if achat_max > 0 else 0

    # ── Analyse flexibilité (objet proche du seuil) ──────
    # Si on est proche du seuil, on autorise une légère souplesse
    # selon l'encombrement et la vitesse de vente
    flexibilite = ""
    achat_max_souple = achat_max
    seuil_proche = prix_revente / (multiplicateur_applique * 0.9)  # 10% de souplesse

    encombrement_up = encombrement.upper()
    vitesse_up = vitesse.upper()

    if "RAPIDE" in vitesse_up and "PETIT" in encombrement_up:
        # Petit objet qui se vend vite → souplesse +15%
        achat_max_souple = int(achat_max * 1.15)
        flexibilite = f"✅ Souplesse +15% accordee (petit objet, vente rapide) → jusqu'a {achat_max_souple} euros"
    elif "RAPIDE" in vitesse_up and "MOYEN" in encombrement_up:
        # Taille moyenne mais vente rapide → souplesse +10%
        achat_max_souple = int(achat_max * 1.10)
        flexibilite = f"✅ Souplesse +10% accordee (vente rapide) → jusqu'a {achat_max_souple} euros"
    elif "LENTE" in vitesse_up or "GRAND" in encombrement_up:
        # Grand objet ou vente lente → pas de souplesse, seuil strict
        flexibilite = f"⚠️ Aucune souplesse (objet encombrant ou vente lente) → seuil strict"

    # ── Emojis ───────────────────────────────────────────
    demande_up = demande.upper()
    if "FORTE" in demande_up:    emoji_demande = "🔥 FORTE"
    elif "FAIBLE" in demande_up: emoji_demande = "🔵 FAIBLE"
    else:                        emoji_demande = "🟡 MOYENNE"

    if "RAPIDE" in vitesse_up:   emoji_vitesse = "⚡ RAPIDE"
    elif "LENTE" in vitesse_up:  emoji_vitesse = "🐢 LENTE"
    else:                        emoji_vitesse = "🕐 NORMALE"

    encombrement_emoji = {
        "PETIT": "📦 PETIT (facile a stocker)",
        "MOYEN": "🗃️ MOYEN",
        "GRAND": "🏠 GRAND (encombrant)"
    }.get(encombrement_up.split()[0] if encombrement_up else "MOYEN", "🗃️ MOYEN")

    facilite_emoji = {
        "FACILE": "✅ FACILE",
        "MOYEN":  "🟡 MOYEN",
        "DIFFICILE": "⚠️ DIFFICILE (fragile/lourd)"
    }.get(facilite.upper(), "🟡 MOYEN")

    return (
        f"🔎 OBJET IDENTIFIE\n{objet}\n\n"

        f"🌐 ANNONCES TROUVEES\n"
        f"✅ Vendu  🔨 Adjuge  🔵 En vente\n"
        f"{annonces}\n\n"

        f"💰 PRIX DU MARCHE\n"
        f"• Prix bas    : {int(prix_bas)} euros\n"
        f"• Prix moyen  : {int(prix_moyen)} euros\n"
        f"• Prix haut   : {int(prix_haut)} euros\n\n"

        f"📦 ANALYSE LOGISTIQUE\n"
        f"• Poids estimé    : {poids}\n"
        f"• Dimensions      : {dimensions}\n"
        f"• Encombrement    : {encombrement_emoji}\n"
        f"• Envoi           : {facilite_emoji}\n\n"

        f"📈 ANALYSE MARCHE\n"
        f"• Demande         : {emoji_demande}\n"
        f"• Vitesse de vente : {emoji_vitesse}\n"
        f"• {raison}\n\n"

        f"✅ RECOMMANDATION ({label_regle})\n"
        f"• Prix de revente conseille : {int(prix_revente)} euros\n"
        f"• Prix achat maximum        : {int(achat_max)} euros\n"
        f"• Marge brute               : +{marge_euros} euros (+{marge_pct}%)\n"
        + (f"• {flexibilite}\n" if flexibilite else "") +
        f"\n"
        f"🏆 MEILLEURES PLATEFORMES\n{plateformes}\n\n"

        f"⚡ CONSEIL\n{conseil}\n\n"

        f"💡 Seuil decision : {int(achat_max)} euros maximum"
        + (f" (ou {achat_max_souple} euros avec souplesse)" if achat_max_souple != achat_max else "")
    )
