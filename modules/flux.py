"""
MODULE FLUX — Analyse marché + génération annonce
2 appels séparés : 1) recherche web libre  2) analyse structurée
"""
import anthropic
import httpx
import base64
import re
import asyncio
import logging
from datetime import datetime
from config.settings import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS
)

logger = logging.getLogger(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

PALIERS = [
    (0,    10,   3.0, "x3"),
    (10,   50,   2.5, "x2.5"),
    (50,   100,  2.0, "x2"),
    (100,  300,  1.8, "x1.8"),
    (300,  1000, 1.6, "x1.6"),
    (1000, 9999, 1.4, "x1.4"),
]

PROMPT_STRUCT = """Tu es un expert en achat-revente. Analyse cet objet et remplis TOUS les champs.

OBJET : {objet}
INFOS : {caption}
DONNEES MARCHE : {market_data}

Reponds UNIQUEMENT avec ce format exact, sans asterisques, sans markdown :

OBJET: [nom complet et précis]
ANNONCES:
[plateforme | prix euros | VENDU ou EN VENTE | etat]
PRIX_BAS: [chiffre — jamais 0]
PRIX_MOYEN: [chiffre — jamais 0]
PRIX_HAUT: [chiffre — jamais 0]
PRIX_REVENTE: [prix conseille — jamais 0]
NB_ANNONCES: [nombre]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
VITESSE: [RAPIDE ou NORMALE ou LENTE]
POIDS: [grammes]
DIMENSIONS: [{dimensions}]
ENCOMBREMENT: [PETIT ou MOYEN ou GRAND]
ENVOI: [FACILE ou MOYEN ou DIFFICILE]
RAISON: [phrase courte sur le marche]"""

PROMPT_ANNONCE = """Tu es expert en vente eBay et Leboncoin.

OBJET : {objet}
DESCRIPTION : {description}
ETAT : {etat}
PRIX : {prix} euros

Reponds UNIQUEMENT avec ce format, sans asterisques :

TITRE_EBAY: [max 80 caracteres, mots-cles en premier]
TITRE_LBC: [max 70 caracteres]
TITRE_VINTED: [max 60 caracteres]
DESCRIPTION:
[200-300 mots, points forts, etat, caracteristiques]
FIN_DESCRIPTION
MOTS_CLES: [15 mots-cles separes par virgules]
CATEGORIE: [categorie principale]
CONSEIL: [1 conseil pratique]"""


async def _retry(func, *args, **kwargs):
    for attempt in range(4):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e) and attempt < 3:
                await asyncio.sleep(15 * (attempt + 1))
            else:
                raise


def _get(text, key):
    m = re.search(rf'^{key}\s*:\s*(.+)', text, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _num(text, key):
    val = _get(text, key)
    nums = re.findall(r'\d+', val.replace(" ", ""))
    return int(nums[0]) if nums else 0


def _calcul_palier(prix_revente):
    for (amin, amax, mult, label) in PALIERS:
        achat = int(prix_revente / mult)
        if amin <= achat < amax:
            return achat, mult, label
    return int(prix_revente / 1.4), 1.4, "x1.4"


def _annonces(text):
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=\n[A-Z_]+:)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return "  • Aucune annonce trouvee"
    lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
    result = []
    for line in lines[:6]:
        lu = line.upper()
        if "VENDU" in lu or "SOLD" in lu:
            result.append(f"  [V] {line}")
        else:
            result.append(f"  [.] {line}")
    return "\n".join(result) if result else "  • Aucune annonce trouvee"


def _extraire_dims(caption):
    if not caption:
        return ""
    m = re.search(
        r'(\d+[\.,]?\d*)\s*(?:cm)?\s*[x*x]\s*(\d+[\.,]?\d*)\s*(?:cm)?\s*[x*x]\s*(\d+[\.,]?\d*)\s*(cm)?',
        caption, re.IGNORECASE
    )
    return f"{m.group(1)} x {m.group(2)} x {m.group(3)} cm" if m else ""


