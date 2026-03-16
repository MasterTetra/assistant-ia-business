"""
MODULE RAPPORTS
──────────────────────────────────────────────────────────
Rapports business hebdomadaires, mensuels, annuels.
Données issues d'Airtable — colonnes réelles du projet.
"""
import httpx
import logging
from datetime import datetime, timedelta
from config.settings import AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS

logger = logging.getLogger(__name__)
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

FIELDS = [
    "Référence gestion", "Statut", "Description",
    "Prix achat total", "Prix achat unitaire", "Quantite totale", "Quantite vendue",
    "Prix vente", "Source", "Plateforme vente",
    "Frais plateforme", "Frais transport",
    "Date achat", "Date vente",
]

FRAIS_DEFAUT = {"ebay": 0.13, "leboncoin": 0.0, "vinted": 0.05, "autre": 0.0}


async def _fetch_all_records() -> list:
    records = []
    offset = None
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            while True:
                params = {"maxRecords": 100, "fields[]": FIELDS}
                if offset:
                    params["offset"] = offset
                resp = await http.get(f"{AIRTABLE_URL}/{TABLE_PRODUITS}", headers=HEADERS, params=params)
                if resp.status_code != 200:
                    break
                data = resp.json()
                records.extend(data.get("records", []))
                offset = data.get("offset")
                if not offset:
                    break
    except Exception as e:
        logger.error(f"Erreur fetch Airtable: {e}")
    return records


def _prix_achat(f: dict) -> float:
    """
    Prix d'achat unitaire pour les calculs de marge.
    Distingue :
    - Article unique  : utilise Prix achat unitaire ou Prix achat total
    - Lot             : utilise Prix achat unitaire (déjà calculé à l'archivage)
    """
    pu = f.get("Prix achat unitaire")
    if pu:
        return float(pu)
    total = float(f.get("Prix achat total") or 0)
    qte = float(f.get("Quantite totale") or 1)
    return total / qte if qte else total

def _prix_achat_total_fiche(f: dict) -> float:
    """Prix d'achat total de la fiche entière (lot ou article unique)."""
    total = f.get("Prix achat total")
    if total:
        return float(total)
    pu = float(f.get("Prix achat unitaire") or 0)
    qte = float(f.get("Quantite totale") or 1)
    return pu * qte

def _est_lot(f: dict) -> bool:
    """Retourne True si la fiche représente un lot (quantité > 1)."""
    return float(f.get("Quantite totale") or 1) > 1


def _frais_pf(f: dict) -> float:
    frais = f.get("Frais plateforme")
    if frais:
        return float(frais)
    pf = (f.get("Plateforme vente") or "").lower()
    pv = float(f.get("Prix vente") or 0)
    return pv * FRAIS_DEFAUT.get(pf, 0.0)


def _frais_tr(f: dict) -> float:
    return float(f.get("Frais transport") or 0)


def _marge(f: dict) -> float:
    pv = float(f.get("Prix vente") or 0)
    return pv - _prix_achat(f) - _frais_pf(f) - _frais_tr(f)


