"""
MODULE FLUX — Flux unique de vente
Photo + légende → Analyse marché → Décision marge → Annonce → Archivage
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
HEADERS_AT  = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

PALIERS = [
    (0,    10,   3.0, "x3"),
    (10,   50,   2.5, "x2.5"),
    (50,   100,  2.0, "x2"),
    (100,  300,  1.8, "x1.8"),
    (300,  1000, 1.6, "x1.6"),
    (1000, 9999, 1.4, "x1.4"),
]

# ─── PROMPT ÉTAPE 1 : ANALYSE MARCHÉ ──────────────────────
PROMPT_ANALYSE = """Tu es un expert en achat-revente d'objets d'occasion avec 20 ans d'experience.

OBJET A ANALYSER : {objet}
INFOS SUPPLEMENTAIRES : {caption}

ETAPE 1 — Identifie précisément l'objet sur la photo et dans la légende.

ETAPE 2 — Lance ces recherches dans cet ordre :
1. "{objet_court} eBay" — prix actuels
2. "{objet_court} eBay completed sold" — ventes conclues
3. Si peu de résultats : essaie "{objet_court} site:catawiki.com" ou "{objet_court} price"

REGLES IMPORTANTES :
- Si tu trouves des ventes conclues (SOLD), utilise-les comme référence principale
- Si tu ne trouves QUE des annonces actives, utilise-les avec prudence
- Si tu ne trouves RIEN du tout : estime d'après ton expertise (mets NB_ANNONCES: 0 estimé)
- Ne mets JAMAIS 0 pour PRIX_REVENTE — fais toujours une estimation même sans résultat
- Pour objets rares/artistiques/signés : leur valeur est souvent plus haute que les résultats basiques

Reponds UNIQUEMENT avec ce format exact, sans markdown, sans astérisques :

OBJET: [nom précis et complet]
ANNONCES:
[plateforme | prix euros | VENDU ou EN VENTE | état]
PRIX_BAS: [chiffre seul]
PRIX_MOYEN: [chiffre seul]
PRIX_HAUT: [chiffre seul]
PRIX_REVENTE: [prix conseillé — jamais 0]
NB_ANNONCES: [nombre ou "0 estimé"]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
VITESSE: [RAPIDE ou NORMALE ou LENTE]
POIDS: [estimation grammes, chiffre seul]
DIMENSIONS: [{dimensions}]
ENCOMBREMENT: [PETIT ou MOYEN ou GRAND]
ENVOI: [FACILE ou MOYEN ou DIFFICILE]
RAISON: [phrase courte sur le marché de cet objet]"""

# ─── PROMPT ÉTAPE 2 : GÉNÉRATION ANNONCE ──────────────────
PROMPT_ANNONCE = """Tu es un expert en vente sur eBay et Leboncoin.

OBJET : {objet}
DESCRIPTION : {description}
ÉTAT : {etat}
PRIX VENTE : {prix}€

Génère l'annonce parfaite. Reponds UNIQUEMENT avec ce format, sans markdown :

