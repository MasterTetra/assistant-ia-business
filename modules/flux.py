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

PROMPT_STRUCT = """Tu es un expert en achat-revente d'occasion. Analyse cet objet précisément.

OBJET A ANALYSER : {objet}
DESCRIPTION COMPLETE : {caption}
DONNEES MARCHE TROUVEES : {market_data}

REGLES STRICTES :
- Utilise UNIQUEMENT les données de marché fournies ci-dessus pour les prix et le nombre d'annonces
- Si les données sont insuffisantes, indique NB_ANNONCES: 0 et estime prudemment
- NB_ANNONCES doit refléter le nombre REEL d'annonces trouvées dans DONNEES MARCHE, pas une estimation
- PRIX_BAS, PRIX_MOYEN, PRIX_HAUT doivent être extraits des vrais prix trouvés, jamais inventés
- PRIX_REVENTE = prix de vente réaliste basé sur les ventes CONCLUES, pas les annonces en cours
- Si peu de données : être conservateur sur DEMANDE et VITESSE

Reponds UNIQUEMENT avec ce format exact, sans asterisques, sans markdown :

OBJET: [nom précis incluant marque et modèle si identifiables]
ANNONCES:
[plateforme | prix euros | VENDU ou EN VENTE | etat]
PRIX_BAS: [chiffre — jamais 0]
PRIX_MOYEN: [chiffre — jamais 0]
PRIX_HAUT: [chiffre — jamais 0]
PRIX_REVENTE: [prix réaliste basé sur ventes conclues — jamais 0]
NB_ANNONCES: [nombre exact trouvé dans les données]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
VITESSE: [RAPIDE ou NORMALE ou LENTE]
POIDS: [grammes estimés]
DIMENSIONS: [{dimensions}]
ENCOMBREMENT: [PETIT ou MOYEN ou GRAND]
ENVOI: [FACILE ou MOYEN ou DIFFICILE]
RAISON: [phrase courte factuelle basée sur les données trouvées]"""

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
    """Retry avec backoff exponentiel — gère 429 (rate limit) ET 529 (overloaded)."""
    max_attempts = 6
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err = str(e)
            is_429 = "429" in err
            is_529 = "529" in err or "overloaded" in err.lower()
            if (is_429 or is_529) and attempt < max_attempts - 1:
                wait = (2 ** attempt) * 5  # 5s, 10s, 20s, 40s, 80s
                logger.info(f"API {'surchargée' if is_529 else 'rate limit'} — retry {attempt+1}/{max_attempts-1} dans {wait}s")
                await asyncio.sleep(wait)
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
                    f'Recherche EXACTEMENT cet objet sur eBay.fr : "{objet_court}"\n'
                    f'Description complète : {caption[:200] if caption else objet_court}\n'
                    f'Fais ces 3 recherches :\n'
                    f'1) "{objet_court} site:ebay.fr" — annonces actives\n'
                    f'2) "{objet_court} ebay.fr vendu" — ventes conclues récentes\n'
                    f'3) "{objet_court} occasion prix" — marché occasion\n'
                    f'IMPORTANT : cherche des articles IDENTIQUES ou très similaires (même marque, même modèle, même état si possible).\n'
                    f'Donne-moi :\n'
                    f'- Liste des 5-10 annonces les plus proches avec prix exact et statut (vendu/en vente)\n'
                    f'- Prix bas / moyen / haut UNIQUEMENT basé sur les vrais prix trouvés\n'
                    f'- Nombre exact d\'annonces similaires trouvées\n'
                    f'Texte court et factuel uniquement.'
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
    # ── SCORING /10 — 5 critères pondérés ────────────────────────────
    score = 0.0

    # 1. DEMANDE (0-2.5 pts) — indicateur de liquidité du marché
    demande_up = demande.upper()
    if demande_up == "FORTE":   score += 2.5
    elif demande_up == "MOYENNE": score += 1.5
    else:                         score += 0.5  # FAIBLE

    # 2. VITESSE DE VENTE (0-2 pts)
    vitesse_up = vitesse.upper()
    if vitesse_up == "RAPIDE":  score += 2.0
    elif vitesse_up == "NORMALE": score += 1.0
    else:                         score += 0.0  # LENTE

    # 3. MARGE POTENTIELLE (0-3 pts) — critère principal
    marge_potentielle = prix_rev - achat_max
    taux_marge = (marge_potentielle / achat_max) if achat_max > 0 else 0
    if taux_marge >= 2.0:    score += 3.0   # marge > 200% (x3)
    elif taux_marge >= 1.5:  score += 2.5   # marge > 150%
    elif taux_marge >= 1.0:  score += 2.0   # marge > 100% (x2)
    elif taux_marge >= 0.5:  score += 1.0   # marge > 50%
    elif taux_marge > 0:     score += 0.5
    else:                    score += 0.0   # marge nulle ou négative

    # 4. FACILITÉ D'ENVOI (0-1.5 pts)
    envoi_up = envoi.upper()
    if envoi_up == "FACILE":    score += 1.5
    elif envoi_up == "MOYEN":   score += 0.75
    else:                       score -= 0.5  # DIFFICILE = pénalité

    # 5. LIQUIDITÉ MARCHÉ — nb annonces (0-1 pt)
    try:
        nb_int = int(str(nb).replace("+", "").strip())
        if 5 <= nb_int <= 30:   score += 1.0   # marché actif mais pas saturé
        elif nb_int > 30:       score += 0.5   # marché saturé
        elif nb_int >= 1:       score += 0.75  # peu d'annonces = moins de concurrence
        else:                   score += 0.0
    except:
        score += 0.5  # données insuffisantes = neutre

    score = round(max(1.0, min(10.0, score)), 1)

    # ── Prix max d'achat rentable recalculé plus précisément ──────
    # achat_max = prix_revente / multiplicateur (déjà calculé) mais on l'affine
    # En tenant compte des frais plateforme (~13% eBay)
    frais_pf_estimes = prix_rev * 0.13
    achat_max_net = (prix_rev - frais_pf_estimes) / mult if mult > 0 else achat_max

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
        "achat_max_net": round(achat_max_net, 2),
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




