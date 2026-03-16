"""
MODULE RAPPORTS
──────────────────────────────────────────────────────────
Rapports business : journalier, hebdomadaire, mensuel, annuel.
Colonnes Airtable réelles. Nouveaux statuts v2.
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

# ── Statuts v2 ──────────────────────────────────────────────────────────────
# "acheté"                → acheté, pas encore mis en vente
# "en ligne"              → publié sur une plateforme
# "en cours d'expédition" → vendu, en conditionnement/acheminement
# "livré"                 → livré, en attente confirmation acheteur
# "vendu"                 → vente finalisée, sort de la gestion active
# "en stockage"           → stockage client (location d'espace)
# "en rénovation"         → en réparation, retiré ou à retirer des plateformes

STATUTS_VENDUS    = ("vendu", "en cours d'expédition", "livré")
STATUTS_ACTIFS    = ("acheté", "en ligne", "en cours d'expédition", "livré", "en stockage", "en rénovation")
STATUTS_STOCK     = ("acheté", "en ligne", "en stockage", "en rénovation")

FRAIS_DEFAUT = {"ebay": 0.13, "leboncoin": 0.0, "vinted": 0.05, "autre": 0.0}

# TVA sur marge (Art. 297A CGI) — biens d'occasion achetés à des non-assujettis
TVA_MARGE_TAUX = 20 / 120  # = 16.67% de la marge TTC

# Charges sociales estimées (auto-entrepreneur marchandises) — à adapter
CHARGES_SOCIALES_TAUX = 0.128  # 12.8% du CA HT


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
                    logger.error(f"Airtable {resp.status_code}: {resp.text[:100]}")
                    break
                data = resp.json()
                records.extend(data.get("records", []))
                offset = data.get("offset")
                if not offset:
                    break
    except Exception as e:
        logger.error(f"Fetch Airtable: {e}")
    return [r.get("fields", {}) for r in records]


def _prix_achat(f: dict) -> float:
    """Prix achat unitaire pour calcul de marge par article."""
    pu = f.get("Prix achat unitaire")
    if pu:
        return float(pu)
    total = float(f.get("Prix achat total") or 0)
    qte = float(f.get("Quantite totale") or 1)
    return total / qte if qte else total


def _est_lot(f: dict) -> bool:
    return float(f.get("Quantite totale") or 1) > 1


def _capital_periode(liste: list) -> float:
    """
    Capital réel — groupe par description pour éviter double-comptage des lots.
    La fiche principale du lot porte Prix achat total ; les copies unitaires non.
    """
    groupes = {}
    for f in liste:
        desc = f.get("Description") or "?"
        groupes.setdefault(desc, []).append(f)
    total = 0.0
    for groupe in groupes.values():
        avec_total = [f for f in groupe if f.get("Prix achat total")]
        if avec_total:
            total += sum(float(f["Prix achat total"]) for f in avec_total)
        else:
            total += sum(float(f.get("Prix achat unitaire") or 0) for f in groupe)
    return total


def _frais_pf(f: dict) -> float:
    x = f.get("Frais plateforme")
    if x:
        return float(x)
    pf = (f.get("Plateforme vente") or "").lower()
    pv = float(f.get("Prix vente") or 0)
    return pv * FRAIS_DEFAUT.get(pf, 0.0)


def _frais_tr(f: dict) -> float:
    return float(f.get("Frais transport") or 0)


def _marge_brute(f: dict) -> float:
    """Marge brute = Prix vente - Prix achat unitaire (hors frais)."""
    return float(f.get("Prix vente") or 0) - _prix_achat(f)


def _marge_nette(f: dict) -> float:
    """Marge nette = Marge brute - Frais plateforme - Frais transport."""
    return _marge_brute(f) - _frais_pf(f) - _frais_tr(f)


def _tva_marge(marge_ttc: float) -> float:
    """TVA sur marge (Art. 297A CGI). Ne s'applique que si marge > 0."""
    return marge_ttc * TVA_MARGE_TAUX if marge_ttc > 0 else 0.0


