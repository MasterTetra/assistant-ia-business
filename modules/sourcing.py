"""
MODULE SOURCING — Analyse technique + marché complète
- Dimensions extraites automatiquement de la légende
- Recherche web ciblée
- Règles de rentabilité par palier
"""
import anthropic
import httpx
import base64
import re
import asyncio
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

PALIERS = [
    (0,    10,   3.0, 200, "x3 minimum"),
    (10,   50,   2.5, 150, "x2.5 minimum"),
    (50,   100,  2.0, 100, "x2 minimum"),
    (100,  300,  1.8,  80, "x1.8 minimum"),
    (300,  1000, 1.6,  60, "x1.6 minimum"),
    (1000, 9999, 1.4,  40, "x1.4 minimum"),
]

PROMPT_ID = """Analyse cet objet avec toutes les informations disponibles.
Informations fournies : {caption}

Reponds en 1 seule ligne tres precise :
[Marque/Artiste] [type exact] [matiere] [couleur/style] [epoque] [etat] [dimensions si connues]

Utilise TOUTES les informations fournies dans la legende."""

PROMPT_ANALYSE = """Tu es un expert en achat-revente d'objets d'occasion avec 20 ans d'experience.

Objet : {objet}
Informations techniques connues : {caption}

ETAPE 1 - Fais 2 recherches dans cet ordre OBLIGATOIRE :
- Recherche 1 : "{requete_1} prix" — cherche des annonces actives et ventes terminees
- Recherche 2 : "{requete_2} vendu" — cherche specifiquement des ventes realisees

Si peu de resultats, essaie aussi : "{requete_3} prix vente"

IMPORTANT : Cherche sur ebay.fr, leboncoin.fr, catawiki.com, selency.fr, pamono.com, 1stdibs.com

ETAPE 2 - Reponds avec ce format exact, sans markdown :

OBJET: [nom complet precis]
ANNONCES:
[site | prix euros | VENDU ou EN VENTE ou ADJUGE | etat]
BAS: [chiffre]
MOYEN: [chiffre]
HAUT: [chiffre]
REVENTE: [chiffre conseille base sur les ventes reelles]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
VITESSE: [RAPIDE ou NORMALE ou LENTE]
RAISON: [phrase sur tendance marche actuelle]
POIDS: [{poids}]
DIMENSIONS: [{dimensions}]
ENCOMBREMENT: [PETIT ou MOYEN ou GRAND]
FACILITE_ENVOI: [FACILE ou MOYEN ou DIFFICILE]
PLATEFORMES: [liste ordonnee par pertinence pour cet objet]
CONSEIL: [conseil pratique base sur les donnees trouvees]"""


