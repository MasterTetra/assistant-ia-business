"""
MODULE AUDIT — Diagnostic interne + veille + recommandations actionnables
Architecture :
  /audit global   → audit complet business
  /audit pricing  → analyse des prix
  /audit sourcing → analyse des sources d'achat
  /audit fiscal   → veille fiscale SAS
  /audit outils   → optimisation process
  /audit veille   → tendances marché externes
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
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS
)

logger = logging.getLogger(__name__)
PARIS_TZ = ZoneInfo("Europe/Paris")
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}
def _get_client():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

# ── SYSTEM PROMPT AUDIT ───────────────────────────────────────────────────────
SYSTEM_AUDIT = """
Tu es un système expert d'audit et d'optimisation business pour une SAS française de revente d'objets d'occasion.
Tu analyses : performance, outils, marché, fiscalité, cashflow, pricing, sourcing.

Pour CHAQUE point identifié, tu utilises OBLIGATOIREMENT ce format :

🔎 [CATÉGORIE] : [titre court]
❗ Problème : [description précise]
💡 Opportunité : [ce qu'on peut améliorer]
✅ Solution : [action concrète à mettre en place]
📊 Impact estimé : [€/mois ou % ou heures économisées]
🎯 Priorité : HIGH / MEDIUM / LOW
─────────────────────────────────

Règles :
- Maximum 5 points par audit pour rester actionnable
- Trier par priorité décroissante (HIGH en premier)
- Impact toujours chiffré si possible
- Solutions concrètes et directement applicables
- Pas de théorie, que du pratique
"""

# ── DONNÉES INTERNES ──────────────────────────────────────────────────────────

async def _fetch_donnees_business() -> dict:
    """Récupère toutes les données Airtable pour analyse."""
    fields = [
        "Référence gestion", "Description", "Statut",
        "Prix achat unitaire", "Prix achat total", "Quantite totale",
        "Prix vente", "Date achat", "Date vente",
        "Plateforme vente", "Frais plateforme", "Frais transport",
        "Notes", "Annonce générée", "Photos URLs",
    ]
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={"fields[]": fields, "maxRecords": 200,
                        "sort[0][field]": "Date achat", "sort[0][direction]": "desc"}
            )
        records = resp.json().get("records", [])
        return {"records": [r["fields"] for r in records], "total": len(records)}
    except Exception as e:
        logger.error(f"_fetch_donnees_business: {e}")
        return {"records": [], "total": 0}


def _compiler_stats(records: list) -> dict:
    """Compile les statistiques business depuis les records Airtable."""
    now = datetime.now(PARIS_TZ)
    il_y_a_30j = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    il_y_a_7j  = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    stats = {
        "total_articles": len(records),
        "par_statut": {},
        "ca_30j": 0, "ca_7j": 0,
        "vendus_30j": [], "vendus_7j": [],
        "stock_bloque": [],  # achetés depuis > 30j sans vente
        "sans_annonce": [],  # statut acheté sans annonce générée
        "sans_photos": [],   # statut acheté sans photos
        "plateformes": {},   # performance par plateforme
        "marges": [],        # liste des marges nettes
        "prix_sous_marche": [],  # articles potentiellement sous-pricés
        "rotation_rapide": [],   # vendus en < 7j
        "rotation_lente": [],    # achetés > 45j non vendus
    }

    for f in records:
        statut = f.get("Statut", "?")
        stats["par_statut"][statut] = stats["par_statut"].get(statut, 0) + 1

        pv = float(f.get("Prix vente") or 0)
        pa = float(f.get("Prix achat unitaire") or 0)
        date_vente = f.get("Date vente", "") or ""
        date_achat = f.get("Date achat", "") or ""
        pf = f.get("Plateforme vente", "") or "Non renseigné"
        desc = f.get("Description", "")[:40]

        # CA par période
        if date_vente >= il_y_a_30j and pv > 0:
            stats["ca_30j"] += pv
            stats["vendus_30j"].append({"desc": desc, "pv": pv, "pa": pa, "pf": pf, "date": date_vente})
        if date_vente >= il_y_a_7j and pv > 0:
            stats["ca_7j"] += pv
            stats["vendus_7j"].append({"desc": desc, "pv": pv, "pa": pa})

        # Marge
        if pv > 0 and pa > 0:
            marge = (pv - pa) / pv * 100
            stats["marges"].append({"desc": desc, "marge_pct": round(marge, 1), "pv": pv, "pa": pa})

        # Plateformes
        if statut == "vendu" and pf:
            if pf not in stats["plateformes"]:
                stats["plateformes"][pf] = {"ventes": 0, "ca": 0}
            stats["plateformes"][pf]["ventes"] += 1
            stats["plateformes"][pf]["ca"] += pv

        # Stock bloqué (acheté > 30j)
        if statut == "acheté" and date_achat and date_achat < il_y_a_30j:
            stats["stock_bloque"].append({"desc": desc, "date_achat": date_achat, "pa": pa})

        # Sans annonce / sans photos
        if statut == "acheté":
            if not f.get("Annonce générée"):
                stats["sans_annonce"].append(desc)
            if not f.get("Photos URLs"):
                stats["sans_photos"].append(desc)

        # Rotation rapide (vendu en < 7j)
        if statut == "vendu" and date_achat and date_vente:
            try:
                da = datetime.strptime(date_achat[:10], "%Y-%m-%d")
                dv = datetime.strptime(date_vente[:10], "%Y-%m-%d")
                jours = (dv - da).days
                if jours <= 7:
                    stats["rotation_rapide"].append({"desc": desc, "jours": jours, "pv": pv})
                elif jours > 45 and statut == "acheté":
                    stats["rotation_lente"].append({"desc": desc, "jours": jours})
            except Exception:
                pass

    return stats


async def _audit_ia(type_audit: str, stats: dict, donnees_brutes: str) -> str:
    """Appel Claude pour générer l'audit avec les données compilées."""
    prompts = {
        "global": f"""Réalise un audit GLOBAL complet de ce business de revente.
Données disponibles :
{donnees_brutes}

Analyse : rentabilité, stock bloqué, pricing, plateformes, process, opportunités manquées.
Identifie les 5 points les plus critiques à améliorer.""",

        "pricing": f"""Réalise un audit PRICING précis.
Données :
{donnees_brutes}

Analyse : prix trop bas vs marché, prix trop hauts (invendus), cohérence par plateforme.
Identifie les articles sous-pricés et sur-pricés avec impact chiffré.""",

        "sourcing": f"""Réalise un audit SOURCING.
Données :
{donnees_brutes}

Analyse : sources d'achat les plus rentables, catégories à fort potentiel, 
articles à fort taux de rotation, opportunités de sourcing non exploitées.""",

        "fiscal": f"""Réalise un audit FISCAL pour une SAS française.
Contexte : régime TVA sur marge (Art.297A CGI), IS 15%/25%, biens d'occasion.
Données :
{donnees_brutes}

Analyse : optimisation fiscale, seuils à surveiller, obligations légales,
points de vigilance comptables, opportunités de déduction.""",

        "outils": f"""Réalise un audit OUTILS & PROCESS.
Données :
{donnees_brutes}

Analyse : tâches répétitives à automatiser, process inefficaces, 
outils manquants, intégrations possibles (Make.com, eBay API, etc.).
Temps gagnable et ROI estimé de chaque amélioration.""",

        "veille": f"""Réalise un audit VEILLE MARCHÉ.
Données actuelles du business :
{donnees_brutes}

Identifie :
1. Tendances marché actuelles applicables à ce business
2. Catégories émergentes à fort potentiel
3. Nouvelles plateformes ou canaux de vente à explorer
4. Opportunités saisonnières prochaines
5. Menaces ou changements réglementaires à surveiller
Raisonne à partir de tes connaissances du marché français de l'occasion.""",
    }

    prompt_texte = prompts.get(type_audit, prompts["global"])

    try:
        r = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            system=SYSTEM_AUDIT,
            messages=[{"role": "user", "content": prompt_texte}]
        )
        return r.content[0].text if r.content else "⚠️ Pas de résultat."
    except Exception as e:
        logger.error(f"_audit_ia error: {e}")
        return f"⚠️ Erreur lors de l'analyse IA: {e}"