def _charges_sociales(ca: float) -> float:
    """Cotisations sociales auto-entrepreneur marchandises (~12.8% CA)."""
    return ca * CHARGES_SOCIALES_TAUX


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
    fl = await _fetch_all_records()

    def date_ok(d): return bool(d) and d[:10] >= debut_str

    # ── ACHATS de la période ──────────────────────────────────────────────────
    # Tous les articles achetés pendant la période, SAUF ceux finalisés (vendu)
    achetes_periode = [
        f for f in fl
        if date_ok(f.get("Date achat", ""))
        and f.get("Statut") not in ("vendu",)
    ]
    capital_investi = _capital_periode(achetes_periode)

    # Sources
    sources: dict = {}
    for f in achetes_periode:
        src = f.get("Source") or "Non renseigné"
        sources[src] = sources.get(src, 0) + 1

    # ── VENTES de la période ─────────────────────────────────────────────────
    vendus = [
        f for f in fl
        if f.get("Statut") in STATUTS_VENDUS
        and date_ok(f.get("Date vente", ""))
    ]
    nb_vendus = len(vendus)
    ca = sum(float(f.get("Prix vente") or 0) for f in vendus)
    cout_vendus = sum(_prix_achat(f) for f in vendus)
    fp_total = sum(_frais_pf(f) for f in vendus)
    ft_total = sum(_frais_tr(f) for f in vendus)

    # ── MARGES ───────────────────────────────────────────────────────────────
    mb_total = ca - cout_vendus                          # Marge brute HT
    mn_avant_charges = mb_total - fp_total - ft_total   # Après frais directs
    tva_total = _tva_marge(mn_avant_charges)            # TVA sur marge
    charges = _charges_sociales(ca)                      # Charges sociales
    mn_nette = mn_avant_charges - tva_total - charges   # Marge nette réelle
    taux_mn = (mn_nette / ca * 100) if ca > 0 else 0

    # ── PAR PLATEFORME ────────────────────────────────────────────────────────
    pf_data: dict = {}
    for f in vendus:
        pf = f.get("Plateforme vente") or "Non renseigné"
        pf_data.setdefault(pf, {"nb": 0, "ca": 0})
        pf_data[pf]["nb"] += 1
        pf_data[pf]["ca"] += float(f.get("Prix vente") or 0)

    # ── STOCK PAR STATUT ─────────────────────────────────────────────────────
    def nb_statut(s): return len([f for f in fl if f.get("Statut") == s])
    def nb_statuts(ss): return len([f for f in fl if f.get("Statut") in ss])

    s_achete     = nb_statut("acheté")
    s_en_ligne   = nb_statut("en ligne")
    s_expedition = nb_statut("en cours d'expédition")
    s_livre      = nb_statut("livré")
    s_stockage   = nb_statut("en stockage")
    s_renovation = nb_statut("en rénovation")

    capital_stock = _capital_periode([f for f in fl if f.get("Statut") in STATUTS_STOCK])
    potentiel = sum(float(f.get("Prix vente") or 0) for f in fl if f.get("Statut") == "en ligne")
    pot_marge = potentiel - _capital_periode([f for f in fl if f.get("Statut") == "en ligne"])

    meilleure = max(vendus, key=_marge_nette, default=None)

    # ── CONSTRUCTION DU RAPPORT ───────────────────────────────────────────────
    lines = [
        f"📊 *RAPPORT — {label}*",
        f"📅 {debut.strftime('%d/%m/%Y')} → {now.strftime('%d/%m/%Y')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🛒 *ACHATS*",
        f"  • Objets achetés (actifs) : *{len(achetes_periode)}*",
        f"  • Capital investi : *{capital_investi:.2f}€*",
    ]
    if sources:
        top = sorted(sources.items(), key=lambda x: x[1], reverse=True)[:3]
        lines.append(f"  • Sources : {', '.join(f'{s} ({n})' for s, n in top)}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "✅ *VENTES*",
        f"  • Objets vendus : *{nb_vendus}*",
        f"  • Chiffre d'affaires : *{ca:.2f}€*",
        f"  • Coût d'achat : -{cout_vendus:.2f}€",
        f"  • Frais plateformes : -{fp_total:.2f}€",
        f"  • Frais transport : -{ft_total:.2f}€",
    ]
    if pf_data:
        for pf, d in sorted(pf_data.items(), key=lambda x: x[1]["ca"], reverse=True)[:3]:
            lines.append(f"  • {pf} : {d['nb']} ventes — {d['ca']:.2f}€")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💰 *MARGES*",
        f"  • Marge brute HT : *{mb_total:.2f}€*",
        f"  • Marge après frais directs : *{mn_avant_charges:.2f}€*",
        f"  • TVA sur marge (Art.297A) : -{tva_total:.2f}€",
        f"  • Charges sociales (~12.8% CA) : -{charges:.2f}€",
        f"  • *Marge nette réelle : {mn_nette:.2f}€ ({taux_mn:.1f}%)*",
    ]
    if meilleure:
        desc = (meilleure.get("Description") or "")[:30]
        lines.append(f"  • Meilleure vente : {desc} (+{_marge_nette(meilleure):.2f}€)")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📦 *STOCK ACTUEL*",
    ]
    if s_achete:     lines.append(f"  🛒 Acheté (à mettre en ligne) : *{s_achete}*")
    if s_en_ligne:   lines.append(f"  🟢 En ligne : *{s_en_ligne}*")
    if s_expedition: lines.append(f"  📬 En cours d'expédition : *{s_expedition}*")
    if s_livre:      lines.append(f"  📦 Livré (attente confirmation) : *{s_livre}*")
    if s_stockage:   lines.append(f"  🏭 En stockage : *{s_stockage}*")
    if s_renovation: lines.append(f"  🔧 En rénovation : *{s_renovation}*")
    lines.append(f"  💰 Capital immobilisé : *{capital_stock:.2f}€*")
    if potentiel > 0:
        lines.append(f"  📈 Potentiel ventes en ligne : *{potentiel:.2f}€* (+{pot_marge:.2f}€ marge)")

    if nb_vendus == 0:
        perf = "🔴 Aucune vente cette période"
    elif taux_mn >= 40:
        perf = "🟢 Excellente performance !"
    elif taux_mn >= 20:
        perf = "🟡 Bonne performance"
    else:
        perf = "🟠 Marge à améliorer"
    lines += ["", f"⚡ *Performance :* {perf}"]
    lines.append(f"\n_⚠️ TVA et charges estimées — confirmer avec votre comptable_")

    return "\n".join(lines)