# ── RECHERCHE TEXTE LIBRE ──────────────────────────────────────────────────────
PROMPT_RECHERCHE_TEXTE = """Tu es un expert en achat-revente d'occasion. Analyse UNIQUEMENT cet article précis.

ARTICLE : {query}

INSTRUCTIONS STRICTES :
1. Recherche "{query} site:ebay.fr" → annonces ACTIVES seulement (pas vendues)
2. Recherche "{query} ebay.fr vendu" → ventes conclues des 90 derniers jours
3. Recherche "{query} site:leboncoin.fr" → annonces LBC ACTIVES
4. Recherche "{query} site:vinted.fr" → annonces Vinted ACTIVES

RÈGLES ABSOLUES :
- NE PAS mélanger plusieurs articles différents — analyse UNIQUEMENT "{query}"
- Liens = URLs complètes d'annonces ENCORE EN VENTE (pas vendues, pas expirées)
- Prix = prix réels trouvés uniquement, jamais inventés
- Fourchette = exclure les outliers aberrants (garder 10e-90e percentile des prix trouvés)
- Si article rare : préciser clairement le peu de données disponibles
- Vérifier que les annonces correspondent EXACTEMENT à la recherche (même édition, même langue, même modèle)

Format STRICT (sans markdown, sans astérisques) :

OBJET: [nom précis et complet de l'article trouvé]
ANNONCES:
[plateforme | prix en euros | EN VENTE | état | URL complète]
[plateforme | prix en euros | VENDU le JJ/MM | état | URL si disponible]
PRIX_BAS: [prix plancher sur articles identiques EN VENTE — exclure outliers]
PRIX_MOYEN: [médiane des prix EN VENTE]
PRIX_HAUT: [prix plafond raisonnable — exclure outliers]
PRIX_REVENTE: [prix de vente réaliste basé sur ventes CONCLUES récentes]
NB_ANNONCES: [nombre d'annonces EN VENTE trouvées]
NB_VENDUS: [nombre de ventes conclues trouvées]
DEMANDE: [FORTE si >20 ventes récentes / MOYENNE 5-20 / FAIBLE <5]
VITESSE: [RAPIDE si vendu en <7j / NORMALE 7-30j / LENTE >30j]
RAISON: [analyse factuelle basée uniquement sur les données trouvées]
CONSEIL: [prix max d'achat conseillé pour être rentable à la revente]"""


