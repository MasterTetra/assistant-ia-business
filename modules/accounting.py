"""
MODULE COMPTABILITÉ
──────────────────────────────────────────────────────────
Bilan financier complet, TVA sur marge, suivi par plateforme.
"""
import httpx
import logging
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
PARIS_TZ = ZoneInfo("Europe/Paris")
from config.settings import AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS

logger = logging.getLogger(__name__)
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}
TVA_TAUX = 0.20
FRAIS_DEFAUT = {"ebay": 0.13, "leboncoin": 0.0, "vinted": 0.05, "autre": 0.0}

FIELDS = [
    "Référence gestion", "Statut", "Description",
    "Prix achat total", "Prix achat unitaire", "Quantite totale",
    "Prix vente", "Source", "Plateforme vente",
    "Frais plateforme", "Frais transport",
    "Date achat", "Date vente",
]


async def _fetch_all() -> list:
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
        logger.error(f"Fetch error: {e}")
    return [r.get("fields", {}) for r in records]


def _pa(f):
    """Prix achat unitaire — pour calcul de marge par article vendu."""
    pu = f.get("Prix achat unitaire")
    if pu: return float(pu)
    t = float(f.get("Prix achat total") or 0)
    q = float(f.get("Quantite totale") or 1)
    return t / q if q else t

def _pa_total(f):
    """Prix achat total de la fiche — pour capital immobilisé."""
    total = f.get("Prix achat total")
    if total: return float(total)
    pu = float(f.get("Prix achat unitaire") or 0)
    qte = float(f.get("Quantite totale") or 1)
    return pu * qte

def _pv(f): return float(f.get("Prix vente") or 0)

def _fp(f):
    x = f.get("Frais plateforme")
    if x: return float(x)
    pf = (f.get("Plateforme vente") or "").lower()
    return _pv(f) * FRAIS_DEFAUT.get(pf, 0.0)

def _ft(f): return float(f.get("Frais transport") or 0)


def _capital_periode(liste: list) -> float:
    """Calcule le capital réel en groupant par description pour éviter le double-comptage des lots."""
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


def _marge_nette(f): return _pv(f) - _pa(f) - _fp(f) - _ft(f)

def _tva_marge(f):
    marge = _marge_nette(f)
    return marge * TVA_TAUX / (1 + TVA_TAUX) if marge > 0 else 0


def calculer_tva_sur_marge(prix_achat: float, prix_vente: float, frais: float = 0) -> dict:
    """Calcule la TVA selon le régime de la marge (Art. 297A CGI)."""
    marge_ttc = prix_vente - prix_achat - frais
    tva = marge_ttc * TVA_TAUX / (1 + TVA_TAUX) if marge_ttc > 0 else 0
    marge_ht = marge_ttc - tva
    return {
        "prix_achat": prix_achat,
        "prix_vente": prix_vente,
        "frais_total": frais,
        "marge_ttc": marge_ttc,
        "tva_marge": tva,
        "marge_ht": marge_ht,
        "taux_marge_net": (marge_ht / prix_vente * 100) if prix_vente > 0 else 0
    }