async def generate_stock_report() -> str:
    """Rapport détaillé du stock par statut."""
    fl = await _fetch_all_records()
    now = datetime.now()

    def items(s): return [f for f in fl if f.get("Statut") == s]

    ordre = [
        ("acheté",               "🛒", "Acheté (à mettre en ligne)"),
        ("en ligne",             "🟢", "En ligne"),
        ("en cours d'expédition","📬", "En cours d'expédition"),
        ("livré",                "📦", "Livré (attente confirmation)"),
        ("en stockage",          "🏭", "En stockage"),
        ("en rénovation",        "🔧", "En rénovation"),
    ]

    lines = [
        "📦 *ÉTAT DU STOCK*",
        f"📅 {now.strftime('%d/%m/%Y %H:%M')}",
        "",
    ]
    for statut, emoji, label in ordre:
        lst = items(statut)
        if not lst:
            continue
        val = _capital_periode(lst)
        lines.append(f"{emoji} *{label}* : {len(lst)} articles — {val:.2f}€")
        if statut == "en ligne":
            pf_c: dict = {}
            for f in lst:
                pf = f.get("Plateforme vente") or "?"
                pf_c[pf] = pf_c.get(pf, 0) + 1
            for pf, nb in sorted(pf_c.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"   └ {pf} : {nb} articles")

    actifs = [f for f in fl if f.get("Statut") in STATUTS_STOCK]
    capital = _capital_periode(actifs)
    lines += ["", f"💰 Capital actif immobilisé : *{capital:.2f}€*"]
    return "\n".join(lines)
