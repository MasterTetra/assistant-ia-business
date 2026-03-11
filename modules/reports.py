"""
MODULE RAPPORTS
──────────────────────────────────────────────────────────
Génère des rapports business hebdomadaires et mensuels
directement dans Telegram.
"""
import httpx
from datetime import datetime, timedelta
from config.settings import AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}


async def generate_report(periode: str = "semaine") -> str:
    """Génère un rapport pour la période donnée (semaine ou mois)."""
    now = datetime.now()

    if periode == "semaine":
        debut = now - timedelta(days=7)
        label = "7 DERNIERS JOURS"
    else:
        debut = now.replace(day=1, hour=0, minute=0, second=0)
        label = f"MOIS DE {now.strftime('%B %Y').upper()}"

    debut_iso = debut.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "maxRecords": 1000,
                    "fields[]": [
                        "Référence", "Statut", "Prix achat", "Prix vente",
                        "Date achat", "Date vente", "Source",
                        "Frais plateforme", "Frais transport", "Plateforme vente", "Notes"
                    ]
                }
            )

        if resp.status_code != 200:
            return f"⚠️ Erreur Airtable: {resp.status_code}"

        all_records = resp.json().get("records", [])

    except Exception as e:
        return f"⚠️ Erreur connexion: {str(e)}"

    # ── Filtrage par période ──
    achetes_periode = []
    vendus_periode = []

    for rec in all_records:
        f = rec.get("fields", {})

        date_achat = f.get("Date achat", "")
        if date_achat and date_achat >= debut_iso:
            achetes_periode.append(f)

        date_vente = f.get("Date vente", "")
        statut = f.get("Statut", "")
        if statut in ("vendu", "expédié", "livré") and date_vente and date_vente >= debut_iso:
            vendus_periode.append(f)

    # ── Calculs financiers ──
    total_achats = sum(f.get("Prix achat", 0) or 0 for f in achetes_periode)
    total_ventes = sum(f.get("Prix vente", 0) or 0 for f in vendus_periode)
    total_frais_plateforme = sum(f.get("Frais plateforme", 0) or 0 for f in vendus_periode)
    total_frais_transport = sum(f.get("Frais transport", 0) or 0 for f in vendus_periode)
    total_cout_achats_vendus = sum(f.get("Prix achat", 0) or 0 for f in vendus_periode)

    marge_brute = total_ventes - total_cout_achats_vendus
    marge_nette = marge_brute - total_frais_plateforme - total_frais_transport
    taux_marge = (marge_nette / total_ventes * 100) if total_ventes > 0 else 0

    # ── Stock total actuel ──
    en_ligne = sum(1 for r in all_records if r.get("fields", {}).get("Statut") == "en ligne")
    en_stock = sum(1 for r in all_records if r.get("fields", {}).get("Statut") in ("acheté", "en stockage"))
    total_stock = sum(1 for r in all_records if r.get("fields", {}).get("Statut") not in ("livré",))

    # ── Plateformes les plus actives ──
    plateformes = {}
    for f in vendus_periode:
        pf = f.get("Plateforme vente", "Non renseigné")
        plateformes[pf] = plateformes.get(pf, 0) + 1

    top_plateformes = sorted(plateformes.items(), key=lambda x: x[1], reverse=True)

    # ── Sources d'achat ──
    sources = {}
    for f in achetes_periode:
        src = f.get("Source", "Inconnu")
        sources[src] = sources.get(src, 0) + 1

    top_sources = sorted(sources.items(), key=lambda x: x[1], reverse=True)[:3]

    # ── Construction du rapport ──
    lines = [
        f"📊 *RAPPORT — {label}*",
        f"📅 {debut.strftime('%d/%m/%Y')} → {now.strftime('%d/%m/%Y')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🛒 *ACHATS*",
        f"  • Objets achetés : *{len(achetes_periode)}*",
        f"  • Capital investi : *{total_achats:.2f}€*",
    ]

    if top_sources:
        lines.append(f"  • Top sources : {', '.join(f'{s} ({n})' for s, n in top_sources)}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "✅ *VENTES*",
        f"  • Objets vendus : *{len(vendus_periode)}*",
        f"  • Chiffre d'affaires : *{total_ventes:.2f}€*",
        f"  • Frais plateformes : -{total_frais_plateforme:.2f}€",
        f"  • Frais transport : -{total_frais_transport:.2f}€",
    ]

    if top_plateformes:
        lines.append(f"  • Top plateforme : {top_plateformes[0][0]} ({top_plateformes[0][1]} ventes)")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "💰 *MARGES*",
        f"  • Marge brute : *{marge_brute:.2f}€*",
        f"  • Marge nette : *{marge_nette:.2f}€*",
        f"  • Taux de marge : *{taux_marge:.1f}%*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "📦 *STOCK ACTUEL*",
        f"  • En ligne : {en_ligne} objets",
        f"  • En stock (pas encore mis en ligne) : {en_stock} objets",
        f"  • Total actif : {total_stock} objets",
    ]

    # ── Indicateur de performance ──
    if taux_marge >= 50:
        perf = "🟢 Excellente semaine !"
    elif taux_marge >= 30:
        perf = "🟡 Bonne performance"
    elif taux_marge > 0:
        perf = "🟠 Marge à améliorer"
    else:
        perf = "🔴 Pas de ventes cette période"

    lines += ["", f"⚡ *Performance :* {perf}"]

    return "\n".join(lines)
