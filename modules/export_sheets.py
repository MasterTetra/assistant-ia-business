"""
MODULE EXPORT SHEETS — Rapports Google Sheets via Make.com webhook
Pas d'import Google API — tout passe par Make.com
"""
import os
import httpx
import logging
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
PARIS_TZ = ZoneInfo("Europe/Paris")

MAKE_WEBHOOK_SHEETS = os.getenv("MAKE_WEBHOOK_SHEETS", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cashbert-secret-2026")
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}


async def _fetch_periode(debut_str: str, fin_str: str) -> dict:
    """Récupère et compile les données Airtable pour une période."""
    TABLE = "Produits"
    fields = [
        "Statut", "Prix achat unitaire", "Prix achat total",
        "Quantite totale", "Prix vente", "Date achat", "Date vente",
        "Plateforme vente", "Frais plateforme", "Frais transport",
        "Description", "Source",
    ]
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={"fields[]": fields, "maxRecords": 500}
            )
        records = [r["fields"] for r in resp.json().get("records", [])]
    except Exception as e:
        logger.error(f"_fetch_periode: {e}")
        return {}

    TVA = 20 / 120
    IS  = 0.15

    achetes = [f for f in records
               if (f.get("Date achat") or "") >= debut_str
               and (f.get("Date achat") or "") <= fin_str]

    vendus = [f for f in records
              if f.get("Statut") == "vendu"
              and (f.get("Date vente") or "") >= debut_str
              and (f.get("Date vente") or "") <= fin_str]

    ca         = sum(float(f.get("Prix vente") or 0) for f in vendus)
    cout       = sum(float(f.get("Prix achat unitaire") or 0) for f in vendus)
    frais_pf   = sum(float(f.get("Frais plateforme") or 0) or
                     float(f.get("Prix vente") or 0) * 0.13 for f in vendus)
    frais_tr   = sum(float(f.get("Frais transport") or 0) for f in vendus)
    marge_b    = ca - cout
    marge_af   = marge_b - frais_pf - frais_tr
    tva        = max(0, marge_af) * TVA
    is_estime  = max(0, marge_af - tva) * IS
    result_net = marge_af - tva - is_estime

    # Capital immobilisé (groupé par description)
    groupes = {}
    for f in achetes:
        groupes.setdefault(f.get("Description", "?"), []).append(f)
    capital = 0.0
    for groupe in groupes.values():
        avec_tot = [f for f in groupe if f.get("Prix achat total")]
        if avec_tot:
            capital += sum(float(f["Prix achat total"]) for f in avec_tot)
        else:
            capital += sum(float(f.get("Prix achat unitaire") or 0) for f in groupe)

    plateformes = {}
    for f in vendus:
        pf = f.get("Plateforme vente") or "Non renseigné"
        plateformes[pf] = plateformes.get(pf, 0) + float(f.get("Prix vente") or 0)
    pf_principale = max(plateformes, key=plateformes.get) if plateformes else "—"

    return {
        "periode_debut":      debut_str,
        "periode_fin":        fin_str,
        "nb_achats":          len(achetes),
        "capital_investi":    round(capital, 2),
        "nb_ventes":          len(vendus),
        "ca":                 round(ca, 2),
        "cout_achats":        round(cout, 2),
        "frais_plateformes":  round(frais_pf, 2),
        "frais_transport":    round(frais_tr, 2),
        "marge_brute":        round(marge_b, 2),
        "marge_apres_frais":  round(marge_af, 2),
        "tva_marge":          round(tva, 2),
        "is_estime":          round(is_estime, 2),
        "resultat_net":       round(result_net, 2),
        "taux_marge_net":     round(result_net / ca * 100, 1) if ca > 0 else 0,
        "plateforme_principale": pf_principale,
        "export_date":        datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M"),
    }


async def _envoyer_make(payload: dict) -> bool:
    """Envoie les données au webhook Make.com."""
    if not MAKE_WEBHOOK_SHEETS:
        logger.warning("MAKE_WEBHOOK_SHEETS non configuré")
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(MAKE_WEBHOOK_SHEETS, json=payload)
        return resp.status_code in (200, 201, 204)
    except Exception as e:
        logger.error(f"_envoyer_make: {e}")
        return False


async def exporter_rapport(type_rapport: str = "hebdo") -> tuple:
    """
    Exporte un rapport vers Google Sheets via Make.com.
    Retourne (ok: bool, stats: dict)
    """
    now = datetime.now(PARIS_TZ)
    MOIS_FR = {1:"Janvier",2:"Février",3:"Mars",4:"Avril",5:"Mai",6:"Juin",
               7:"Juillet",8:"Août",9:"Septembre",10:"Octobre",11:"Novembre",12:"Décembre"}

    if type_rapport == "hebdo":
        debut = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        fin   = now.strftime("%Y-%m-%d")
        label = f"Semaine {now.isocalendar()[1]} — {now.year}"
        onglet = "Hebdomadaire"
    elif type_rapport == "mensuel":
        debut = now.replace(day=1).strftime("%Y-%m-%d")
        fin   = now.strftime("%Y-%m-%d")
        label = f"{MOIS_FR[now.month]} {now.year}"
        onglet = "Mensuel"
    elif type_rapport == "annuel":
        debut = now.replace(month=1, day=1).strftime("%Y-%m-%d")
        fin   = now.strftime("%Y-%m-%d")
        label = str(now.year)
        onglet = "Annuel"
    else:
        return False, {}

    stats = await _fetch_periode(debut, fin)
    if not stats:
        return False, {}

    stats["periode_label"] = label
    ok = await _envoyer_make({
        "secret": WEBHOOK_SECRET,
        "event":  "rapport_export",
        "type":   type_rapport,
        "onglet": onglet,
        "data":   stats,
    })
    return ok, stats
