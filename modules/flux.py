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

PROMPT_ANNONCE = """Tu es expert en vente en ligne (eBay, Leboncoin, Vinted).

OBJET : {objet}
ETAT : {etat}
DETAILS SUPPLEMENTAIRES : {details}
DONNÉES MARCHÉ : {market_context}

REGLES STRICTES :
- NE PAS mentionner le prix dans le titre ni dans la description
- NE PAS mentionner l'expédition, la livraison, le transport, les frais de port
- NE PAS mentionner de remise en main propre
- Titre : max 60 caracteres, mots-cles importants en premier, jamais de majuscules inutiles
- Description : 200-300 mots, points forts, état précis, caractéristiques, usage idéal
- Intégrer les DETAILS SUPPLEMENTAIRES naturellement dans la description
- Si ETAT contient des défauts, les mentionner honnêtement dans la description

Reponds UNIQUEMENT avec ce format exact, sans asterisques ni markdown :

TITRE: [titre optimise SEO]
DESCRIPTION:
[description complète]
FIN_DESCRIPTION
MOTS_CLES: [15 mots-cles separes par virgules]"""


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

    # ── SCORE OPPORTUNITE /10 ─────────────────────────────
    score = 5.0
    # Demande
    if demande.upper() == "FORTE":   score += 2.0
    elif demande.upper() == "MOYENNE": score += 1.0
    # Vitesse de vente
    if vitesse.upper() == "RAPIDE":  score += 1.5
    elif vitesse.upper() == "NORMALE": score += 0.5
    # Marge potentielle
    marge_potentielle = prix_rev - achat_max
    if marge_potentielle >= achat_max:  score += 1.5  # marge > 100%
    elif marge_potentielle >= achat_max * 0.5: score += 0.5
    # Envoi
    if envoi.upper() == "FACILE":    score += 0.5
    elif envoi.upper() == "DIFFICILE": score -= 1.0
    # Nombre d'annonces (liquidite)
    try:
        nb_int = int(str(nb).replace("+", "").strip())
        if nb_int >= 20: score += 0.5
        elif nb_int <= 3: score -= 0.5
    except: pass
    score = round(max(1.0, min(10.0, score)), 1)

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
        "score": score,
    }


async def generer_annonce(data: dict, etat: str = "", details: str = "") -> dict:
    market_context = (
        f"Marche : {data.get('demande','?')} demande, "
        f"prix moyen {data.get('prix_moyen',0)}€, "
        f"vitesse {data.get('vitesse','?')}"
    )
    r = await _retry(
        client.messages.create,
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": PROMPT_ANNONCE.format(
            objet=data["objet"],
            etat=etat or "Bon etat",
            details=details or "Aucun detail supplementaire",
            market_context=market_context
        )}]
    )
    raw = r.content[0].text if r.content else ""
    raw = re.sub(r'\*\*(.+?)\*\*', r'\1', raw)
    raw = re.sub(r'\*(.+?)\*', r'\1', raw)

    desc_m = re.search(r'DESCRIPTION:\s*\n(.*?)FIN_DESCRIPTION', raw, re.DOTALL)
    titre = _get(raw, "TITRE") or _get(raw, "TITRE_EBAY") or data["objet"]
    data["titre"] = titre
    data["titre_ebay"]   = titre
    data["titre_lbc"]    = titre
    data["titre_vinted"] = titre
    data["description"]  = desc_m.group(1).strip() if desc_m else data["caption"]
    data["mots_cles"]    = _get(raw, "MOTS_CLES") or ""
    data["categorie"]    = _get(raw, "CATEGORIE") or ""
    data["conseil"]      = _get(raw, "CONSEIL") or ""
    return data


def _score_emoji(score: float) -> str:
    if score >= 8.5: return "🔥"
    if score >= 7.0: return "✅"
    if score >= 5.0: return "🟡"
    return "🔴"

def _score_bar(score: float) -> str:
    filled = round(score)
    return "█" * filled + "░" * (10 - filled)


