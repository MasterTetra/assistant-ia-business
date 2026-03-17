"""
MODULE EXPORT — Génère les données structurées pour Google Sheets via Make.com
Rapports : hebdo / mensuel / annuel
Veille réglementaire : analyse Claude + export structuré
"""
import httpx
import logging
import asyncio
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import anthropic
from config.settings import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS,
    WEBHOOK_SECRET
)

logger = logging.getLogger(__name__)
PARIS_TZ = ZoneInfo("Europe/Paris")
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# URL webhook Make.com dédié aux exports Google Sheets
# À configurer dans les variables Railway
import os
MAKE_WEBHOOK_SHEETS = os.getenv("MAKE_WEBHOOK_SHEETS", "")


# ── FETCH DONNÉES ─────────────────────────────────────────────────────────────

async def _fetch_periode(debut_str: str, fin_str: str) -> dict:
    """Récupère les données Airtable pour une période donnée."""
    fields = [
        "Référence gestion", "Description", "Statut",
        "Prix achat unitaire", "Prix achat total", "Quantite totale",
        "Prix vente", "Date achat", "Date vente",
        "Plateforme vente", "Frais plateforme", "Frais transport", "Source",
    ]
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={"fields[]": fields, "maxRecords": 500,
                        "sort[0][field]": "Date achat", "sort[0][direction]": "asc"}
            )
        records = [r["fields"] for r in resp.json().get("records", [])]
    except Exception as e:
        logger.error(f"_fetch_periode: {e}")
        return {}

    TVA_TAUX = 20 / 120
    IS_TAUX = 0.15

    achetes = [f for f in records if (f.get("Date achat") or "") >= debut_str
               and (f.get("Date achat") or "") <= fin_str
               and f.get("Statut") != "vendu"]

    vendus = [f for f in records if f.get("Statut") == "vendu"
              and (f.get("Date vente") or "") >= debut_str
              and (f.get("Date vente") or "") <= fin_str]

    # Capital investi (groupé par description pour éviter double-comptage lots)
    groupes_achat = {}
    for f in achetes:
        desc = f.get("Description") or "?"
        groupes_achat.setdefault(desc, []).append(f)
    capital = 0.0
    for groupe in groupes_achat.values():
        avec_total = [f for f in groupe if f.get("Prix achat total")]
        if avec_total:
            capital += sum(float(f["Prix achat total"]) for f in avec_total)
        else:
            capital += sum(float(f.get("Prix achat unitaire") or 0) for f in groupe)

    # Ventes
    ca = sum(float(f.get("Prix vente") or 0) for f in vendus)
    cout = sum(float(f.get("Prix achat unitaire") or 0) for f in vendus)
    frais_pf = sum(float(f.get("Frais plateforme") or 0) or
                   float(f.get("Prix vente") or 0) * 0.13 for f in vendus)
    frais_tr = sum(float(f.get("Frais transport") or 0) for f in vendus)
    marge_brute = ca - cout
    marge_apres_frais = marge_brute - frais_pf - frais_tr
    tva = max(0, marge_apres_frais) * TVA_TAUX
    is_estime = max(0, marge_apres_frais - tva) * IS_TAUX
    resultat_net = marge_apres_frais - tva - is_estime

    # Plateformes
    plateformes = {}
    for f in vendus:
        pf = f.get("Plateforme vente") or "Non renseigné"
        plateformes[pf] = plateformes.get(pf, 0) + float(f.get("Prix vente") or 0)
    pf_principale = max(plateformes, key=plateformes.get) if plateformes else "—"

    # Sources
    sources = {}
    for f in achetes:
        src = f.get("Source") or "Non renseigné"
        sources[src] = sources.get(src, 0) + 1
    src_principale = max(sources, key=sources.get) if sources else "—"

    return {
        "periode_debut": debut_str,
        "periode_fin": fin_str,
        "nb_achats": len(achetes),
        "capital_investi": round(capital, 2),
        "nb_ventes": len(vendus),
        "ca": round(ca, 2),
        "cout_achats": round(cout, 2),
        "frais_plateformes": round(frais_pf, 2),
        "frais_transport": round(frais_tr, 2),
        "marge_brute": round(marge_brute, 2),
        "marge_apres_frais": round(marge_apres_frais, 2),
        "tva_marge": round(tva, 2),
        "is_estime": round(is_estime, 2),
        "resultat_net": round(resultat_net, 2),
        "taux_marge_net": round(resultat_net / ca * 100, 1) if ca > 0 else 0,
        "plateforme_principale": pf_principale,
        "source_principale": src_principale,
        "plateformes_detail": plateformes,
        "export_date": datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
    }


