"""
MODULE ARCHIVE — Gestion lots Airtable + archivage Google Sheets via Make.com
Logique :
  Vente détectée → archive ligne VENTES dans Sheets
                 → décrémente quantité lot dans Airtable
                 → si qté = 0 → archive PRODUIT dans Sheets → supprime Airtable
"""
import os
import httpx
import logging
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
PARIS_TZ = ZoneInfo("Europe/Paris")

AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")
AIRTABLE_URL     = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT       = {"Authorization": f"Bearer {AIRTABLE_API_KEY}",
                    "Content-Type": "application/json"}
TABLE            = "Produits"


# ── HELPERS AIRTABLE ──────────────────────────────────────────────────────────

async def _get_lot_by_ref(ref: str) -> dict | None:
    """Récupère un lot Airtable par sa Référence gestion."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={
                    "filterByFormula": f"{{Référence gestion}}=\"{ref}\"",
                    "maxRecords": 1
                }
            )
        records = resp.json().get("records", [])
        if not records:
            return None
        r = records[0]
        return {"record_id": r["id"], **r["fields"]}
    except Exception as e:
        logger.error(f"_get_lot_by_ref({ref}): {e}")
        return None


async def _get_lot_by_description(description: str) -> dict | None:
    """Récupère un lot actif par description (pour les ventes Make.com sans ref)."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={
                    "filterByFormula": (
                        f"AND(FIND(LOWER(\"{description[:30].lower()}\"), LOWER({{Description}}))>0,"
                        f"{{Statut}}=\"en ligne\")"
                    ),
                    "maxRecords": 1,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc"
                }
            )
        records = resp.json().get("records", [])
        if not records:
            return None
        r = records[0]
        return {"record_id": r["id"], **r["fields"]}
    except Exception as e:
        logger.error(f"_get_lot_by_description: {e}")
        return None


async def _patch_lot(record_id: str, fields: dict) -> bool:
    """Met à jour un record Airtable."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE}/{record_id}",
                headers=HEADERS_AT,
                json={"fields": fields}
            )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"_patch_lot: {e}")
        return False


async def _supprimer_lot(record_id: str) -> bool:
    """Supprime un record Airtable (UNIQUEMENT après archivage Sheets confirmé)."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.delete(
                f"{AIRTABLE_URL}/{TABLE}/{record_id}",
                headers=HEADERS_AT
            )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"_supprimer_lot: {e}")
        return False


# ── ARCHIVAGE GOOGLE SHEETS ───────────────────────────────────────────────────

async def _envoyer_make(payload: dict) -> bool:
    """Envoie vers Make.com pour archivage Google Sheets."""
    webhook = os.getenv("MAKE_WEBHOOK_SHEETS", "")
    secret  = os.getenv("WEBHOOK_SECRET", "cashbert-secret-2026")
    if not webhook:
        logger.warning("MAKE_WEBHOOK_SHEETS non configuré")
        return False
    try:
        payload["secret"] = secret
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(webhook, json=payload)
        ok = resp.status_code in (200, 201, 204)
        logger.info(f"Make.com {payload.get('event')} → {resp.status_code}")
        return ok
    except Exception as e:
        logger.error(f"_envoyer_make: {e}")
        return False


def _calculer_resultat_net(pv: float, pa: float, frais_pf: float, frais_tr: float) -> float:
    """Calcule le résultat net après TVA marge + IS estimé."""
    marge_brute = pv - pa
    marge_apres_frais = marge_brute - frais_pf - frais_tr
    tva = max(0, marge_apres_frais) * (20 / 120)
    is_estime = max(0, marge_apres_frais - tva) * 0.15
    return round(marge_apres_frais - tva - is_estime, 2)


# ── LOGIQUE PRINCIPALE ────────────────────────────────────────────────────────