async def get_financial_summary() -> str:
    """Bilan financier complet toutes périodes confondues."""
    records = await _fetch_all()

    # Statuts v2 : seul "vendu" = vente finalisée pour comptabilité
    vendus    = [f for f in records if f.get("Statut") == "vendu"]
    en_attente = [f for f in records if f.get("Statut") in ("en cours d'expédition", "livré")]
    en_cours  = [f for f in records if f.get("Statut") not in ("vendu", "en cours d'expédition", "livré")]
    en_ligne  = [f for f in records if f.get("Statut") == "en ligne"]

    # ── Résultats réalisés ────────────────────────────────────
    ca = sum(_pv(f) for f in vendus)
    cout = sum(_pa(f) for f in vendus)
    fp_total = sum(_fp(f) for f in vendus)
    ft_total = sum(_ft(f) for f in vendus)
    marge_brute = ca - cout
    marge_nette = marge_brute - fp_total - ft_total
    # TVA sur marge (Art. 297A CGI)
    tva_totale = sum(_tva_marge(f) for f in vendus)
    benefice_avant_is = marge_nette - tva_totale
    # IS SAS : 15% jusqu'à 42 500€, 25% au-delà
    if benefice_avant_is <= 0:
        is_estime = 0.0
    elif benefice_avant_is <= 42500:
        is_estime = benefice_avant_is * 0.15
    else:
        is_estime = 42500 * 0.15 + (benefice_avant_is - 42500) * 0.25
    resultat_net = benefice_avant_is - is_estime

    # ── Par plateforme ────────────────────────────────────────
    pf_data: dict = {}
    for f in vendus:
        pf = f.get("Plateforme vente") or "Non renseigné"
        if pf not in pf_data:
            pf_data[pf] = {"nb": 0, "ca": 0, "marge": 0, "frais": 0}
        pf_data[pf]["nb"] += 1
        pf_data[pf]["ca"] += _pv(f)
        pf_data[pf]["marge"] += _marge_nette(f)
        pf_data[pf]["frais"] += _fp(f)

    # ── Stock et trésorerie ───────────────────────────────────
    capital_stock = _capital_periode(en_cours)
    potentiel_vente = sum(_pv(f) for f in en_ligne if _pv(f))
    pot_marge = potentiel_vente - _capital_periode(en_ligne)

    # ── Top articles vendus (par marge) ──────────────────────
    top5 = sorted(vendus, key=_marge_nette, reverse=True)[:5]

    now = datetime.now(PARIS_TZ)
    lines = [
        "💰 *BILAN FINANCIER COMPLET*",
        f"📅 Au {now.strftime('%d/%m/%Y %H:%M')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📈 *RÉSULTATS RÉALISÉS*",
        f"  • Ventes : *{len(vendus)}* articles",
        f"  • Chiffre d'affaires : *{ca:.2f}€*",
        f"  • Coût d'achat total : -{cout:.2f}€",
        f"  • Frais plateformes : -{fp_total:.2f}€",
        f"  • Frais transport : -{ft_total:.2f}€",
        f"  ─────────────────────",
        f"  • Marge brute : *{marge_brute:.2f}€*",
        f"  • Marge nette : *{marge_nette:.2f}€*",
        f"  • Taux de marge : *{(marge_nette/ca*100) if ca else 0:.1f}%*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🧾 *FISCALITÉ SAS*",
        f"  • TVA sur marge (Art.297A CGI) : -{tva_totale:.2f}€",
        f"  • Bénéfice avant IS : *{benefice_avant_is:.2f}€*",
        f"  • IS estimé (15%/25%) : -{is_estime:.2f}€",
        f"  • *Résultat net SAS : {resultat_net:.2f}€*",
        f"  ⚠️ Estimations — à valider avec votre expert-comptable",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🏪 *PAR PLATEFORME*",
    ]

    for pf, d in sorted(pf_data.items(), key=lambda x: x[1]["ca"], reverse=True):
        taux_pf = (d["marge"] / d["ca"] * 100) if d["ca"] else 0
        lines.append(f"  • *{pf}* : {d['nb']} ventes — CA {d['ca']:.2f}€ — Marge {d['marge']:.2f}€ ({taux_pf:.0f}%)")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📦 *STOCK & TRÉSORERIE*",
        f"  • Articles en cours : *{len(en_cours)}*",
        f"  • Capital immobilisé : *{capital_stock:.2f}€*",
        f"  • Articles en ligne : *{len(en_ligne)}*",
    ]
    if potentiel_vente > 0:
        lines.append(f"  • Potentiel ventes : *{potentiel_vente:.2f}€* (marge +{pot_marge:.2f}€)")

    # Section EN ATTENTE
    if en_attente:
        ca_att = sum(_pv(f) for f in en_attente)
        cout_att = sum(_pa(f) for f in en_attente)
        lines += [
            "",
            "━━━━━━━━━━━━━━━━━━━━━━",
            "⏳ *EN ATTENTE DE CONFIRMATION*",
            f"  • {sum(1 for f in en_attente if f.get('Statut') == 'en cours d\'expédition')} en cours d'expédition",
            f"  • {sum(1 for f in en_attente if f.get('Statut') == 'livré')} livrés (attente acheteur)",
            f"  • CA potentiel : *{ca_att:.2f}€* | Marge brute : *{ca_att - cout_att:.2f}€*",
            f"  ℹ️ _Non comptabilisé tant que non confirmé_",
        ]

    if top5:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━", "🏆 *TOP 5 MEILLEURES MARGES*"]
        for i, f in enumerate(top5, 1):
            desc = (f.get("Description") or "")[:25]
            pf = f.get("Plateforme vente") or "?"
            lines.append(f"  {i}. {desc} → +{_marge_nette(f):.2f}€ ({pf})")

    return "\n".join(lines)


async def get_realtime_dashboard() -> str:
    """Dashboard temps réel — ventes du jour et de la semaine."""
    from datetime import timedelta
    records = await _fetch_all()
    now = datetime.now(PARIS_TZ)
    today = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    vendus_jour = [f for f in records
                   if f.get("Statut") in ("vendu", "en cours d'expédition", "livré")
                   and (f.get("Date vente") or "")[:10] == today]
    vendus_semaine = [f for f in records
                      if f.get("Statut") in ("vendu", "en cours d'expédition", "livré")
                      and (f.get("Date vente") or "")[:10] >= week_start]
    en_ligne = [f for f in records if f.get("Statut") == "en ligne"]
    en_stock = [f for f in records if f.get("Statut") == "acheté"]

    ca_j = sum(_pv(f) for f in vendus_jour)
    marge_j = sum(_marge_nette(f) for f in vendus_jour)
    ca_s = sum(_pv(f) for f in vendus_semaine)
    marge_s = sum(_marge_nette(f) for f in vendus_semaine)

    lines = [
        "⚡ *DASHBOARD TEMPS RÉEL*",
        f"🕐 {now.strftime('%d/%m/%Y %H:%M')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 *AUJOURD'HUI*",
        f"  • Ventes : *{len(vendus_jour)}* — CA *{ca_j:.2f}€* — Marge *{marge_j:.2f}€*",
        "",
        f"📅 *7 DERNIERS JOURS*",
        f"  • Ventes : *{len(vendus_semaine)}* — CA *{ca_s:.2f}€* — Marge *{marge_s:.2f}€*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📦 *STOCK*",
        f"  • En ligne : *{len(en_ligne)}* articles",
        f"  • En attente de mise en ligne : *{len(en_stock)}* articles",
    ]

    if vendus_jour:
        lines += ["", "✅ *VENTES DU JOUR*"]
        for f in vendus_jour:
            desc = (f.get("Description") or "")[:30]
            pf = f.get("Plateforme vente") or "?"
            lines.append(f"  • {desc} — {_pv(f):.2f}€ ({pf})")

    return "\n".join(lines)