# ── ENVOI VERS MAKE.COM ───────────────────────────────────────────────────────

async def _envoyer_make(payload: dict) -> bool:
    """Envoie les données au webhook Make.com pour écriture dans Google Sheets."""
    if not MAKE_WEBHOOK_SHEETS:
        logger.warning("MAKE_WEBHOOK_SHEETS non configuré — export ignoré")
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(MAKE_WEBHOOK_SHEETS, json=payload)
        if resp.status_code in (200, 201, 204):
            logger.info(f"✅ Export Google Sheets envoyé: {payload.get('event')}")
            return True
        logger.warning(f"Make.com réponse {resp.status_code}: {resp.text[:100]}")
        return False
    except Exception as e:
        logger.error(f"_envoyer_make: {e}")
        return False


# ── EXPORTS RAPPORTS ──────────────────────────────────────────────────────────

async def exporter_rapport_hebdo() -> bool:
    now = datetime.now(PARIS_TZ)
    debut = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    fin = now.strftime("%Y-%m-%d")
    data = await _fetch_periode(debut, fin)
    if not data:
        return False
    data["semaine"] = f"Semaine {now.isocalendar()[1]} — {now.year}"
    return await _envoyer_make({
        "secret": WEBHOOK_SECRET,
        "event": "rapport_export",
        "type": "hebdo",
        "onglet": "Hebdomadaire",
        "data": data,
    })


async def exporter_rapport_mensuel() -> bool:
    now = datetime.now(PARIS_TZ)
    debut = now.replace(day=1).strftime("%Y-%m-%d")
    fin = now.strftime("%Y-%m-%d")
    data = await _fetch_periode(debut, fin)
    if not data:
        return False
    MOIS_FR = {1:"Janvier",2:"Février",3:"Mars",4:"Avril",5:"Mai",6:"Juin",
               7:"Juillet",8:"Août",9:"Septembre",10:"Octobre",11:"Novembre",12:"Décembre"}
    data["mois"] = f"{MOIS_FR[now.month]} {now.year}"
    return await _envoyer_make({
        "secret": WEBHOOK_SECRET,
        "event": "rapport_export",
        "type": "mensuel",
        "onglet": "Mensuel",
        "data": data,
    })


async def exporter_rapport_annuel() -> bool:
    now = datetime.now(PARIS_TZ)
    debut = now.replace(month=1, day=1).strftime("%Y-%m-%d")
    fin = now.strftime("%Y-%m-%d")
    data = await _fetch_periode(debut, fin)
    if not data:
        return False
    data["annee"] = str(now.year)
    return await _envoyer_make({
        "secret": WEBHOOK_SECRET,
        "event": "rapport_export",
        "type": "annuel",
        "onglet": "Annuel",
        "data": data,
    })


# ── VEILLE RÉGLEMENTAIRE ──────────────────────────────────────────────────────

PROMPT_VEILLE = """Tu es un expert juridique et fiscal spécialisé dans les SAS françaises
de commerce de biens d'occasion. Effectue une veille réglementaire actualisée.

Recherche les dernières évolutions sur :
1. TVA sur marge (Art.297A CGI) — changements, jurisprudence
2. Obligations déclaratives plateformes (DAC7, Directive UE)
3. Seuils IS pour les PME/SAS
4. Réglementation e-commerce et marketplaces en France
5. Obligations légales revente occasion (TRACFIN, registre police si applicable)
6. CFE et cotisations sociales dirigeant SAS
7. Nouveautés fiscales applicables aux SAS en {annee}

Pour CHAQUE point trouvé, réponds avec ce format JSON strict (tableau) :
[
  {{
    "date": "MM/YYYY",
    "type": "TVA|IS|LEGAL|PLATEFORME|SOCIAL|AUTRE",
    "sujet": "titre court",
    "resume": "description en 2 phrases max",
    "impact": "HIGH|MEDIUM|LOW",
    "action": "action concrète à prendre",
    "lien": "URL source si disponible, sinon vide"
  }}
]

Retourne UNIQUEMENT le JSON, sans texte avant ou après."""