async def traiter_vente(
    ref: str,
    qte_vendue: int,
    prix_vente: float,
    plateforme: str = "eBay",
    frais_plateforme: float = 0.0,
    frais_transport: float = 0.0,
) -> dict:
    """
    Traite une vente :
    1. Archive ligne dans Sheets VENTES
    2. Décrémente la quantité du lot dans Airtable
    3. Si qté = 0 → archive lot dans Sheets PRODUITS ARCHIVÉS → supprime Airtable
    Retourne un dict avec le résultat de l'opération.
    """
    now = datetime.now(PARIS_TZ)
    date_vente = now.strftime("%d/%m/%Y")

    # 1. Récupérer le lot
    lot = await _get_lot_by_ref(ref)
    if not lot:
        return {"ok": False, "erreur": f"Lot {ref} introuvable dans Airtable"}

    record_id      = lot["record_id"]
    pa_unitaire    = float(lot.get("Prix achat unitaire") or 0)
    qte_actuelle   = int(lot.get("Quantite totale") or 0)
    qte_deja_vendue = int(lot.get("Quantite vendue") or 0)

    # Frais plateforme par défaut 13% si non fourni
    if frais_plateforme == 0:
        frais_plateforme = round(prix_vente * qte_vendue * 0.13, 2)

    marge_brute  = round((prix_vente - pa_unitaire) * qte_vendue, 2)
    resultat_net = _calculer_resultat_net(
        prix_vente * qte_vendue, pa_unitaire * qte_vendue,
        frais_plateforme, frais_transport
    )

    # 2. Archiver la vente dans Sheets VENTES
    payload_vente = {
        "event": "vente_archiver",
        "data": {
            "date_vente":          date_vente,
            "reference":           ref,
            "description":         lot.get("Description", ""),
            "qte_vendue":          qte_vendue,
            "prix_achat_unitaire": pa_unitaire,
            "prix_vente":          prix_vente,
            "marge_brute":         marge_brute,
            "frais_plateforme":    frais_plateforme,
            "frais_transport":     frais_transport,
            "resultat_net":        resultat_net,
            "plateforme":          plateforme,
            "statut":              "vendu"
        }
    }
    ok_vente = await _envoyer_make(payload_vente)

    # 3. Décrémenter dans Airtable
    nouvelle_qte   = max(0, qte_actuelle - qte_vendue)
    nouvelle_vendue = qte_deja_vendue + qte_vendue

    patch_fields = {
        "Quantite totale":  nouvelle_qte,
        "Quantite vendue":  nouvelle_vendue,
        "Prix vente":       prix_vente,
        "Plateforme vente": plateforme,
        "Frais plateforme": frais_plateforme,
        "Frais transport":  frais_transport,
        "Date vente":       now.strftime("%Y-%m-%d"),
    }

    # 4. Si qté = 0 → archiver le produit complet + supprimer Airtable
    archive_produit = False
    suppression_ok  = False

    if nouvelle_qte == 0:
        patch_fields["Statut"] = "vendu"
        await _patch_lot(record_id, patch_fields)

        # Archiver dans Sheets PRODUITS ARCHIVÉS
        payload_produit = {
            "event": "produit_archiver",
            "data": {
                "reference":          ref,
                "description":        lot.get("Description", ""),
                "prix_achat_total":   lot.get("Prix achat total", 0),
                "prix_achat_unitaire": pa_unitaire,
                "qte_totale":         lot.get("Quantite totale", 0),
                "qte_vendue":         nouvelle_vendue,
                "prix_vente":         prix_vente,
                "date_achat":         lot.get("Date achat", ""),
                "date_vente":         date_vente,
                "plateforme":         plateforme,
                "frais_plateforme":   frais_plateforme,
                "frais_transport":    frais_transport,
                "source":             lot.get("Source", ""),
                "statut":             "vendu",
                "notes":              lot.get("Notes", ""),
                "annonce_generee":    lot.get("Annonce générée", "")[:200],
            }
        }
        archive_produit = await _envoyer_make(payload_produit)

        # Supprimer Airtable UNIQUEMENT si archivage confirmé
        if archive_produit:
            suppression_ok = await _supprimer_lot(record_id)
            logger.info(f"✅ Lot {ref} archivé + supprimé Airtable")
        else:
            logger.error(f"Lot {ref} : archivage Sheets échoué — suppression Airtable annulée")
    else:
        # Lot partiel — juste décrémenter
        patch_fields["Statut"] = "en ligne"
        await _patch_lot(record_id, patch_fields)

    return {
        "ok":              True,
        "ref":             ref,
        "description":     lot.get("Description", ""),
        "qte_vendue":      qte_vendue,
        "qte_restante":    nouvelle_qte,
        "prix_vente":      prix_vente,
        "marge_brute":     marge_brute,
        "resultat_net":    resultat_net,
        "lot_solde":       nouvelle_qte == 0,
        "archive_vente":   ok_vente,
        "archive_produit": archive_produit,
        "supprime_at":     suppression_ok,
    }


async def sync_ventes_vers_sheets() -> dict:
    """
    Synchronise toutes les ventes finalisées (statut=vendu) vers Google Sheets.
    Utile pour la migration initiale depuis Airtable.
    Retourne un résumé de la synchronisation.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={
                    "filterByFormula": "{Statut}=\"vendu\"",
                    "maxRecords": 200,
                    "sort[0][field]": "Date vente",
                    "sort[0][direction]": "desc"
                }
            )
        records = resp.json().get("records", [])
    except Exception as e:
        logger.error(f"sync_ventes_vers_sheets: {e}")
        return {"ok": False, "erreur": str(e)}

    ok_count = 0
    for r in records:
        f = r["fields"]
        pv = float(f.get("Prix vente") or 0)
        pa = float(f.get("Prix achat unitaire") or 0)
        frais_pf = float(f.get("Frais plateforme") or 0) or round(pv * 0.13, 2)
        frais_tr = float(f.get("Frais transport") or 0)
        date_v = f.get("Date vente", "")
        if date_v:
            try:
                date_v = datetime.strptime(date_v[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                pass

        payload = {
            "event": "produit_archiver",
            "data": {
                "reference":           f.get("Référence gestion", "?"),
                "description":         f.get("Description", ""),
                "prix_achat_total":    f.get("Prix achat total", 0),
                "prix_achat_unitaire": pa,
                "qte_totale":          f.get("Quantite totale", 0),
                "qte_vendue":          f.get("Quantite vendue", 0),
                "prix_vente":          pv,
                "date_achat":          f.get("Date achat", ""),
                "date_vente":          date_v,
                "plateforme":          f.get("Plateforme vente", ""),
                "frais_plateforme":    frais_pf,
                "frais_transport":     frais_tr,
                "source":              f.get("Source", ""),
                "statut":              "vendu",
                "notes":               f.get("Notes", ""),
                "annonce_generee":     (f.get("Annonce générée", "") or "")[:200],
            }
        }
        if await _envoyer_make(payload):
            ok_count += 1

    return {
        "ok": True,
        "total": len(records),
        "archives": ok_count,
        "echecs": len(records) - ok_count
    }