async def generer_audit(type_audit: str = "global") -> str:
    """Point d'entrée principal — génère l'audit demandé."""
    types_valides = ["global", "pricing", "sourcing", "fiscal", "outils", "veille"]
    if type_audit not in types_valides:
        return (
            f"⚠️ Type d'audit invalide : `{type_audit}`\n\n"
            f"Types disponibles :\n" +
            "\n".join(f"  • `/audit {t}`" for t in types_valides)
        )

    # Récupérer et compiler les données
    data = await _fetch_donnees_business()
    stats = _compiler_stats(data["records"])

    # Construire le résumé des données pour le prompt
    now = datetime.now(PARIS_TZ)
    # Lire les notes manuelles et veille réglementaire depuis GSheets
    notes_manuelles = ""
    veille_reglem = ""
    try:
        from modules.gsheets import lire_notes_manuelles, lire_veille_reglementaire
        notes_manuelles = await lire_notes_manuelles()
        veille_reglem = await lire_veille_reglementaire()
    except Exception as e:
        logger.warning(f"GSheets lecture ignorée: {e}")

    donnees_brutes = f"""
SNAPSHOT BUSINESS — {now.strftime("%d/%m/%Y %H:%M")}

STOCK :
  Total articles : {stats["total_articles"]}
  Par statut : {stats["par_statut"]}
  Stock bloqué (>30j sans vente) : {len(stats["stock_bloque"])} articles
  Sans annonce : {len(stats["sans_annonce"])} articles
  Sans photos : {len(stats["sans_photos"])} articles

VENTES :
  CA 7 derniers jours : {stats["ca_7j"]:.2f}€
  CA 30 derniers jours : {stats["ca_30j"]:.2f}€
  Ventes récentes : {[f"{v["desc"]} → {v["pv"]}€ ({v["pf"]})" for v in stats["vendus_30j"][:10]]}

PERFORMANCE PLATEFORMES :
  {stats["plateformes"]}

MARGES (top 5 meilleurs) :
  {sorted(stats["marges"], key=lambda x: x["marge_pct"], reverse=True)[:5]}

ROTATION :
  Articles vendus rapidement (<7j) : {len(stats["rotation_rapide"])}
  {[f"{r["desc"]} ({r["jours"]}j → {r["pv"]}€)" for r in stats["rotation_rapide"][:5]]}

NOTES MANUELLES (ajoutées par le dirigeant) :
{notes_manuelles if notes_manuelles else "Aucune note manuelle"}

VEILLE RÉGLEMENTAIRE EN COURS :
{veille_reglem if veille_reglem else "Aucune veille active"}
"""

    # Générer l'audit
    labels = {
        "global": "GLOBAL", "pricing": "PRICING", "sourcing": "SOURCING",
        "fiscal": "FISCAL", "outils": "OUTILS & PROCESS", "veille": "VEILLE MARCHÉ"
    }
    header = (
        f"🔍 *AUDIT {labels[type_audit]}*\n"
        f"📅 {now.strftime('%d/%m/%Y %H:%M')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    resultat = await _audit_ia(type_audit, stats, donnees_brutes)

    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━\n"
        f"_Données : {stats['total_articles']} articles analysés_\n"
        f"_Autres audits : /audit pricing · sourcing · fiscal · outils · veille_"
    )

    # ── Archivage automatique dans Google Sheets ──────────────────────────────
    try:
        from modules.gsheets import archiver_audit
        await archiver_audit(type_audit, resultat)
    except Exception as e:
        logger.warning(f"GSheets archivage audit ignoré: {e}")

    return header + resultat + footer