def _extraire_dimensions(caption: str):
    """Extrait dimensions et poids depuis la légende."""
    dims = ""
    poids = "A estimer"

    if not caption:
        return dims, poids

    # Chercher pattern dimensions : 12cm * 8cm * 29cm ou 12x8x29 ou 12 x 8 x 29
    m = re.search(
        r'(\d+[\.,]?\d*)\s*(?:cm|mm|m)?\s*[x\*×]\s*(\d+[\.,]?\d*)\s*(?:cm|mm|m)?\s*[x\*×]\s*(\d+[\.,]?\d*)\s*(cm|mm|m)?',
        caption, re.IGNORECASE
    )
    if m:
        d1, d2, d3 = m.group(1), m.group(2), m.group(3)
        unite = m.group(4) or "cm"
        dims = f"{d1} x {d2} x {d3} {unite}"
        # Estimer encombrement
        try:
            max_dim = max(float(d1), float(d2), float(d3))
            if max_dim < 30:
                encombrement = "PETIT"
            elif max_dim < 60:
                encombrement = "MOYEN"
            else:
                encombrement = "GRAND"
        except:
            encombrement = "MOYEN"
    else:
        # Chercher dimension seule
        m2 = re.search(r'(\d+[\.,]?\d*)\s*(cm|mm)', caption, re.IGNORECASE)
        if m2:
            dims = f"{m2.group(1)} {m2.group(2)}"

    # Chercher poids
    m_poids = re.search(r'(\d+[\.,]?\d*)\s*(kg|g|grammes?|kilos?)', caption, re.IGNORECASE)
    if m_poids:
        poids = f"{m_poids.group(1)} {m_poids.group(2)}"

    return dims, poids


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    try:
        # Extraire dimensions depuis la légende AVANT tout
        dims_connues, poids_connu = _extraire_dimensions(caption)

        # Télécharger photo
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")
        media_type = "image/png" if "png" in resp.headers.get("content-type", "") else "image/jpeg"

        # Appel 1 : identification précise avec toutes les infos dispo
        r1 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=120,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": PROMPT_ID.format(caption=caption or "aucune information")}
                ]
            }]
        )
        objet = r1.content[0].text.strip().split("\n")[0]
        objet = re.sub(r'[«»""\*#_]', '', objet).strip()

        # Version courte pour la recherche (sans dimensions)
        objet_court = re.sub(r'\d+\s*(?:cm|mm|x|\*)', '', objet).strip()
        objet_court = re.sub(r'\s+', ' ', objet_court).strip()

        await asyncio.sleep(2)

        # Construire 3 requêtes de recherche progressives
        # Du plus spécifique au plus général
        mots = objet_court.split()
        requete_1 = " ".join(mots[:4]) if len(mots) >= 4 else objet_court  # ex: "César Baldaccini lampe cristal"
        requete_2 = " ".join(mots[:3]) if len(mots) >= 3 else objet_court  # ex: "César Baldaccini lampe"
        requete_3 = " ".join(mots[:2]) if len(mots) >= 2 else mots[0]      # ex: "César Baldaccini"

        # Appel 2 : recherche web + analyse
        r2 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": PROMPT_ANALYSE.format(
                    objet=objet,
                    objet_court=objet_court,
                    requete_1=requete_1,
                    requete_2=requete_2,
                    requete_3=requete_3,
                    caption=caption or "aucune",
                    poids=poids_connu if poids_connu != "A estimer" else "A estimer selon photo",
                    dimensions=dims_connues if dims_connues else "A estimer selon photo"
                )
            }]
        )

        raw = ""
        for block in r2.content:
            if hasattr(block, "text") and block.text:
                raw = block.text

        raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
        raw = re.sub(r'\*(.+?)\*', r'\1', raw)
        raw = re.sub(r'#{1,6}\s+', '', raw)

        return _build(raw, objet, dims_connues, poids_connu)

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


