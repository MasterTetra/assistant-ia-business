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

INSTRUCTIONS - Fais exactement 3 recherches web dans cet ordre :

RECHERCHE 1 : "{objet} vendu prix" sur ebay.fr
But : trouver les ventes TERMINEES et reellement conclues sur eBay France.
Cherche les completed listings / objets vendus.

RECHERCHE 2 : "{objet} prix" sur leboncoin.fr et catawiki.com
But : trouver les annonces actives actuelles et les resultats d'encheres passes.

RECHERCHE 3 : "{objet} adjugé resultat" sur interencheres.com ou drouot.com
But : trouver les prix d'adjudication en ventes aux encheres.

Pour chaque annonce trouvee, precise si c'est :
- VENDU (vente reellement conclue) - le plus fiable
- EN VENTE (annonce active) - prix demande
- ADJUGE (resultat encheres) - prix final paye

Reponds UNIQUEMENT avec ce bloc, sans rien avant ni apres :

---DEBUT---
OBJET: {objet}
ANNONCES:
[liste chaque resultat : site | prix euros | VENDU/EN VENTE/ADJUGE | etat]
PRIX_BAS: [nombre entier, ex: 150]
PRIX_MOYEN: [nombre entier, ex: 300]
PRIX_HAUT: [nombre entier, ex: 500]
PRIX_REVENTE: [nombre entier conseille base sur les ventes reelles]
PRIX_ACHAT_MAX: [nombre entier maximum a payer pour rester rentable]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
RAISON: [une phrase basee sur les ventes trouvees]
PLATEFORMES: [meilleures plateformes selon les resultats]
CONSEIL: [conseil base sur les vraies donnees de vente]
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

        result = _build_message(data, objet)

        # Si l'extraction a echoue (prix tous a 0), relancer avec prompt simplifie
        if "Analyse incomplete" in result:
            r3 = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Quel est le prix de vente de : {objet} ?\n"
                        f"Cherche sur ebay.fr, leboncoin.fr, catawiki.com.\n"
                        f"Donne moi les prix trouves sous ce format exact sans markdown :\n"
                        f"---DEBUT---\n"
                        f"OBJET: {objet}\n"
                        f"ANNONCES:\n"
                        f"[une annonce par ligne : site | prix | statut | etat]\n"
                        f"PRIX_BAS: [chiffre]\n"
                        f"PRIX_MOYEN: [chiffre]\n"
                        f"PRIX_HAUT: [chiffre]\n"
                        f"PRIX_REVENTE: [chiffre]\n"
                        f"PRIX_ACHAT_MAX: [chiffre]\n"
                        f"DEMANDE: [FORTE ou MOYENNE ou FAIBLE]\n"
                        f"RAISON: [une phrase]\n"
                        f"PLATEFORMES: [liste]\n"
                        f"CONSEIL: [une phrase]\n"
                        f"---FIN---"
                    )
                }]
            )
            raw3 = ""
            for block in r3.content:
                if hasattr(block, "text") and block.text:
                    raw3 = block.text
            raw3 = re.sub(r'\*\*(.+?)\*\*', r'\1', raw3)
            raw3 = re.sub(r'\*(.+?)\*', r'\1', raw3)
            match3 = re.search(r'---DEBUT---(.*?)---FIN---', raw3, re.DOTALL)
            data3 = match3.group(1).strip() if match3 else raw3
            result = _build_message(data3, objet)

        return result

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
    """Extrait le bloc annonces avec distinction ventes reelles / annonces actives."""
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=PRIX_BAS|\Z)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return "  • Voir plateformes recommandees"
    
    lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
    result = []
    for line in lines[:6]:
        line_up = line.upper()
        if "VENDU" in line_up:
            result.append(f"  ✅ {line}")   # Vente reelle conclue
        elif "ADJUGE" in line_up:
            result.append(f"  🔨 {line}")   # Resultat encheres
        elif "EN VENTE" in line_up:
            result.append(f"  🔵 {line}")   # Annonce active
        else:
            result.append(f"  • {line}")
    return "\n".join(result) if result else "  • Voir plateformes recommandees"


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
        f"🌐 ANNONCES TROUVEES\n"
        f"✅ Vendu  🔨 Adjuge  🔵 En vente\n"
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
