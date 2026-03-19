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


async def _get_lot_by_description(titre_mail: str) -> dict | None:
    """
    Cherche un lot actif par titre.
    Stratégie en cascade :
    1. Cherche dans Description (correspondance exacte partielle)
    2. Cherche dans Annonce générée (titre du mail = début de l'annonce)
    3. Cherche par mots-clés (premiers mots du titre)
    """
    # Nettoyer le titre : enlever les préfixes eBay courants
    titre = titre_mail.strip()
    for prefix in ["Re: ", "Fwd: ", "eBay - ", "eBay: "]:
        if titre.startswith(prefix):
            titre = titre[len(prefix):]

    # Extraire les 40 premiers caractères significatifs
    titre_court = titre[:40].strip()
    # Premiers mots clés (3 premiers mots)
    mots = [m for m in titre.split()[:4] if len(m) > 2]

    try:
        async with httpx.AsyncClient(timeout=20) as http:

            # Tentative 1 : Description
            formule = (
                f'AND('
                f'FIND(LOWER("{titre_court.lower()}"), LOWER({{Description}}))>0,'
                f'OR({{Statut}}="en ligne",{{Statut}}="acheté")'
                f')'
            )
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={"filterByFormula": formule, "maxRecords": 1,
                        "sort[0][field]": "Référence gestion",
                        "sort[0][direction]": "asc"}
            )
            records = resp.json().get("records", [])
            if records:
                r = records[0]
                logger.info(f"✅ Lot trouvé par Description: {r['fields'].get('Référence gestion')}")
                return {"record_id": r["id"], **r["fields"]}

            # Tentative 2 : Annonce générée
            formule2 = (
                f'AND('
                f'FIND(LOWER("{titre_court.lower()}"), LOWER({{Annonce générée}}))>0,'
                f'OR({{Statut}}="en ligne",{{Statut}}="acheté")'
                f')'
            )
            resp2 = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={"filterByFormula": formule2, "maxRecords": 1,
                        "sort[0][field]": "Référence gestion",
                        "sort[0][direction]": "asc"}
            )
            records2 = resp2.json().get("records", [])
            if records2:
                r = records2[0]
                logger.info(f"✅ Lot trouvé par Annonce générée: {r['fields'].get('Référence gestion')}")
                return {"record_id": r["id"], **r["fields"]}

            # Tentative 3 : mots-clés
            if mots:
                conditions = " ".join([
                    f'FIND(LOWER("{m.lower()}"), LOWER({{Description}}))>0'
                    for m in mots[:3]
                ])
                formule3 = (
                    f'AND('
                    f'OR({{Statut}}="en ligne",{{Statut}}="acheté"),'
                    f'AND({conditions})'
                    f')'
                )
                resp3 = await http.get(
                    f"{AIRTABLE_URL}/{TABLE}",
                    headers=HEADERS_AT,
                    params={"filterByFormula": formule3, "maxRecords": 1,
                            "sort[0][field]": "Référence gestion",
                            "sort[0][direction]": "asc"}
                )
                records3 = resp3.json().get("records", [])
                if records3:
                    r = records3[0]
                    logger.info(f"✅ Lot trouvé par mots-clés: {r['fields'].get('Référence gestion')}")
                    return {"record_id": r["id"], **r["fields"]}

        logger.warning(f"❌ Aucun lot trouvé pour: {titre_court}")
        return None

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


# ── SYNC INTELLIGENT QUOTIDIEN ────────────────────────────────────────────────