def _build(data, objet_fallback, dims_connues="", poids_connu=""):
    objet        = _get(data, "OBJET") or objet_fallback
    annonces     = _annonces(data)
    plateformes  = _get(data, "PLATEFORMES") or "eBay, Leboncoin, Catawiki"
    conseil      = _get(data, "CONSEIL") or "Verifiez l'etat avant achat."
    demande      = _get(data, "DEMANDE") or "MOYENNE"
    vitesse      = _get(data, "VITESSE") or "NORMALE"
    raison       = _get(data, "RAISON") or ""

    # Dimensions : priorité à ce qui est fourni dans la légende
    poids        = poids_connu if poids_connu and poids_connu != "A estimer" else (_get(data, "POIDS") or "Non estime")
    dimensions   = dims_connues if dims_connues else (_get(data, "DIMENSIONS") or "Non estimees")
    encombrement = _get(data, "ENCOMBREMENT") or "MOYEN"
    facilite     = _get(data, "FACILITE_ENVOI") or "MOYEN"

    # Si on a les dimensions, recalculer l'encombrement
    if dims_connues:
        nums = re.findall(r'\d+', dims_connues)
        if nums:
            max_dim = max(float(n) for n in nums)
            if max_dim < 30:
                encombrement = "PETIT"
            elif max_dim < 60:
                encombrement = "MOYEN"
            else:
                encombrement = "GRAND"

    prix_bas     = _num(data, "BAS")
    prix_moyen   = _num(data, "MOYEN")
    prix_haut    = _num(data, "HAUT")
    prix_revente = _num(data, "REVENTE")

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

    # Calcul achat max selon paliers
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

    marge_euros = int(prix_revente - achat_max)
    marge_pct   = round(marge_euros / achat_max * 100) if achat_max > 0 else 0

    # Souplesse selon encombrement et vitesse
    encombrement_up = encombrement.upper()
    vitesse_up = vitesse.upper()
    flexibilite = ""
    achat_max_souple = achat_max

    if "RAPIDE" in vitesse_up and "PETIT" in encombrement_up:
        achat_max_souple = int(achat_max * 1.15)
        flexibilite = f"✅ Souplesse +15% (petit + vente rapide) → jusqu'a {achat_max_souple} euros"
    elif "RAPIDE" in vitesse_up and "MOYEN" in encombrement_up:
        achat_max_souple = int(achat_max * 1.10)
        flexibilite = f"✅ Souplesse +10% (vente rapide) → jusqu'a {achat_max_souple} euros"
    elif "LENTE" in vitesse_up or "GRAND" in encombrement_up:
        flexibilite = "⚠️ Seuil strict (encombrant ou vente lente)"

    # Emojis
    if "FORTE" in demande.upper():    emoji_d = "🔥 FORTE"
    elif "FAIBLE" in demande.upper(): emoji_d = "🔵 FAIBLE"
    else:                             emoji_d = "🟡 MOYENNE"

    if "RAPIDE" in vitesse_up:        emoji_v = "⚡ RAPIDE"
    elif "LENTE" in vitesse_up:       emoji_v = "🐢 LENTE"
    else:                             emoji_v = "🕐 NORMALE"

    enc_map = {"PETIT": "📦 PETIT (facile a stocker)", "MOYEN": "🗃️ MOYEN", "GRAND": "🏠 GRAND (encombrant)"}
    emoji_enc = enc_map.get(encombrement_up.split()[0] if encombrement_up else "MOYEN", "🗃️ MOYEN")

    fac_map = {"FACILE": "✅ FACILE", "MOYEN": "🟡 MOYEN", "DIFFICILE": "⚠️ DIFFICILE"}
    emoji_fac = fac_map.get(facilite.upper(), "🟡 MOYEN")

    msg = (
        f"🔎 OBJET IDENTIFIE\n{objet}\n\n"
        f"🌐 ANNONCES TROUVEES\n"
        f"✅ Vendu  🔨 Adjuge  🔵 En vente\n"
        f"{annonces}\n\n"
        f"💰 PRIX DU MARCHE\n"
        f"• Prix bas    : {int(prix_bas)} euros\n"
        f"• Prix moyen  : {int(prix_moyen)} euros\n"
        f"• Prix haut   : {int(prix_haut)} euros\n\n"
        f"📦 ANALYSE LOGISTIQUE\n"
        f"• Poids       : {poids}\n"
        f"• Dimensions  : {dimensions}\n"
        f"• Encombrement: {emoji_enc}\n"
        f"• Envoi       : {emoji_fac}\n\n"
        f"📈 ANALYSE MARCHE\n"
        f"• Demande          : {emoji_d}\n"
        f"• Vitesse de vente : {emoji_v}\n"
        + (f"• {raison}\n" if raison else "") +
        f"\n✅ RECOMMANDATION ({label_regle})\n"
        f"• Prix de revente conseille : {int(prix_revente)} euros\n"
        f"• Prix achat maximum        : {int(achat_max)} euros\n"
        f"• Marge brute               : +{marge_euros} euros (+{marge_pct}%)\n"
        + (f"• {flexibilite}\n" if flexibilite else "") +
        f"\n🏆 MEILLEURES PLATEFORMES\n{plateformes}\n\n"
        f"⚡ CONSEIL\n{conseil}\n\n"
        f"💡 Seuil decision : {int(achat_max)} euros maximum"
        + (f" (ou {achat_max_souple} euros avec souplesse)" if achat_max_souple != achat_max else "")
    )
    return msg