async def generer_veille_reglementaire() -> tuple:
    """
    Génère la veille réglementaire via Claude avec web search.
    Retourne (items, message_telegram).
    """
    now = datetime.now(PARIS_TZ)
    try:
        r = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content":
                PROMPT_VEILLE.format(annee=now.year)}]
        )
        raw = ""
        for block in r.content:
            if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
                raw += block.text

        import json as _json, re as _re
        # Extraire le JSON
        m = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if m:
            items = _json.loads(m.group(0))
        else:
            items = []

    except Exception as e:
        logger.error(f"generer_veille_reglementaire: {e}")
        items = []

    # Envoyer vers Make.com → Google Sheets
    if items:
        await _envoyer_make({
            "secret": WEBHOOK_SECRET,
            "event": "veille_reglementaire",
            "date_veille": now.strftime("%d/%m/%Y"),
            "items": items,
        })

    # Construire message Telegram
    if not items:
        msg_tg = "📋 *Veille réglementaire* — Aucune nouveauté détectée cette semaine."
    else:
        high = [i for i in items if i.get("impact") == "HIGH"]
        lines = [f"📋 *VEILLE RÉGLEMENTAIRE* — {now.strftime('%d/%m/%Y')}",
                 f"_{len(items)} point(s) analysé(s)_\n"]
        if high:
            lines.append("🔴 *À TRAITER EN PRIORITÉ :*")
            for i in high:
                lines.append(f"  • *{i['sujet']}*")
                lines.append(f"    _{i['resume']}_")
                if i.get("action"):
                    lines.append(f"    ✅ {i['action']}")
        lines.append("\n_Détail complet → Google Sheet Veille Réglementaire_")
        msg_tg = "\n".join(lines)

    return items, msg_tg


# ── SYSTÈME VEILLE INTELLIGENTE ───────────────────────────────────────────────
# Table Airtable "Veille" — colonnes attendues :
# Date | Type | Sujet | Résumé | Source | Impact | Applicable | Action | Statut | Score

AIRTABLE_TABLE_VEILLE = os.getenv("AIRTABLE_TABLE_VEILLE", "Veille")

PROMPT_VEILLE_ANALYSE = """
Tu es un système d'analyse et de veille business pour une SAS française de revente d'objets d'occasion.

CONTEXTE BUSINESS :
- Activité : achat-revente d'objets d'occasion (brocantes, vide-greniers, enchères)
- Plateformes : eBay, LeBonCoin, Vinted, Etsy
- Structure : SAS, régime TVA sur marge (Art.297A CGI)
- Outils : Telegram bot, Airtable, Make.com, Railway

MISSION CETTE SEMAINE :

PARTIE 1 — ANALYSE PERFORMANCES :
{stats_business}

Pour ces données, identifie :
- Points positifs à renforcer
- Problèmes à corriger
- 3 actions prioritaires avec impact chiffré

PARTIE 2 — VEILLE EXISTANTE À ANALYSER :
{veille_existante}

Pour chaque entrée :
- Est-ce toujours valide/pertinent ?
- A-t-on progressé sur ce sujet ?
- Faut-il mettre à jour le statut ?

PARTIE 3 — NOUVELLES OPPORTUNITÉS :
Recherche des informations récentes sur :
1. Outils d'automatisation pour la revente (eBay, Vinted, LBC)
2. Optimisations fiscales SAS / TVA marge
3. Tendances marché occasion en France
4. Nouvelles fonctionnalités des plateformes de vente
5. Outils IA applicables à la gestion de stock

FILTRE ABSOLU — n'inclure QUE si lien direct avec :
revente / logistique / automatisation / fiscalité / optimisation business

SCORING (0-10) pour chaque info :
score = (impact × pertinence × facilité_mise_en_place) / 3
→ Ne garder que score ≥ 6

FORMAT DE RÉPONSE :

## PERFORMANCES
[analyse courte avec 3 actions prioritaires]

## VEILLE MISE À JOUR
[liste des entrées à modifier avec nouveau statut]

## NOUVELLES OPPORTUNITÉS
[liste JSON]
[{"date": "MM/YYYY", "type": "outil|fiscalite|strategie|automatisation", "sujet": "...", 
  "resume": "...", "source": "...", "impact": "HIGH|MEDIUM|LOW", 
  "applicable": "OUI|NON|PARTIEL", "action": "...", "statut": "à tester", "score": 8.5}]
"""