TITRE_EBAY: [titre SEO optimisé max 80 caractères, mots-clés importants en premier]
TITRE_LBC: [titre naturel max 70 caractères]
TITRE_VINTED: [titre simple max 60 caractères]
DESCRIPTION:
[description 200-300 mots, naturelle, points forts, état, caractéristiques, appel à l'action]
FIN_DESCRIPTION
MOTS_CLES: [15 mots-clés séparés par virgules]
CATEGORIE: [catégorie principale]
CONSEIL: [1 conseil pour maximiser la vente]"""


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
    n = re.findall(r'\d+', val.replace(" ", ""))
    return int(n[0]) if n else 0

def _calcul_palier(prix_revente):
    for (amin, amax, mult, label) in PALIERS:
        achat = int(prix_revente / mult)
        if amin <= achat < amax:
            return achat, mult, label
    return int(prix_revente / 1.4), 1.4, "x1.4"

def _annonces(text):
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=\nPRIX_BAS:)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return "  • Aucune annonce trouvée"
    lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
    result = []
    for line in lines[:6]:
        lu = line.upper()
        if "VENDU" in lu or "SOLD" in lu:
            result.append(f"  ✅ {line}")
        elif "ADJUGE" in lu:
            result.append(f"  🔨 {line}")
        else:
            result.append(f"  🔵 {line}")
    return "\n".join(result) if result else "  • Aucune annonce trouvée"


async def analyser_marche(photo_url: str, caption: str) -> dict:
    """Étape 1 — Analyse marché complète."""
    objet = caption or "objet inconnu"
    mots = objet.split()
    objet_court = " ".join(mots[:4])
    dims = _extraire_dims(caption)

    # Télécharger photo
    image_content = []
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            resp = await http.get(photo_url)
            img = base64.standard_b64encode(resp.content).decode()
            mt = "image/png" if "png" in resp.headers.get("content-type","") else "image/jpeg"
            image_content = [{"type":"image","source":{"type":"base64","media_type":mt,"data":img}}]
    except:
        pass

    import logging
    logger = logging.getLogger(__name__)

    prompt_text = PROMPT_ANALYSE.format(
        objet=objet, objet_court=objet_court,
        caption=caption or "aucune",
        dimensions=dims or "à estimer"
    )

    messages = [{"role": "user", "content": image_content + [{"type": "text", "text": prompt_text}]}]

    # Boucle multi-tours pour laisser Claude utiliser les outils de recherche
    raw = ""
    for _ in range(6):
        response = await _retry(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages
        )

        # Collecter le texte et vérifier si on doit continuer
        has_tool_use = False
        for block in response.content:
            if hasattr(block, "text") and block.text:
                raw = block.text
            if block.type == "tool_use":
                has_tool_use = True

        if response.stop_reason == "end_turn" or not has_tool_use:
            break

        # Ajouter la réponse de Claude à la conversation et continuer
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": block.id, "content": ""}
            for block in response.content if block.type == "tool_use"
        ]})
        await asyncio.sleep(2)

    raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
    raw = re.sub(r'\*(.+?)\*', r'\1', raw)
    logger.info(f"RAW ANALYSE:\n{raw[:800]}")

    objet_id   = _get(raw, "OBJET") or objet
    prix_bas   = _num(raw, "PRIX_BAS")
    prix_moyen = _num(raw, "PRIX_MOYEN")
    prix_haut  = _num(raw, "PRIX_HAUT")
    prix_rev   = _num(raw, "PRIX_REVENTE") or prix_moyen
    nb         = _get(raw, "NB_ANNONCES") or "?"
    demande    = _get(raw, "DEMANDE") or "MOYENNE"
    vitesse    = _get(raw, "VITESSE") or "NORMALE"
    poids      = _get(raw, "POIDS") or "?"
    dimensions = dims or _get(raw, "DIMENSIONS") or "?"
    encombr    = _get(raw, "ENCOMBREMENT") or "MOYEN"
    envoi      = _get(raw, "ENVOI") or "MOYEN"
    raison     = _get(raw, "RAISON") or ""
    annonces   = _annonces(raw)

    # Recalcul encombrement si dims connues
    if dims:
        nums = re.findall(r'\d+', dims)
        if nums:
            mx = max(float(n) for n in nums)
            encombr = "PETIT" if mx < 30 else "MOYEN" if mx < 60 else "GRAND"

    achat_max, mult, label = _calcul_palier(prix_rev)
    marge = prix_rev - achat_max
    marge_pct = round(marge / achat_max * 100) if achat_max > 0 else 0

    # Marge suffisante ?
    marge_ok = marge_pct >= (mult - 1) * 100 * 0.8  # 80% du multiplicateur cible

    return {
        "objet": objet_id,
        "caption": caption,
        "photo_url": photo_url,
        "annonces": annonces,
        "nb_annonces": nb,
        "prix_bas": prix_bas,
        "prix_moyen": prix_moyen,
        "prix_haut": prix_haut,
        "prix_revente": prix_rev,
        "achat_max": achat_max,
        "mult": mult,
        "label": label,
        "marge": marge,
        "marge_pct": marge_pct,
        "marge_ok": marge_ok,
        "demande": demande,
        "vitesse": vitesse,
        "poids": poids,
        "dimensions": dimensions,
        "encombrement": encombr,
        "envoi": envoi,
        "raison": raison,
    }


async def generer_annonce(data: dict, etat: str = "") -> dict:
    """Étape 2 — Génération annonce complète."""
    response = await _retry(
        client.messages.create,
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role":"user","content": PROMPT_ANNONCE.format(
            objet=data["objet"],
            description=data["caption"],
            etat=etat or "Bon état",
            prix=data["prix_revente"]
        )}]
    )

    raw = response.content[0].text if response.content else ""
    raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
    raw = re.sub(r'\*(.+?)\*', r'\1', raw)

    desc_m = re.search(r'DESCRIPTION:\s*\n(.*?)FIN_DESCRIPTION', raw, re.DOTALL)
    description = desc_m.group(1).strip() if desc_m else data["caption"]

    data["titre_ebay"]   = _get(raw, "TITRE_EBAY") or data["objet"]
    data["titre_lbc"]    = _get(raw, "TITRE_LBC") or data["objet"]
    data["titre_vinted"] = _get(raw, "TITRE_VINTED") or data["objet"]
    data["description"]  = description
    data["mots_cles"]    = _get(raw, "MOTS_CLES") or ""
    data["categorie"]    = _get(raw, "CATEGORIE") or ""
    data["conseil"]      = _get(raw, "CONSEIL") or ""
    return data


def formater_analyse(data: dict) -> str:
    """Formate le message d'analyse marché — sans prix d'achat (inconnu)."""
    em_d = {"FORTE":"🔥 FORTE","MOYENNE":"🟡 MOYENNE","FAIBLE":"🔵 FAIBLE"}
    em_v = {"RAPIDE":"⚡ RAPIDE","NORMALE":"🕐 NORMALE","LENTE":"🐢 LENTE"}
    em_e = {"PETIT":"📦 PETIT","MOYEN":"🗃️ MOYEN","GRAND":"🏠 GRAND"}
    em_env = {"FACILE":"✅ FACILE","MOYEN":"🟡 MOYEN","DIFFICILE":"⚠️ DIFFICILE"}

    return (
        f"🔎 OBJET IDENTIFIE\n{data['objet']}\n\n"
        f"🌐 ANNONCES ({data['nb_annonces']} references)\n"
        f"✅ Vendu  🔵 En vente\n"
        f"{data['annonces']}\n\n"
        f"💰 PRIX DU MARCHE\n"
        f"• Bas    : {data['prix_bas']} euros\n"
        f"• Moyen  : {data['prix_moyen']} euros\n"
        f"• Haut   : {data['prix_haut']} euros\n\n"
        f"📦 LOGISTIQUE\n"
        f"• Poids       : {data['poids']}\n"
        f"• Dimensions  : {data['dimensions']}\n"
        f"• Encombrement: {em_e.get(data['encombrement'], data['encombrement'])}\n"
        f"• Envoi       : {em_env.get(data['envoi'], data['envoi'])}\n\n"
        f"📈 MARCHE\n"
        f"• Demande : {em_d.get(data['demande'].upper(), data['demande'])}\n"
        f"• Vitesse : {em_v.get(data['vitesse'].upper(), data['vitesse'])}\n"
        + (f"• {data['raison']}\n" if data['raison'] else "") +
        f"\n💡 ESTIMATION\n"
        f"• Prix de revente conseillé : {data['prix_revente']} euros\n"
        f"• Prix d'achat maximum      : {data['achat_max']} euros ({data['label']})\n"
    )


def formater_rentabilite(data: dict, prix_achat: float) -> str:
    """Calcule et formate la rentabilité une fois le prix d'achat connu."""
    marge = data["prix_revente"] - prix_achat
    marge_pct = round(marge / prix_achat * 100) if prix_achat > 0 else 0
    achat_max = data["achat_max"]
    ok = prix_achat <= achat_max

    if ok:
        status = f"✅ BON ACHAT — sous le seuil de {achat_max} euros"
    else:
        depassement = round((prix_achat - achat_max) / achat_max * 100) if achat_max > 0 else 0
        status = f"⚠️ AU-DESSUS du seuil ({depassement}% de plus que les {achat_max} euros conseillés)"

    return (
        f"💰 ANALYSE RENTABILITE\n"
        f"• Prix d'achat       : {prix_achat} euros\n"
        f"• Prix revente cible  : {data['prix_revente']} euros\n"
        f"• Marge brute         : +{marge} euros (+{marge_pct}%)\n"
        f"• {status}\n"
    )


def formater_annonce(data: dict) -> str:
    """Formate le message d'annonce générée."""
    return (
        f"📝 ANNONCE GENEREE\n"
        f"{'='*35}\n\n"
        f"🏷️ TITRE eBay :\n{data['titre_ebay']}\n\n"
        f"🏷️ TITRE LBC :\n{data['titre_lbc']}\n\n"
        f"🏷️ TITRE VINTED :\n{data['titre_vinted']}\n\n"
        f"💶 PRIX\n"
        f"• eBay    : {data['prix_revente']} euros\n"
        f"• LBC     : {int(data['prix_revente'] * 0.9)} euros\n"
        f"• Vinted  : {int(data['prix_revente'] * 0.85)} euros\n\n"
        f"📄 DESCRIPTION\n"
        f"{data['description']}\n\n"
        f"🔑 MOTS-CLES\n{data['mots_cles']}\n\n"
        f"💡 {data['conseil']}"
    )


async def generer_ref() -> str:
    """Génère la prochaine référence RG-AAAA-NNN."""
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
        n = max(nums) + 1 if nums else 1
        return f"RG-{annee}-{str(n).zfill(3)}"
    except:
        return f"RG-{annee}-001"


async def archiver(data: dict, ref: str, prix_achat: float, source: str) -> bool:
    """Archive dans Airtable."""
    try:
        annonce = (
            f"TITRE EBAY: {data.get('titre_ebay','')}\n"
            f"TITRE LBC: {data.get('titre_lbc','')}\n"
            f"TITRE VINTED: {data.get('titre_vinted','')}\n\n"
            f"{data.get('description','')}\n\n"
            f"MOTS-CLES: {data.get('mots_cles','')}"
        )
        fields = {
            "Référence": ref,
            "Référence gestion": ref,
            "Description": data["caption"] or data["objet"],
            "Prix achat": prix_achat,
            "Prix vente": data["prix_revente"],
            "Source": source or "Non renseigné",
            "Statut": "en ligne",
            "Annonce générée": annonce,
            "Date achat": datetime.now().strftime("%Y-%m-%d"),
            "Notes": data.get("conseil", ""),
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


def _extraire_dims(caption: str) -> str:
    if not caption:
        return ""
    m = re.search(
        r'(\d+[\.,]?\d*)\s*(?:cm)?\s*[x\*×]\s*(\d+[\.,]?\d*)\s*(?:cm)?\s*[x\*×]\s*(\d+[\.,]?\d*)\s*(cm)?',
        caption, re.IGNORECASE
    )
    return f"{m.group(1)} x {m.group(2)} x {m.group(3)} cm" if m else ""