def formater_analyse(data: dict) -> str:
    score = data.get("score", 5.0)
    emoji = _score_emoji(score)
    bar = _score_bar(score)
    annonces = data["annonces"].replace("[V]", "✅").replace("[.]", "🔵")

    demande_icon = {"FORTE": "🔥", "MOYENNE": "📊", "FAIBLE": "📉"}.get(data["demande"].upper(), "📊")
    vitesse_icon = {"RAPIDE": "⚡", "NORMALE": "🕐", "LENTE": "🐢"}.get(data["vitesse"].upper(), "🕐")
    envoi_icon   = {"FACILE": "📦", "MOYEN": "🚚", "DIFFICILE": "⚠️"}.get(data["envoi"].upper(), "🚚")

    return (
        f"📦 {data['objet'].upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{emoji} SCORE OPPORTUNITE : {score}/10\n"
        f"{bar}\n\n"
        f"💶 PRIX DU MARCHE\n"
        f"  Bas    : {data['prix_bas']}€\n"
        f"  Moyen  : {data['prix_moyen']}€\n"
        f"  Haut   : {data['prix_haut']}€\n\n"
        f"📊 MARCHE ({data['nb_annonces']} annonces)\n"
        f"  {demande_icon} Demande : {data['demande']}\n"
        f"  {vitesse_icon} Vitesse : {data['vitesse']}\n"
        + (f"  {data['raison']}\n" if data.get("raison") else "") +
        f"\n🚚 LOGISTIQUE\n"
        f"  {envoi_icon} Envoi : {data['envoi']}\n"
        f"  Poids : {data['poids']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Prix revente conseille : {data['prix_revente']}€\n"
        f"🛒 Prix achat maximum     : {data['achat_max']}€ ({data['label']})\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )


def _palier_pour_achat(prix_achat: float):
    """Retourne (mult, label, prix_revente_min) pour un prix d'achat donné."""
    for (amin, amax, mult, label) in PALIERS:
        if amin <= prix_achat < amax:
            return mult, label, round(prix_achat * mult, 2)
    return 1.4, "x1.4", round(prix_achat * 1.4, 2)


def formater_rentabilite(data: dict, prix_achat: float, quantite: int = 1, prix_total: float = None) -> str:
    prix_rev   = data.get("prix_revente", 0)
    achat_max  = data.get("achat_max", 0)
    mult, label, revente_min = _palier_pour_achat(prix_achat)
    marge_u    = round(prix_rev - prix_achat, 2)
    marge_pct  = round(marge_u / prix_achat * 100) if prix_achat > 0 else 0
    marge_tot  = round(marge_u * quantite, 2)
    ok_seuil   = (achat_max == 0) or (prix_achat <= achat_max)
    ok_coeff   = prix_rev >= revente_min

    if ok_seuil and ok_coeff:
        statut = "✅ BON ACHAT"
        detail = f"Seuil respecte ({label} = {revente_min}€ min)"
    elif not ok_seuil:
        dep = round((prix_achat - achat_max) / achat_max * 100) if achat_max > 0 else 0
        statut = "⚠️ AU-DESSUS DU SEUIL"
        detail = f"Achat max conseille : {achat_max}€/u (+{dep}% de trop)"
    else:
        manque = round(revente_min - prix_rev, 2)
        statut = "⚠️ MARGE INSUFFISANTE"
        detail = f"Revente min ({label}) : {revente_min}€ — manque {manque}€"

    lines = [
        f"📊 RENTABILITE",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"💶 Prix achat      : {prix_achat}€/u",
    ]
    if quantite > 1 and prix_total:
        lines.append(f"   Quantite        : {quantite} × {prix_achat}€ = {prix_total}€")
    lines += [
        f"💡 Revente estimee : {prix_rev}€/u",
        f"📈 Marge/unite     : +{marge_u}€ (+{marge_pct}%)",
    ]
    if quantite > 1:
        lines.append(f"📈 Marge totale    : +{marge_tot}€")
    lines += [
        f"",
        f"🔢 Coeff applique  : {label} (achat {prix_achat}€)",
        f"   Revente min     : {revente_min}€",
        f"",
        f"{statut}",
        f"{detail}",
        f"━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def formater_annonce(data: dict) -> str:
    titre = data.get("titre") or data.get("titre_ebay") or data.get("objet", "")
    prix = data.get("prix_revente", 0)
    conseil_line = ""  # CONSEIL supprimé
    return (
        f"ANNONCE GENEREE\n"
        f"{'='*35}\n\n"
        f"TITRE :\n{titre}\n\n"
        f"PRIX : {prix} euros\n\n"
        f"DESCRIPTION\n{data.get('description', '')}\n\n"
        f"MOTS-CLES\n{data.get('mots_cles', '')}"
        f"{conseil_line}"
    )


async def get_next_numero_global() -> int:
    """Retourne le prochain numéro global (toutes refs confondues)."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={
                    "fields[]": ["Référence gestion"],
                    "maxRecords": 1000
                }
            )
        records = resp.json().get("records", [])
        nums = []
        for r in records:
            ref = r.get("fields", {}).get("Référence gestion", "")
            m = re.search(r'AV-\d{8}-(\d+)', ref)
            if m:
                nums.append(int(m.group(1)))
        return max(nums) + 1 if nums else 1
    except Exception as e:
        logger.warning(f"get_next_numero_global error: {e}")
        return 1


async def generer_ref() -> str:
    """Génère une référence unique AV-YYYYMMDD-NNNN."""
    date = datetime.now().strftime("%Y%m%d")
    n = await get_next_numero_global()
    return f"AV-{date}-{str(n).zfill(4)}"


async def archiver(data: dict, ref: str, prix_achat_total: float, source: str,
                   quantite: int = 1) -> list:
    """
    Nouvelle architecture : crée UNE ligne par unité dans Airtable SANS annonce.
    L'annonce sera générée séparément via /listing dans Post&Sell.
    Notifie General (Inventory) à la création.
    """
    try:
        prix_unitaire = round(prix_achat_total / quantite, 4) if quantite > 0 else prix_achat_total
        description_objet = data.get("caption") or data.get("objet", "")
        objet_nom = data.get("objet", description_objet)
        date_achat = datetime.now().strftime("%Y-%m-%d")
        source_val = source or "Non renseigne"
        prix_vente_estime = round(float(data.get("prix_revente", 0)), 2)

        num_debut = await get_next_numero_global()
        date_str = datetime.now().strftime("%Y%m%d")

        refs_creees = []
        async with httpx.AsyncClient(timeout=30) as http:
            for i in range(quantite):
                ref_i = f"AV-{date_str}-{str(num_debut + i).zfill(4)}"
                fields = {
                    "Référence": ref_i,
                    "Référence gestion": ref_i,
                    "Description": description_objet,
                    "Prix achat total": round(float(prix_achat_total), 2) if i == 0 else 0,
                    "Prix achat unitaire": round(float(prix_unitaire), 4),
                    "Prix vente": prix_vente_estime,
                    "Source": source_val,
                    "Statut": "en stockage",
                    "Date achat": date_achat,
                    "Quantite totale": 1,
                    "Quantite vendue": 0,
                }
                resp = await http.post(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                    headers=HEADERS_AT,
                    json={"fields": fields}
                )
                if resp.status_code not in (200, 201):
                    logger.error(f"Airtable error ligne {i+1}: {resp.status_code} {resp.text[:300]}")
                else:
                    refs_creees.append(ref_i)
                    logger.info(f"✅ Airtable créé : {ref_i}")

        # Notifier General (Inventory) — fiche produit créée
        if refs_creees:
            try:
                from modules.routing import send_to_topic
                prix_u = round(prix_achat_total / quantite, 2) if quantite else prix_achat_total
                score = data.get("score", "?")
                msg = (
                    f"📦 NOUVEAU STOCK\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏷 {objet_nom}\n"
                    f"🔖 Ref : {refs_creees[0]}"
                    + (f" → {refs_creees[-1]}" if len(refs_creees) > 1 else "") +
                    f"\n💶 Achat : {prix_u}€/u × {quantite}"
                    + (f" = {prix_achat_total}€ total" if quantite > 1 else "") +
                    f"\n💡 Revente estimée : {prix_vente_estime}€\n"
                    f"📊 Score : {score}/10\n"
                    f"📍 Statut : En stockage\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"➡️ /listing {refs_creees[0]} dans Post&Sell pour générer l'annonce"
                )
                await send_to_topic("general", msg)
            except Exception as e:
                logger.warning(f"notify inventory skipped: {e}")

        return refs_creees
    except Exception as e:
        logger.error(f"archiver error: {e}", exc_info=True)
        return []