async def recherche_texte(query: str) -> dict:
    """
    Analyse marché à partir d'une requête texte libre (sans photo).
    Retourne un dict avec données marché + liens annonces.
    """
    try:
        r1 = await _retry(
            client.messages.create,
            model=CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": [{
                "type": "text",
                "text": PROMPT_RECHERCHE_TEXTE.format(query=query)
            }]}]
        )
        raw = ""
        for block in r1.content:
            if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
                raw += block.text + "\n"
        logger.info(f"RECHERCHE TEXTE [{query[:40]}]:\n{raw[:600]}")
    except Exception as e:
        logger.warning(f"Recherche texte failed [{query}]: {e}")
        raw = ""

    # Si échec total : retourner un dict d'erreur explicite
    if not raw and search_error:
        err_msg = {
            "surcharge": "⏳ API surchargée — réessaie dans 1-2 minutes",
            "rate_limit": "⏳ Trop de requêtes — réessaie dans 1 minute",
            "erreur": "⚠️ Erreur technique lors de la recherche",
        }.get(search_error, "⚠️ Recherche échouée")
        return {
            "query": query, "objet": query, "erreur": err_msg,
            "annonces": [], "nb_annonces": "0", "nb_vendus": "0",
            "prix_bas": 0, "prix_moyen": 0, "prix_haut": 0,
            "prix_revente": 0, "achat_max": 0, "achat_max_net": 0,
            "mult": 3, "label": "x3", "demande": "?", "vitesse": "?",
            "raison": err_msg, "conseil": "", "score": 0, "raw": "",
        }

    # Parser le résultat
    objet_id  = _get(raw, "OBJET") or query
    prix_bas  = _num(raw, "PRIX_BAS")
    prix_moy  = _num(raw, "PRIX_MOYEN")
    prix_haut = _num(raw, "PRIX_HAUT")
    prix_rev  = _num(raw, "PRIX_REVENTE") or prix_moy or prix_bas
    nb        = _get(raw, "NB_ANNONCES") or "?"
    nb_vendus = _get(raw, "NB_VENDUS") or "?"
    demande   = _get(raw, "DEMANDE") or "MOYENNE"
    vitesse   = _get(raw, "VITESSE") or "NORMALE"
    raison    = _get(raw, "RAISON") or ""
    conseil   = _get(raw, "CONSEIL") or ""
    annonces  = _annonces_avec_liens(raw)

    # Score
    achat_max, mult, label = _calcul_palier(prix_rev)
    frais_pf = prix_rev * 0.13
    achat_max_net = (prix_rev - frais_pf) / mult if mult > 0 else achat_max

    # Scoring identique à analyser_marche
    score = 0.0
    if demande.upper() == "FORTE":   score += 2.5
    elif demande.upper() == "MOYENNE": score += 1.5
    else: score += 0.5
    if vitesse.upper() == "RAPIDE":  score += 2.0
    elif vitesse.upper() == "NORMALE": score += 1.0
    taux_marge = ((prix_rev - achat_max) / achat_max) if achat_max > 0 else 0
    if taux_marge >= 2.0: score += 3.0
    elif taux_marge >= 1.5: score += 2.5
    elif taux_marge >= 1.0: score += 2.0
    elif taux_marge >= 0.5: score += 1.0
    elif taux_marge > 0: score += 0.5
    score = round(max(1.0, min(10.0, score)), 1)

    return {
        "query": query,
        "objet": objet_id,
        "annonces": annonces,
        "nb_annonces": nb,
        "nb_vendus": nb_vendus,
        "prix_bas": prix_bas,
        "prix_moyen": prix_moy,
        "prix_haut": prix_haut,
        "prix_revente": prix_rev,
        "achat_max": achat_max,
        "achat_max_net": round(achat_max_net, 2),
        "mult": mult,
        "label": label,
        "demande": demande,
        "vitesse": vitesse,
        "raison": raison,
        "conseil": conseil,
        "score": score,
        "raw": raw,
    }