async def sync_intelligent() -> dict:
    """
    Sync quotidien 23h59 :
    1. Lit snapshot précédent depuis Google Sheets
    2. Lit Airtable actuel
    3. Détecte changements de quantité (ventes hors ligne)
    4. Archive les ventes manquantes dans Sheets VENTES
    5. Si qté = 0 → archive PRODUITS ARCHIVÉS + supprime Airtable
    6. Met à jour le SNAPSHOT
    7. Corrige Description = titre de l'Annonce générée si différent

    Retourne un résumé des opérations.
    """
    from modules.gsheets_direct import lire_snapshot, ecrire_snapshot

    now = datetime.now(PARIS_TZ)
    date_str = now.strftime("%d/%m/%Y")
    resultats = {"ventes_detectees": 0, "archives": 0, "corrections_desc": 0, "erreurs": 0}

    # 1. Lire snapshot précédent
    snapshot = await lire_snapshot()

    # 2. Lire Airtable actuel
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={
                    "fields[]": [
                        "Référence gestion", "Description", "Statut",
                        "Quantite totale", "Quantite vendue", "Prix achat unitaire",
                        "Prix achat total", "Prix vente", "Date achat", "Date vente",
                        "Source", "Plateforme vente", "Frais plateforme",
                        "Frais transport", "Notes", "Annonce générée"
                    ],
                    "maxRecords": 200,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc"
                }
            )
        at_records = resp.json().get("records", [])
    except Exception as e:
        logger.error(f"sync_intelligent fetch Airtable: {e}")
        return {"erreur": str(e)}

    # 3. Comparer et détecter changements
    for r in at_records:
        f = r["fields"]
        ref = f.get("Référence gestion", "")
        record_id = r["id"]
        if not ref:
            continue

        qte_actuelle = int(f.get("Quantite totale") or 0)
        qte_vendue_at = int(f.get("Quantite vendue") or 0)
        statut = f.get("Statut", "")
        description = f.get("Description", "")
        annonce = f.get("Annonce générée", "") or ""
        pv = float(f.get("Prix vente") or 0)
        pa = float(f.get("Prix achat unitaire") or 0)
        plateforme = f.get("Plateforme vente", "") or "Hors ligne"
        frais_pf = float(f.get("Frais plateforme") or 0)
        frais_tr = float(f.get("Frais transport") or 0)

        # 3a. Correction Description si différent du titre Annonce générée
        if annonce:
            # Extraire le titre de l'annonce (première ligne non vide)
            titre_annonce = ""
            for ligne in annonce.split("\n"):
                ligne = ligne.strip()
                # Ignorer les préfixes courants
                for prefix in ["TITRE:", "Titre:", "Title:", "**", "##"]:
                    if ligne.startswith(prefix):
                        ligne = ligne[len(prefix):].strip()
                if ligne and len(ligne) > 5:
                    titre_annonce = ligne[:100]
                    break

            if titre_annonce and titre_annonce != description and len(titre_annonce) > 5:
                try:
                    async with httpx.AsyncClient(timeout=15) as http:
                        await http.patch(
                            f"{AIRTABLE_URL}/{TABLE}/{record_id}",
                            headers=HEADERS_AT,
                            json={"fields": {"Description": titre_annonce}}
                        )
                    logger.info(f"✅ Description corrigée: {ref} → {titre_annonce[:40]}")
                    resultats["corrections_desc"] += 1
                    description = titre_annonce
                except Exception as e:
                    logger.error(f"Correction description {ref}: {e}")

        # 3b. Détecter ventes hors ligne via snapshot
        statut_snapshot = snapshot.get(ref, {}).get("statut", "") if ref in snapshot else ""
        qte_snapshot = snapshot[ref]["qte"] if ref in snapshot else None

        # Cas 1 : quantité diminuée
        diff_qte = 0
        if qte_snapshot is not None:
            diff_qte = qte_snapshot - qte_actuelle

        # Cas 2 : statut passé à "vendu" sans changement de qté (lignes unitaires)
        statut_vendu_detecte = (
            statut == "vendu"
            and statut_snapshot not in ("vendu", "")
            and diff_qte == 0
            and qte_snapshot is not None
        )

        # Nombre d'unités vendues à archiver
        nb_a_archiver = diff_qte
        if statut_vendu_detecte:
            nb_a_archiver = int(qte_actuelle) if qte_actuelle > 0 else 1

        if nb_a_archiver > 0 or statut_vendu_detecte:
            logger.info(f"📦 Vente hors ligne détectée: {ref} — {nb_a_archiver} unité(s)")
            resultats["ventes_detectees"] += nb_a_archiver

            marge_brute = round((pv - pa), 2) if pv and pa else 0
            resultat_net = round(marge_brute - frais_pf - frais_tr, 2)

            # Archiver chaque unité vendue
            for i in range(max(nb_a_archiver, 1)):
                payload_vente = {
                    "event": "vente_archiver",
                    "data": {
                        "date_vente": date_str,
                        "reference": ref,
                        "description": description[:60],
                        "qte_vendue": 1,
                        "prix_achat_unitaire": pa,
                        "prix_vente": pv,
                        "marge_brute": marge_brute,
                        "frais_plateforme": frais_pf,
                        "frais_transport": frais_tr,
                        "resultat_net": resultat_net,
                        "plateforme": plateforme or "Hors ligne",
                        "statut": "vendu"
                    }
                }
                ok = await _envoyer_make(payload_vente)
                if ok:
                    resultats["archives"] += 1
                else:
                    resultats["erreurs"] += 1

        # 3c. Si qté = 0 OU statut vendu → archiver + supprimer Airtable
        if (qte_actuelle == 0 or statut == "vendu") and statut_snapshot not in ("vendu", ""):
            logger.info(f"🏁 Lot soldé détecté: {ref}")
            await _patch_lot(record_id, {"Statut": "vendu"})

            payload_produit = {
                "event": "produit_archiver",
                "data": {
                    "reference": ref,
                    "description": description,
                    "prix_achat_total": f.get("Prix achat total", 0),
                    "prix_achat_unitaire": pa,
                    "qte_totale": f.get("Quantite totale", 0),
                    "qte_vendue": qte_vendue_at,
                    "prix_vente": pv,
                    "date_achat": f.get("Date achat", ""),
                    "date_vente": date_str,
                    "plateforme": plateforme,
                    "frais_plateforme": frais_pf,
                    "frais_transport": frais_tr,
                    "source": f.get("Source", ""),
                    "statut": "vendu",
                    "notes": f.get("Notes", ""),
                    "annonce_generee": annonce[:200],
                }
            }
            archive_ok = await _envoyer_make(payload_produit)
            if archive_ok:
                await _supprimer_lot(record_id)
                logger.info(f"✅ {ref} archivé + supprimé Airtable")

    # 4. Mettre à jour le snapshot avec l'état actuel
    records_actifs = [r["fields"] for r in at_records if r["fields"].get("Statut") not in ("vendu",)]
    await ecrire_snapshot(records_actifs)
    logger.info(f"✅ Snapshot mis à jour: {len(records_actifs)} records actifs")

    return resultats