PROMPT_VEILLE_DEEP = """
Tu es un expert en optimisation business pour la revente d'occasion.
Effectue une analyse APPROFONDIE avec web search sur les thèmes suivants.

Pour chaque thème, cherche les 2-3 meilleures ressources/outils/opportunités :
1. Automatisation eBay (listing, pricing, fulfillment)
2. Outils de sourcing automatisé (alertes, scraping légal)
3. Fiscalité optimisée SAS revente occasion 2025
4. Plateformes émergentes revente en France
5. IA appliquée à la gestion de stock d'occasion

Filtre : pertinence directe avec l'activité revente uniquement.
Score chaque item (impact × pertinence × facilité / 3).
Ne retenir que score ≥ 7.

Format JSON identique au prompt standard.
"""


async def _fetch_veille_existante() -> list:
    """Lit les entrées de la table Veille dans Airtable."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{AIRTABLE_TABLE_VEILLE}",
                headers=HEADERS_AT,
                params={
                    "fields[]": ["Date", "Type", "Sujet", "Résumé", "Impact",
                                 "Applicable", "Action", "Statut", "Score"],
                    "maxRecords": 50,
                    "sort[0][field]": "Date",
                    "sort[0][direction]": "desc",
                }
            )
        if resp.status_code == 404:
            logger.info("Table Veille non trouvée dans Airtable — à créer")
            return []
        return [r["fields"] for r in resp.json().get("records", [])]
    except Exception as e:
        logger.error(f"_fetch_veille_existante: {e}")
        return []


async def _ajouter_veille_airtable(items: list) -> int:
    """Ajoute de nouvelles entrées dans la table Veille Airtable."""
    ok = 0
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            for item in items:
                fields = {
                    "Date":       item.get("date", ""),
                    "Type":       item.get("type", ""),
                    "Sujet":      item.get("sujet", "")[:100],
                    "Résumé":     item.get("resume", "")[:500],
                    "Source":     item.get("source", ""),
                    "Impact":     item.get("impact", "MEDIUM"),
                    "Applicable": item.get("applicable", ""),
                    "Action":     item.get("action", ""),
                    "Statut":     item.get("statut", "à tester"),
                    "Score":      float(item.get("score", 5)),
                }
                resp = await http.post(
                    f"{AIRTABLE_URL}/{AIRTABLE_TABLE_VEILLE}",
                    headers={**HEADERS_AT, "Content-Type": "application/json"},
                    json={"fields": fields}
                )
                if resp.status_code in (200, 201):
                    ok += 1
    except Exception as e:
        logger.error(f"_ajouter_veille_airtable: {e}")
    return ok


async def generer_rapport_hebdo_complet() -> dict:
    """
    Génère le rapport hebdomadaire complet :
    - Stats business de la semaine
    - Analyse IA des performances
    - Veille mise à jour
    - Nouvelles opportunités
    Retourne dict avec message Telegram + données pour Sheets
    """
    now = datetime.now(PARIS_TZ)
    debut = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    fin = now.strftime("%Y-%m-%d")

    # 1. Données business
    stats = await _fetch_periode(debut, fin)

    # 2. Veille existante
    veille = await _fetch_veille_existante()
    veille_resume = "\n".join([
        f"- [{v.get('Statut','?')}] {v.get('Sujet','?')} ({v.get('Impact','?')})"
        for v in veille[:10]
    ]) or "Aucune entrée existante"

    # 3. Stats résumé pour le prompt
    stats_txt = f"""