async def recherche_multiple(queries: list) -> list:
    """
    Analyse chaque article SÉPARÉMENT — ne jamais grouper.
    Traitement séquentiel avec pause pour éviter rate limit Anthropic.
    """
    results = []
    for i, query in enumerate(queries):
        logger.info(f"Recherche {i+1}/{len(queries)}: {query}")
        try:
            data = await recherche_texte(query)
            results.append(data)
        except Exception as e:
            logger.error(f"Erreur recherche [{query}]: {e}")
            results.append({
                "query": query,
                "objet": query,
                "annonces": [],
                "nb_annonces": "0",
                "nb_vendus": "0",
                "prix_bas": 0, "prix_moyen": 0, "prix_haut": 0,
                "prix_revente": 0, "achat_max": 0, "achat_max_net": 0,
                "mult": 1, "label": "?",
                "demande": "INCONNUE", "vitesse": "INCONNUE",
                "raison": f"Erreur: {str(e)[:100]}",
                "conseil": "", "score": 0, "raw": ""
            })
        # Pause entre chaque article pour respecter le rate limit
        if i < len(queries) - 1:
            await asyncio.sleep(8)
    return results


def _annonces_avec_liens(text: str) -> list:
    """Parse les annonces incluant les liens URL."""
    annonces = []
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=\n[A-Z_]+:)', text, re.DOTALL | re.IGNORECASE)
    if not m:
        return annonces
    bloc = m.group(1).strip()
    for line in bloc.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 4:
            url = next((p for p in parts if p.startswith("http")), "")
            annonces.append({
                "plateforme": parts[0] if len(parts) > 0 else "",
                "prix": parts[1] if len(parts) > 1 else "",
                "statut": parts[2] if len(parts) > 2 else "",
                "etat": parts[3] if len(parts) > 3 else "",
                "url": url,
            })
    return annonces


