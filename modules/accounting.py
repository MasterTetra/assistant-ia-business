"""
MODULE COMPTABILITÉ
──────────────────────────────────────────────────────────
Calculs financiers automatiques, TVA sur marge,
bilan financier complet.
"""
import httpx
from datetime import datetime
from config.settings import AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS, PLATFORM_FEES

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}

TVA_TAUX = 0.20  # 20%


def calculer_tva_sur_marge(prix_achat: float, prix_vente: float, frais: float = 0) -> dict:
    """
    Calcule la TVA selon le régime de la marge (Art. 297A CGI).
    Applicable aux biens d'occasion achetés à des non-assujettis.
    
    TVA = (Marge TTC) × 20/120
    Marge nette HT = Marge TTC × 100/120
    """
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
    """Retourne un résumé financier complet depuis Airtable."""
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "maxRecords": 1000,
                    "fields[]": [
                        "Statut", "Prix achat", "Prix vente",
                        "Frais plateforme", "Frais transport",
                        "Plateforme vente", "Date vente"
                    ]
                }
            )

        if resp.status_code != 200:
            return f"⚠️ Erreur Airtable: {resp.status_code}"

        records = [r.get("fields", {}) for r in resp.json().get("records", [])]

    except Exception as e:
        return f"⚠️ Erreur connexion: {str(e)}"

    vendus = [r for r in records if r.get("Statut") in ("vendu", "expédié", "livré")]
    en_cours = [r for r in records if r.get("Statut") not in ("vendu", "expédié", "livré", "")]

    # ── Calculs globaux ──
    ca_total = sum(r.get("Prix vente", 0) or 0 for r in vendus)
    cout_achats = sum(r.get("Prix achat", 0) or 0 for r in vendus)
    frais_pf = sum(r.get("Frais plateforme", 0) or 0 for r in vendus)
    frais_tr = sum(r.get("Frais transport", 0) or 0 for r in vendus)
    total_frais = frais_pf + frais_tr

    marge_brute = ca_total - cout_achats
    marge_nette = marge_brute - total_frais

    # TVA sur marge totale
    tva_totale = 0
    for r in vendus:
        pa = r.get("Prix achat", 0) or 0
        pv = r.get("Prix vente", 0) or 0
        fr = (r.get("Frais plateforme", 0) or 0) + (r.get("Frais transport", 0) or 0)
        calc = calculer_tva_sur_marge(pa, pv, fr)
        tva_totale += calc["tva_marge"]

    # Capital immobilisé (stock actuel)
    capital_stock = sum(r.get("Prix achat", 0) or 0 for r in en_cours)
    valeur_vente_potentielle = sum(r.get("Prix vente", 0) or 0 for r in en_cours if r.get("Prix vente"))

    # ── Frais par plateforme ──
    frais_par_pf = {}
    for r in vendus:
        pf = r.get("Plateforme vente", "Inconnu")
        pv = r.get("Prix vente", 0) or 0
        frais_auto = pv * PLATFORM_FEES.get(pf.lower(), 0) / 100
        frais_par_pf[pf] = frais_par_pf.get(pf, 0) + frais_auto

    now = datetime.now()
    lines = [
        f"💰 *BILAN FINANCIER*",
        f"📅 Au {now.strftime('%d/%m/%Y')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📈 *RÉSULTATS RÉALISÉS*",
        f"  • Nombre de ventes : *{len(vendus)}*",
        f"  • Chiffre d'affaires : *{ca_total:.2f}€*",
        f"  • Coût total des achats vendus : -{cout_achats:.2f}€",
        f"  • Frais plateformes : -{frais_pf:.2f}€",
        f"  • Frais transport : -{frais_tr:.2f}€",
        f"  ─────────────────────",
        f"  • Marge brute : *{marge_brute:.2f}€*",
        f"  • Marge nette : *{marge_nette:.2f}€*",
        f"  • Taux de marge net : *{(marge_nette/ca_total*100) if ca_total else 0:.1f}%*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🧾 *TVA SUR LA MARGE*",
        f"  • TVA collectée (régime marge) : *{tva_totale:.2f}€*",
        f"  • Marge nette après TVA : *{marge_nette - tva_totale:.2f}€*",
        f"  ⚠️ Vérifier avec votre comptable",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📦 *STOCK & TRÉSORERIE*",
        f"  • Objets en stock : *{len(en_cours)}*",
        f"  • Capital immobilisé : *{capital_stock:.2f}€*",
    ]

    if valeur_vente_potentielle > 0:
        pot = valeur_vente_potentielle - capital_stock
        lines.append(f"  • Marge potentielle stock : *{pot:.2f}€*")

    if frais_par_pf:
        lines += ["", "━━━━━━━━━━━━━━━━━━━━━━",
                  "🏪 *FRAIS PAR PLATEFORME*"]
        for pf, frais in sorted(frais_par_pf.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  • {pf} : {frais:.2f}€")

    return "\n".join(lines)