async def analyser_marche(photo_url: str, caption: str) -> dict:
    objet = caption or "objet inconnu"
    mots = objet.split()
    objet_court = " ".join(mots[:4])
    dims = _extraire_dims(caption)

    # Telecharger photo
    image_content = []
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.get(photo_url)
            img = base64.standard_b64encode(resp.content).decode()
            mt = "image/png" if "png" in resp.headers.get("content-type", "") else "image/jpeg"
            image_content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": img}}]
    except Exception as e:
        logger.warning(f"Photo download failed: {e}")

    # ── APPEL 1 : recherche web libre ─────────────────────
    market_data = ""
    try:
        r1 = await _retry(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=600,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": [{
                "type": "text",
                "text": (
                    f'Recherche le prix de marche de cet objet : "{objet_court}"\n'
                    f'Fais 2 recherches : 1) "{objet_court} eBay prix"  2) "{objet_court} eBay sold"\n'
                    f'Donne-moi : liste des annonces avec prix, prix bas/moyen/haut des ventes conclues, '
                    f'estimation prix revente. Texte court.'
                )
            }]}]
        )
        for block in r1.content:
            if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
                market_data += block.text + "\n"
        logger.info(f"MARKET DATA:\n{market_data[:500]}")
    except Exception as e:
        logger.warning(f"Web search failed: {e}")
        market_data = "Pas de donnees web disponibles — utilise tes connaissances."

    # ── APPEL 2 : analyse structuree ──────────────────────
    prompt = PROMPT_STRUCT.format(
        objet=objet,
        caption=caption or "aucune",
        market_data=market_data[:800] if market_data else "Estime d'apres tes connaissances.",
        dimensions=dims or "a estimer"
    )

    r2 = await _retry(
        client.messages.create,
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": image_content + [{"type": "text", "text": prompt}]}]
    )

    raw = ""
    for block in r2.content:
        if hasattr(block, "text") and block.text:
            raw += block.text + "\n"
    raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
    raw = re.sub(r'\*(.+?)\*', r'\1', raw)
    logger.info(f"RAW STRUCT:\n{raw[:800]}")

    objet_id  = _get(raw, "OBJET") or objet
    prix_bas  = _num(raw, "PRIX_BAS")
    prix_moy  = _num(raw, "PRIX_MOYEN")
    prix_haut = _num(raw, "PRIX_HAUT")
    prix_rev  = _num(raw, "PRIX_REVENTE") or prix_moy or prix_bas
    nb        = _get(raw, "NB_ANNONCES") or "?"
    demande   = _get(raw, "DEMANDE") or "MOYENNE"
    vitesse   = _get(raw, "VITESSE") or "NORMALE"
    poids     = _get(raw, "POIDS") or "?"
    dimensions = dims or _get(raw, "DIMENSIONS") or "?"
    encombr   = _get(raw, "ENCOMBREMENT") or "MOYEN"
    envoi     = _get(raw, "ENVOI") or "MOYEN"
    raison    = _get(raw, "RAISON") or ""
    annonces  = _annonces(raw)

    if dims:
        nums = re.findall(r'\d+', dims)
        if nums:
            mx = max(float(n) for n in nums)
            encombr = "PETIT" if mx < 30 else "MOYEN" if mx < 60 else "GRAND"

    achat_max, mult, label = _calcul_palier(prix_rev) if prix_rev > 0 else (0, 3.0, "x3")

    return {
        "objet": objet_id,
        "caption": caption,
        "photo_url": photo_url,
        "annonces": annonces,
        "nb_annonces": nb,
        "prix_bas": prix_bas,
        "prix_moyen": prix_moy,
        "prix_haut": prix_haut,
        "prix_revente": prix_rev,
        "achat_max": achat_max,
        "mult": mult,
        "label": label,
        "demande": demande,
        "vitesse": vitesse,
        "poids": poids,
        "dimensions": dimensions,
        "encombrement": encombr,
        "envoi": envoi,
        "raison": raison,
    }


async def generer_annonce(data: dict, etat: str = "") -> dict:
    r = await _retry(
        client.messages.create,
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT_ANNONCE.format(
            objet=data["objet"],
            description=data["caption"],
            etat=etat or "Bon etat",
            prix=data["prix_revente"]
        )}]
    )
    raw = r.content[0].text if r.content else ""
    raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
    raw = re.sub(r'\*(.+?)\*', r'\1', raw)

    desc_m = re.search(r'DESCRIPTION:\s*\n(.*?)FIN_DESCRIPTION', raw, re.DOTALL)
    data["titre_ebay"]   = _get(raw, "TITRE_EBAY") or data["objet"]
    data["titre_lbc"]    = _get(raw, "TITRE_LBC") or data["objet"]
    data["titre_vinted"] = _get(raw, "TITRE_VINTED") or data["objet"]
    data["description"]  = desc_m.group(1).strip() if desc_m else data["caption"]
    data["mots_cles"]    = _get(raw, "MOTS_CLES") or ""
    data["categorie"]    = _get(raw, "CATEGORIE") or ""
    data["conseil"]      = _get(raw, "CONSEIL") or ""
    return data


def formater_analyse(data: dict) -> str:
    em_d   = {"FORTE": "FORTE", "MOYENNE": "MOYENNE", "FAIBLE": "FAIBLE"}
    em_v   = {"RAPIDE": "RAPIDE", "NORMALE": "NORMALE", "LENTE": "LENTE"}
    em_e   = {"PETIT": "PETIT", "MOYEN": "MOYEN", "GRAND": "GRAND"}
    em_env = {"FACILE": "FACILE", "MOYEN": "MOYEN", "DIFFICILE": "DIFFICILE"}

    annonces = data["annonces"].replace("[V]", "✅").replace("[.]", "🔵")

    return (
        f"OBJET IDENTIFIE\n{data['objet']}\n\n"
        f"ANNONCES ({data['nb_annonces']} references)\n"
        f"{annonces}\n\n"
        f"PRIX DU MARCHE\n"
        f"Bas    : {data['prix_bas']} euros\n"
        f"Moyen  : {data['prix_moyen']} euros\n"
        f"Haut   : {data['prix_haut']} euros\n\n"
        f"LOGISTIQUE\n"
        f"Poids       : {data['poids']}\n"
        f"Dimensions  : {data['dimensions']}\n"
        f"Encombrement: {em_e.get(data['encombrement'].upper(), data['encombrement'])}\n"
        f"Envoi       : {em_env.get(data['envoi'].upper(), data['envoi'])}\n\n"
        f"MARCHE\n"
        f"Demande : {em_d.get(data['demande'].upper(), data['demande'])}\n"
        f"Vitesse : {em_v.get(data['vitesse'].upper(), data['vitesse'])}\n"
        + (f"{data['raison']}\n" if data["raison"] else "") +
        f"\nESTIMATION\n"
        f"Prix de revente conseille : {data['prix_revente']} euros\n"
        f"Prix d'achat maximum      : {data['achat_max']} euros ({data['label']})\n"
    )