CA semaine : {stats.get('ca', 0)}€
Profit net : {stats.get('resultat_net', 0)}€
Nb ventes : {stats.get('nb_ventes', 0)}
Nb achats : {stats.get('nb_achats', 0)}
Capital immobilisé : {stats.get('capital_investi', 0)}€
Plateforme principale : {stats.get('plateforme_principale', '—')}
Taux marge net : {stats.get('taux_marge_net', 0)}%
"""

    # 4. Analyse IA
    try:
        r = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content":
                PROMPT_VEILLE_ANALYSE.format(
                    stats_business=stats_txt,
                    veille_existante=veille_resume
                )
            }]
        )
        analyse_raw = "".join(
            b.text for b in r.content
            if getattr(b, "type", "") == "text" and getattr(b, "text", "")
        )
    except Exception as e:
        logger.error(f"generer_rapport_hebdo_complet IA: {e}")
        analyse_raw = "⚠️ Analyse IA indisponible"

    # 5. Extraire nouvelles opportunités et les ajouter dans Airtable
    import json as _json, re as _re
    nb_ajouts = 0
    m = _re.search(r'\[.*\]', analyse_raw, _re.DOTALL)
    if m:
        try:
            nouveaux = _json.loads(m.group(0))
            # Filtrer score >= 6
            filtres = [i for i in nouveaux if float(i.get("score", 0)) >= 6]
            if filtres:
                nb_ajouts = await _ajouter_veille_airtable(filtres)
                # Export vers Google Sheets veille
                await _envoyer_make({
                    "secret": WEBHOOK_SECRET,
                    "event": "veille_update",
                    "date": now.strftime("%d/%m/%Y"),
                    "items": filtres,
                })
        except Exception:
            pass

    # 6. Construire message Telegram
    roi = stats.get('taux_marge_net', 0)
    roi_emoji = "🟢" if roi >= 40 else ("🟡" if roi >= 20 else "🔴")

    # Extraire section performances du texte IA
    perf_match = _re.search(r'## PERFORMANCES\n(.*?)(?=## |$)', analyse_raw, _re.DOTALL)
    perf_txt = perf_match.group(1).strip()[:600] if perf_match else ""

    msg_tg = (
        f"📊 *RAPPORT HEBDOMADAIRE*\n"
        f"📅 Semaine du {(now - timedelta(days=7)).strftime('%d/%m')} au {now.strftime('%d/%m/%Y')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💶 CA : *{stats.get('ca', 0):.2f}€*\n"
        f"💰 Profit net : *{stats.get('resultat_net', 0):.2f}€*\n"
        f"{roi_emoji} ROI net : *{roi}%*\n"
        f"📦 Ventes : {stats.get('nb_ventes', 0)} | Achats : {stats.get('nb_achats', 0)}\n"
        f"🏪 Plateforme : {stats.get('plateforme_principale', '—')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
    )

    if perf_txt:
        msg_tg += f"🧠 *Analyse :*\n{perf_txt}\n━━━━━━━━━━━━━━━━━━━━\n"

    if nb_ajouts:
        msg_tg += f"🔍 *Veille :* {nb_ajouts} nouvelle(s) opportunité(s) ajoutée(s)\n"

    msg_tg += f"_Détail complet → Google Sheets_"

    # 7. Export rapport vers Sheets
    await exporter_rapport_hebdo()

    return {
        "message_telegram": msg_tg,
        "stats": stats,
        "analyse": analyse_raw,
        "nb_veille_ajouts": nb_ajouts,
    }


async def analyser_veille_manuelle() -> str:
    """
    Lit la table Veille Airtable, analyse les entrées manuelles non encore traitées
    et complète les champs manquants via IA.
    """
    veille = await _fetch_veille_existante()
    a_analyser = [v for v in veille if v.get("Statut") in ("à tester", "") or not v.get("Action")]

    if not a_analyser:
        return "✅ Toutes les entrées de veille sont à jour."

    items_txt = "\n".join([
        f"- {v.get('Sujet', '?')} (Type: {v.get('Type', '?')}, Statut: {v.get('Statut', '?')})"
        for v in a_analyser[:10]
    ])

    prompt = f"""Analyse ces entrées de veille pour une SAS de revente d'occasion :

{items_txt}

Pour chaque entrée, évalue :
1. Applicabilité directe à la revente d'occasion
2. Gain potentiel (temps / argent)
3. Facilité de mise en place
4. Action concrète recommandée

Format court et actionnable. Ignore ce qui n'est pas directement applicable."""

    try:
        r = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return r.content[0].text if r.content else "⚠️ Analyse indisponible"
    except Exception as e:
        return f"⚠️ Erreur: {e}"