def formater_recherche(data: dict) -> str:
    """Formate le résultat d'une recherche texte pour Telegram."""
    # Cas d'erreur
    if data.get("erreur"):
        return (
            f"🔍 *{data['query']}*\n"
            f"{'─'*30}\n"
            f"{data['erreur']}\n\n"
            f"_Utilise /recherche {data['query']} pour réessayer_"
        )

    score = data["score"]
    score_emoji = "🟢" if score >= 7 else ("🟡" if score >= 5 else "🔴")

    # Calculer la cohérence de la fourchette
    ecart = data["prix_haut"] - data["prix_bas"] if data["prix_haut"] and data["prix_bas"] else 0
    fourchette_ok = ecart > 0 and ecart < data["prix_moyen"] * 3 if data["prix_moyen"] else True

    lines = [
        f"🔍 *{data['objet']}*",
        f"{'─'*30}",
        f"{score_emoji} Score opportunité : *{score}/10*",
        "",
        "📊 *Analyse marché*",
        f"  • Fourchette marché : *{data['prix_bas']}€ → {data['prix_haut']}€*",
        f"  • Prix médian : *{data['prix_moyen']}€*",
        f"  • Prix revente réaliste : *{data['prix_revente']}€* _(basé sur ventes conclues)_",
        f"  • Annonces actives : {data['nb_annonces']} | Vendus récents : {data['nb_vendus']}",
        f"  • Demande : *{data['demande']}* | Rotation : *{data['vitesse']}*",
        "",
        "💰 *Rentabilité*",
        f"  • Achat max brut ({data['label']}) : *{data['achat_max']}€*",
        f"  • Achat max net (après ~13% frais) : *{data['achat_max_net']}€*",
    ]

    if data.get("raison"):
        lines += ["", f"📝 {data['raison']}"]

    if data.get("conseil"):
        lines += [f"💡 {data['conseil']}"]

    # Annonces avec liens
    annonces_vendues = [a for a in data["annonces"] if "VENDU" in a.get("statut", "").upper()]
    annonces_en_vente = [a for a in data["annonces"] if "EN VENTE" in a.get("statut", "").upper() or "VENTE" in a.get("statut", "").upper()]

    if annonces_vendues:
        lines += ["", "✅ *Ventes conclues*"]
        for a in annonces_vendues[:5]:
            url_part = f" — [lien]({a['url']})" if a.get("url") else ""
            lines.append(f"  • {a['plateforme']} | {a['prix']} | {a['etat']}{url_part}")

    if annonces_en_vente:
        lines += ["", "🏪 *En vente actuellement*"]
        for a in annonces_en_vente[:5]:
            url_part = f" — [lien]({a['url']})" if a.get("url") else ""
            lines.append(f"  • {a['plateforme']} | {a['prix']} | {a['etat']}{url_part}")

    return "\n".join(lines)


def formater_rapport_multiple(results: list) -> str:
    """Rapport consolidé pour plusieurs articles."""
    if not results:
        return "⚠️ Aucun résultat."

    lines = [
        f"🔍 *RAPPORT MARCHÉ — {len(results)} article(s)*",
        f"📅 {__import__('datetime').datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # Trier par score décroissant
    for i, d in enumerate(sorted(results, key=lambda x: x["score"], reverse=True), 1):
        score = d["score"]
        emoji = "🟢" if score >= 7 else ("🟡" if score >= 5 else "🔴")
        lines += [
            f"{emoji} *{i}. {d['objet']}*",
            f"   Score : {score}/10 | Prix revente : {d['prix_revente']}€ | Achat max net : {d['achat_max_net']}€",
            f"   Demande : {d['demande']} | Annonces : {d['nb_annonces']} | Vendus : {d['nb_vendus']}",
        ]
        if d.get("raison"):
            lines.append(f"   📝 {d['raison']}")
        lines.append("")

    # Meilleure opportunité
    best = max(results, key=lambda x: x["score"])
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🏆 *Meilleure opportunité : {best['objet']}*",
        f"   Score {best['score']}/10 — acheter max {best['achat_max_net']}€ net",
    ]

    return "\n".join(lines)


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
                    "Prix achat unitaire": round(float(prix_unitaire), 2),
                    "Prix vente": float(prix_vente_estime),
                    "Statut": "acheté",
                    "Date achat": date_achat,
                    "Quantite totale": 1,
                }
                # Prix achat total uniquement sur la première ligne
                if i == 0:
                    fields["Prix achat total"] = round(float(prix_achat_total), 2)
                # Source uniquement si fournie (champ select Airtable)
                if source_val and source_val != "Non renseigne":
                    fields["Source"] = source_val
                logger.info(f"📤 INSERT Airtable — fields: {fields}")
                resp = await http.post(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                    headers=HEADERS_AT,
                    json={"fields": fields}
                )
                logger.info(f"📥 Airtable réponse: {resp.status_code} | {resp.text[:600]}")
                if resp.status_code not in (200, 201):
                    logger.error(f"❌ Airtable ECHEC ligne {i+1}: {resp.status_code} | {resp.text[:600]}")
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
                    f"📍 Statut : Acheté\n"
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