def formater_rentabilite(data: dict, prix_achat: float) -> str:
    prix_rev  = data.get("prix_revente", 0)
    achat_max = data.get("achat_max", 0)
    marge     = prix_rev - prix_achat
    marge_pct = round(marge / prix_achat * 100) if prix_achat > 0 else 0
    ok        = prix_achat <= achat_max or achat_max == 0

    if ok:
        status = f"BON ACHAT — sous le seuil de {achat_max} euros"
    else:
        dep = round((prix_achat - achat_max) / achat_max * 100) if achat_max > 0 else 0
        status = f"AU-DESSUS du seuil ({dep}% de plus que {achat_max} euros conseilles)"

    return (
        f"ANALYSE RENTABILITE\n"
        f"Prix d'achat       : {prix_achat} euros\n"
        f"Prix revente cible : {prix_rev} euros\n"
        f"Marge brute        : +{marge} euros (+{marge_pct}%)\n"
        f"{status}\n"
    )


def formater_annonce(data: dict) -> str:
    return (
        f"ANNONCE GENEREE\n"
        f"{'='*35}\n\n"
        f"TITRE eBay :\n{data.get('titre_ebay', '')}\n\n"
        f"TITRE LBC :\n{data.get('titre_lbc', '')}\n\n"
        f"TITRE VINTED :\n{data.get('titre_vinted', '')}\n\n"
        f"PRIX\n"
        f"eBay    : {data['prix_revente']} euros\n"
        f"LBC     : {int(data['prix_revente'] * 0.9)} euros\n"
        f"Vinted  : {int(data['prix_revente'] * 0.85)} euros\n\n"
        f"DESCRIPTION\n{data.get('description', '')}\n\n"
        f"MOTS-CLES\n{data.get('mots_cles', '')}\n\n"
        f"CONSEIL : {data.get('conseil', '')}"
    )


async def generer_ref() -> str:
    annee = datetime.now().strftime("%Y")
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={
                    "fields[]": ["Reference gestion"],
                    "filterByFormula": f"FIND('{annee}', {{Reference gestion}})",
                    "maxRecords": 1000
                }
            )
        records = resp.json().get("records", [])
        nums = []
        for r in records:
            ref = r.get("fields", {}).get("Reference gestion", "")
            m = re.search(r'RG-\d{4}-(\d+)', ref)
            if m:
                nums.append(int(m.group(1)))
        n = max(nums) + 1 if nums else 1
        return f"RG-{annee}-{str(n).zfill(3)}"
    except Exception as e:
        logger.warning(f"generer_ref error: {e}")
        return f"RG-{annee}-001"


async def archiver(data: dict, ref: str, prix_achat_total: float, source: str,
                   quantite: int = 1) -> bool:
    try:
        prix_unitaire = round(prix_achat_total / quantite, 4) if quantite > 0 else prix_achat_total
        annonce = (
            f"TITRE EBAY: {data.get('titre_ebay', '')}\n"
            f"TITRE LBC: {data.get('titre_lbc', '')}\n"
            f"TITRE VINTED: {data.get('titre_vinted', '')}\n\n"
            f"{data.get('description', '')}\n\n"
            f"MOTS-CLES: {data.get('mots_cles', '')}"
        )
        fields = {
            "Référence": ref,
            "Référence gestion": ref,
            "Description": data.get("caption") or data.get("objet", ""),
            "Prix achat total": round(float(prix_achat_total), 2),
            "Prix achat unitaire": round(float(prix_unitaire), 4),
            "Prix vente": round(float(data["prix_revente"]), 2),
            "Source": source or "Non renseigne",
            "Statut": "en ligne",
            "Annonce générée": annonce,
            "Date achat": datetime.now().strftime("%Y-%m-%d"),
            "Notes": data.get("conseil", ""),
            "Quantite totale": int(quantite),
            "Quantite vendue": 0,
        }
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                json={"fields": fields}
            )
        if resp.status_code not in (200, 201):
            logger.error(f"Airtable error {resp.status_code}: {resp.text[:500]}")
            return False
        return True
    except Exception as e:
        logger.error(f"archiver error: {e}", exc_info=True)
        return False