async def generate_report(periode: str = "semaine") -> str:
    now = datetime.now()
    if periode in ("jour", "journalier"):
        debut = now.replace(hour=0, minute=0, second=0, microsecond=0)
        label = f"AUJOURD'HUI — {now.strftime('%d/%m/%Y')}"
    elif periode == "semaine":
        debut = now - timedelta(days=7)
        label = "7 DERNIERS JOURS"
    elif periode == "mois":
        debut = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"MOIS DE {now.strftime('%B %Y').upper()}"
    else:
        debut = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        label = f"ANNÉE {now.year}"

    debut_str = debut.strftime("%Y-%m-%d")
    all_records = await _fetch_all_records()
    fl = [r.get("fields", {}) for r in all_records]

    def date_ok(d): return bool(d) and d[:10] >= debut_str

    achetes = [f for f in fl if date_ok(f.get("Date achat", ""))]

    # DEBUG — log des champs financiers pour diagnostiquer
    for i, f in enumerate(achetes[:5]):  # max 5 fiches
        logger.info(
            f"DEBUG fiche {i+1}: "
            f"ref={f.get('Référence gestion','?')} | "
            f"pa_total={f.get('Prix achat total','VIDE')} | "
            f"pa_unit={f.get('Prix achat unitaire','VIDE')} | "
            f"qte={f.get('Quantite totale','VIDE')} | "
            f"→ _pa_total_fiche={_prix_achat_total_fiche(f):.4f}"
        )
    logger.info(f"DEBUG total capital calculé: {sum(_prix_achat_total_fiche(f) for f in achetes):.4f}€ pour {len(achetes)} fiches")
    vendus = [f for f in fl if f.get("Statut") in ("vendu", "expédié", "livré") and date_ok(f.get("Date vente", ""))]
    en_ligne = [f for f in fl if f.get("Statut") == "en ligne"]
    en_stock = [f for f in fl if f.get("Statut") in ("acheté", "en stockage", "en rénovation")]

    ca = sum(float(f.get("Prix vente") or 0) for f in vendus)
    cout = sum(_prix_achat(f) for f in vendus)
    fp = sum(_frais_pf(f) for f in vendus)
    ft = sum(_frais_tr(f) for f in vendus)
    marge_brute = ca - cout
    marge_nette = marge_brute - fp - ft
    taux = (marge_nette / ca * 100) if ca > 0 else 0

    plateformes: dict = {}
    ca_pf: dict = {}
    for f in vendus:
        pf = f.get("Plateforme vente") or "Non renseigné"
        plateformes[pf] = plateformes.get(pf, 0) + 1
        ca_pf[pf] = ca_pf.get(pf, 0) + float(f.get("Prix vente") or 0)

    sources: dict = {}
    for f in achetes:
        src = f.get("Source") or "Inconnu"
        sources[src] = sources.get(src, 0) + 1

    meilleure = max(vendus, key=_marge, default=None)
    capital_investi = sum(_prix_achat_total_fiche(f) for f in achetes)
    capital_stock = sum(_prix_achat_total_fiche(f) for f in en_ligne + en_stock)
    potentiel = sum(float(f.get("Prix vente") or 0) for f in en_ligne)
    pot_marge = potentiel - sum(_prix_achat_total_fiche(f) for f in en_ligne)

    lines = [
        f"📊 *RAPPORT — {label}*",
        f"📅 {debut.strftime('%d/%m/%Y')} → {now.strftime('%d/%m/%Y')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🛒 *ACHATS*",
        f"  • Objets achetés : *{len(achetes)}*",
        f"  • Capital investi : *{capital_investi:.2f}€*",
    ]
    if sources:
        top = sorted(sources.items(), key=lambda x: x[1], reverse=True)[:3]
        lines.append(f"  • Sources : {', '.join(f'{s} ({n})' for s, n in top)}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "✅ *VENTES*",
        f"  • Objets vendus : *{len(vendus)}*",
        f"  • Chiffre d'affaires : *{ca:.2f}€*",
        f"  • Coût d'achat : -{cout:.2f}€",
        f"  • Frais plateformes : -{fp:.2f}€",
        f"  • Frais transport : -{ft:.2f}€",
    ]
    if plateformes:
        for pf, nb in sorted(plateformes.items(), key=lambda x: x[1], reverse=True)[:3]:
            lines.append(f"  • {pf} : {nb} ventes — {ca_pf.get(pf, 0):.2f}€")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💰 *MARGES*",
        f"  • Marge brute : *{marge_brute:.2f}€*",
        f"  • Marge nette : *{marge_nette:.2f}€*",
        f"  • Taux de marge : *{taux:.1f}%*",
    ]
    if meilleure:
        desc = (meilleure.get("Description") or "")[:30]
        lines.append(f"  • Meilleure vente : {desc} (+{_marge(meilleure):.2f}€)")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📦 *STOCK ACTUEL*",
        f"  • En ligne : *{len(en_ligne)}* articles",
        f"  • En stock/rénovation : *{len(en_stock)}* articles",
        f"  • Capital immobilisé : *{capital_stock:.2f}€*",
    ]
    if potentiel > 0:
        lines.append(f"  • Potentiel ventes en ligne : *{potentiel:.2f}€* (+{pot_marge:.2f}€ marge)")

    if len(vendus) == 0:
        perf = "🔴 Aucune vente cette période"
    elif taux >= 50:
        perf = "🟢 Excellente performance !"
    elif taux >= 30:
        perf = "🟡 Bonne performance"
    else:
        perf = "🟠 Marge à améliorer"
    lines += ["", f"⚡ *Performance :* {perf}"]
    return "\n".join(lines)


async def generate_stock_report() -> str:
    """Rapport détaillé du stock par statut."""
    all_records = await _fetch_all_records()
    fl = [r.get("fields", {}) for r in all_records]

    statuts: dict = {}
    for f in fl:
        s = f.get("Statut") or "Inconnu"
        statuts.setdefault(s, []).append(f)

    ordre = ["acheté", "en stockage", "en rénovation", "en ligne", "vendu", "expédié", "livré"]
    emojis = {"acheté": "🛒", "en stockage": "📦", "en rénovation": "🔧",
               "en ligne": "🟢", "vendu": "✅", "expédié": "📬", "livré": "🏠"}

    lines = [
        "📦 *ÉTAT DU STOCK*",
        f"📅 {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "",
    ]
    for s in ordre:
        items = statuts.get(s, [])
        if not items:
            continue
        emoji = emojis.get(s, "•")
        val = sum(_prix_achat_total_fiche(f) for f in items)
        lines.append(f"{emoji} *{s.capitalize()}* : {len(items)} articles — {val:.2f}€")
        if s == "en ligne":
            pf_c: dict = {}
            for f in items:
                pf = f.get("Plateforme vente") or "?"
                pf_c[pf] = pf_c.get(pf, 0) + 1
            for pf, nb in sorted(pf_c.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"   └ {pf} : {nb} articles")

    capital = sum(_prix_achat_total_fiche(f) for f in fl if f.get("Statut") not in ("vendu", "expédié", "livré"))
    lines += ["", f"📊 Capital actif immobilisé : *{capital:.2f}€*"]
    return "\n".join(lines)