async def traiter_articles_vendus() -> dict:
    """
    Scanne Airtable et traite immédiatement :
    - Articles avec statut "vendu"
    - Articles avec Quantite totale = 0
    Archive dans Sheets VENTES + PRODUITS ARCHIVÉS + supprime Airtable.
    """
    resultats = {"traites": 0, "archives_ventes": 0, "supprimes": 0, "erreurs": 0}
    now = datetime.now(PARIS_TZ)
    date_str = now.strftime("%d/%m/%Y")

    try:
        async with httpx.AsyncClient(timeout=30) as http:
            # Récupérer tous les articles vendus ou soldés
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE}",
                headers=HEADERS_AT,
                params={
                    "filterByFormula": 'OR({Statut}="vendu", {Quantite totale}=0)',
                    "fields[]": [
                        "Référence gestion", "Description", "Statut",
                        "Quantite totale", "Quantite vendue",
                        "Prix achat unitaire", "Prix achat total",
                        "Prix vente", "Date achat", "Date vente",
                        "Source", "Plateforme vente", "Frais plateforme",
                        "Frais transport", "Notes", "Annonce générée"
                    ],
                    "maxRecords": 200,
                }
            )
        records = resp.json().get("records", [])
        logger.info(f"traiter_articles_vendus: {len(records)} article(s) à traiter")
    except Exception as e:
        logger.error(f"traiter_articles_vendus fetch: {e}")
        return {"erreur": str(e)}

    for r in records:
        f = r["fields"]
        record_id = r["id"]
        ref = f.get("Référence gestion", "?")
        description = f.get("Description", "")
        statut = f.get("Statut", "")
        qte_totale = int(f.get("Quantite totale") or 0)
        qte_vendue = int(f.get("Quantite vendue") or 0)
        pa = float(f.get("Prix achat unitaire") or 0)
        pv = float(f.get("Prix vente") or 0)
        plateforme = f.get("Plateforme vente") or "Hors ligne"
        frais_pf = float(f.get("Frais plateforme") or 0)
        frais_tr = float(f.get("Frais transport") or 0)
        date_vente = f.get("Date vente", "") or date_str

        # Formater la date — utiliser la date Airtable si disponible, sinon aujourd'hui
        if date_vente and len(date_vente) >= 10 and "-" in date_vente:
            try:
                from datetime import datetime as dt
                date_vente = dt.strptime(date_vente[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                date_vente = date_str
        elif not date_vente:
            date_vente = date_str

        marge_brute = round((pv - pa) * max(qte_vendue, 1), 2) if pv and pa else 0
        resultat_net = round(marge_brute - frais_pf - frais_tr, 2)

        resultats["traites"] += 1

        # 1. Archiver dans Sheets VENTES (une ligne par unité vendue)
        nb_ventes = max(qte_vendue, 1)
        for i in range(nb_ventes):
            payload_vente = {
                "event": "vente_archiver",
                "data": {
                    "date_vente": date_vente,
                    "reference": ref,
                    "description": description[:60],
                    "qte_vendue": 1,
                    "prix_achat_unitaire": pa,
                    "prix_vente": pv,
                    "marge_brute": round((pv - pa), 2) if pv and pa else 0,
                    "frais_plateforme": round(frais_pf / nb_ventes, 2) if nb_ventes > 0 else frais_pf,
                    "frais_transport": round(frais_tr / nb_ventes, 2) if nb_ventes > 0 else frais_tr,
                    "resultat_net": round(resultat_net / nb_ventes, 2),
                    "plateforme": plateforme,
                    "statut": "vendu"
                }
            }
            ok = await _envoyer_make(payload_vente)
            if ok:
                resultats["archives_ventes"] += 1
            else:
                resultats["erreurs"] += 1

        # 2. Supprimer de Airtable (ventes archivées dans VENTES)
        ok_del = await _supprimer_lot(record_id)
        if ok_del:
            resultats["supprimes"] += 1
            logger.info(f"✅ {ref} archivé dans VENTES + supprimé Airtable")
        else:
            logger.error(f"❌ {ref} archivé dans VENTES mais suppression Airtable échouée")

    return resultats

