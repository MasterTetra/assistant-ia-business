import asyncio
"""  # v2.1 — update_etat_lot avec préservation Notes
MODULE STOCK
──────────────────────────────────────────────────────────
Gestion complète de l'inventaire via Airtable.
Création de fiches, attribution d'emplacements, suivi statuts.
"""
import httpx
import json
from datetime import datetime
from config.settings import (
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID,
    TABLE_PRODUITS, WAREHOUSE_CONFIG
)

AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

# Statuts valides dans l'ordre du cycle de vie
STATUTS = [
    "acheté",
    "en ligne",
    "en cours d'expédition",
    "livré",
    "vendu",
    "en stockage",
    "en rénovation",
]

# Alias pour faciliter la saisie Telegram (insensible à la casse/accents)
STATUTS_ALIAS = {
    "achete":           "acheté",
    "acheté":           "acheté",
    "en ligne":         "en ligne",
    "enligne":          "en ligne",
    "expedition":       "en cours d'expédition",
    "expedie":          "en cours d'expédition",
    "expédié":          "en cours d'expédition",
    "en cours":         "en cours d'expédition",
    "en cours d'expédition": "en cours d'expédition",
    "livre":            "livré",
    "livré":            "livré",
    "vendu":            "vendu",
    "stockage":         "en stockage",
    "en stockage":      "en stockage",
    "renovation":       "en rénovation",
    "rénovation":       "en rénovation",
    "en rénovation":    "en rénovation",
    "en renovation":    "en rénovation",
    "retour":           "retour en cours",
    "retour en cours":  "retour en cours",
}

# ─────────────────────────────────────────────
#  ATTRIBUTION D'EMPLACEMENT AUTOMATIQUE
# ─────────────────────────────────────────────

async def get_next_location() -> str:
    """Retourne le prochain emplacement libre dans l'entrepôt."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            # Récupérer tous les produits en stockage avec leur emplacement
            params = {
                "filterByFormula": "AND({Statut}!='livré', {Statut}!='vendu', {Emplacement}!='')",
                "fields[]": ["Emplacement"],
                "maxRecords": 500
            }
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params=params
            )
            occupied = set()
            if resp.status_code == 200:
                data = resp.json()
                for rec in data.get("records", []):
                    loc = rec.get("fields", {}).get("Emplacement", "")
                    if loc:
                        occupied.add(loc)
    except Exception:
        occupied = set()

    # Trouver le premier emplacement libre
    cfg = WAREHOUSE_CONFIG
    for etagere in range(1, cfg["etageres"] + 1):
        for niveau in range(1, cfg["niveaux"] + 1):
            for zone in cfg["zones"]:
                loc = f"Étagère {etagere} — Niveau {niveau} — Zone {zone}"
                if loc not in occupied:
                    return loc

    return "Entrepôt complet — assigner manuellement"


# ─────────────────────────────────────────────
#  GÉNÉRATION DE RÉFÉRENCE PRODUIT
# ─────────────────────────────────────────────

async def get_next_ref() -> str:
    """
    Génère la prochaine référence gestion au format AV-YYYYMMDD-NNNN.
    Le numéro est TOUJOURS croissant — même si des lignes sont supprimées.
    Stratégie : cherche le max dans Airtable ET dans Google Sheets VENTES,
    puis incrémente de 1.
    """
    from datetime import datetime as dt
    today = dt.now().strftime("%Y%m%d")
    max_num = 0

    # 1. Chercher dans Airtable (articles actifs)
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "fields[]": ["Référence gestion"],
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "desc",
                    "maxRecords": 1
                }
            )
            if resp.status_code == 200:
                records = resp.json().get("records", [])
                if records:
                    ref = records[0]["fields"].get("Référence gestion", "")
                    # Format AV-YYYYMMDD-NNNN
                    parts = ref.split("-")
                    if len(parts) == 3 and parts[0] == "AV":
                        try:
                            num = int(parts[2])
                            max_num = max(max_num, num)
                        except ValueError:
                            pass
    except Exception:
        pass

    # 2. Chercher dans Google Sheets VENTES (articles archivés/supprimés)
    try:
        from modules.gsheets_direct import _get_access_token
        import os
        sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        if sheet_id:
            token = await _get_access_token()
            url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/VENTES!B2:B10000"
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
            if resp.status_code == 200:
                rows = resp.json().get("values", [])
                for row in rows:
                    if row:
                        ref = row[0].strip()
                        parts = ref.split("-")
                        if len(parts) == 3 and parts[0] == "AV":
                            try:
                                num = int(parts[2])
                                max_num = max(max_num, num)
                            except ValueError:
                                pass
    except Exception:
        pass

    next_num = max_num + 1
    return f"AV-{today}-{next_num:04d}"


# ─────────────────────────────────────────────
#  CRÉER UNE FICHE PRODUIT
# ─────────────────────────────────────────────

async def create_product(photos: list, prix_achat: float, source: str, description: str = "") -> str:
    """
    Crée une fiche produit dans Airtable.
    Attribue automatiquement une référence et un emplacement.
    """
    ref = await get_next_ref()
    location = await get_next_location()
    now = datetime.now().strftime("%Y-%m-%d")

    fields = {
        "Référence": ref,
        "Référence gestion": ref,
        "Date achat": now,
        "Prix achat unitaire": prix_achat,
        "Source": source,
        "Statut": "acheté",
        "Emplacement": location,
        "Nombre de photos": len(photos),
        "Description": description,
    }

    payload = {"records": [{"fields": fields}]}

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                json=payload
            )

        if resp.status_code in (200, 201):
            return (
                f"✅ *Fiche produit créée !*\n\n"
                f"📋 Référence : `{ref}`\n"
                f"💰 Prix d'achat : *{prix_achat:.2f}€*\n"
                f"📍 Emplacement : *{location}*\n"
                f"📸 Photos : {len(photos)}\n"
                f"🏷️ Source : {source}\n"
                f"📅 Date : {datetime.now().strftime('%d/%m/%Y')}\n\n"
                f"➡️ Utilise `/annonce {ref}` pour générer l'annonce de vente."
            )
        else:
            error = resp.json()
            return f"⚠️ Erreur Airtable ({resp.status_code}): {error.get('error', {}).get('message', str(resp.text))}"

    except Exception as e:
        return f"⚠️ Erreur connexion Airtable: {str(e)}"


# ─────────────────────────────────────────────
#  RÉSUMÉ DU STOCK
# ─────────────────────────────────────────────

async def get_stock_summary() -> str:
    """Retourne un résumé complet du stock actuel."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={"maxRecords": 500, "fields[]": ["Référence", "Statut", "Prix achat unitaire", "Prix vente", "Source", "Emplacement"]}
            )

        if resp.status_code != 200:
            return f"⚠️ Erreur Airtable: {resp.status_code}"

        records = resp.json().get("records", [])

        # Comptage par statut
        by_status = {}
        total_investi = 0.0
        total_vente_potentielle = 0.0

        for rec in records:
            f = rec.get("fields", {})
            statut = f.get("Statut", "inconnu")
            by_status[statut] = by_status.get(statut, 0) + 1
            total_investi += f.get("Prix achat unitaire", 0) or 0
            total_vente_potentielle += f.get("Prix vente", 0) or 0

        total = len(records)
        actif = total - by_status.get("livré", 0) - by_status.get("vendu", 0)

        lines = [
            "📦 *ÉTAT DU STOCK*\n",
            f"📊 Total produits : *{total}*",
            f"🟢 Actifs (non vendus) : *{actif}*",
            f"💰 Capital immobilisé : *{total_investi:.2f}€*",
            "",
            "📋 *Par statut :*",
        ]
        for statut in STATUTS:
            count = by_status.get(statut, 0)
            if count > 0:
                emoji = {"acheté": "🛒", "en stockage": "📦", "en ligne": "🌐",
                         "vendu": "✅", "en cours d'expédition": "🚚", "livré": "🏠",
                         "en transport": "🚛", "en rénovation": "🔧", "retour en cours": "↩️"}.get(statut, "•")
                lines.append(f"  {emoji} {statut.capitalize()} : {count}")

        if total_vente_potentielle > 0:
            marge_pot = total_vente_potentielle - total_investi
            lines += ["", f"💹 Valeur vente potentielle : *{total_vente_potentielle:.2f}€*",
                      f"📈 Marge potentielle : *{marge_pot:.2f}€*"]

        return "\n".join(lines)

    except Exception as e:
        return f"⚠️ Erreur: {str(e)}"


# ─────────────────────────────────────────────
#  CHERCHER UN PRODUIT
# ─────────────────────────────────────────────

async def find_product(query: str) -> str:
    """Recherche un produit par référence ou nom."""
    try:
        # Formule Airtable : cherche dans la référence ou le nom
        formula = f"OR(FIND(LOWER('{query.lower()}'), LOWER({{Référence}})), FIND(LOWER('{query.lower()}'), LOWER({{Nom}})))"

        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={"filterByFormula": formula, "maxRecords": 5}
            )

        if resp.status_code != 200:
            return f"⚠️ Erreur Airtable: {resp.status_code}"

        records = resp.json().get("records", [])

        if not records:
            return f"🔍 Aucun produit trouvé pour : *{query}*"

        lines = [f"🔍 *{len(records)} résultat(s) pour '{query}' :*\n"]
        for rec in records:
            f = rec.get("fields", {})
            ref = f.get("Référence", "?")
            nom = f.get("Nom", "Sans nom")
            statut = f.get("Statut", "?")
            loc = f.get("Emplacement", "Non attribué")
            prix_a = f.get("Prix achat unitaire", 0)
            prix_v = f.get("Prix vente", 0)
            lines += [
                f"━━━━━━━━━━━━━━━",
                f"📋 `{ref}` — {nom}",
                f"📍 *{loc}*",
                f"🏷️ Statut : {statut}",
                f"💰 Achat : {prix_a:.2f}€" + (f" | Vente : {prix_v:.2f}€" if prix_v else ""),
            ]

        return "\n".join(lines)

    except Exception as e:
        return f"⚠️ Erreur: {str(e)}"


# ─────────────────────────────────────────────
#  METTRE À JOUR LE STATUT
# ─────────────────────────────────────────────

async def update_status(ref: str, new_status_raw: str, plateforme: str = "") -> str:
    """
    Met à jour le statut d'un produit.
    Accepte les alias (expedie, livre, vendu, etc.)
    Enregistre automatiquement date vente et plateforme si applicable.
    Cherche par Référence gestion (ex: AV-20260316-0001) ou Référence (ex: REF-0001).
    """
    # Résoudre l'alias
    new_status = STATUTS_ALIAS.get(new_status_raw.lower().strip())
    if not new_status:
        # Essayer correspondance partielle
        for alias, statut in STATUTS_ALIAS.items():
            if new_status_raw.lower() in alias:
                new_status = statut
                break
    if not new_status:
        return (
            f"⚠️ Statut `{new_status_raw}` non reconnu.\n\n"
            f"Statuts disponibles :\n"
            + "\n".join(f"  • `{s}`" for s in STATUTS)
        )

    try:
        async with httpx.AsyncClient(timeout=20) as http:
            # Chercher d'abord par Référence gestion (format AV-YYYYMMDD-NNNN)
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": f"{{Référence gestion}}='{ref}'",
                    "maxRecords": 1
                }
            )
            records = resp.json().get("records", [])

            # Fallback : chercher par Référence classique
            if not records:
                resp = await http.get(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                    headers=HEADERS,
                    params={
                        "filterByFormula": f"{{Référence}}='{ref}'",
                        "maxRecords": 1
                    }
                )
                records = resp.json().get("records", [])

        if not records:
            return f"⚠️ Produit `{ref}` non trouvé dans Airtable."

        record_id = records[0]["id"]
        old_status = records[0].get("fields", {}).get("Statut", "?")
        update_fields = {"Statut": new_status}

        # Date vente automatique pour les statuts de transaction
        if new_status in ("en cours d'expédition", "livré", "vendu"):
            if not records[0].get("fields", {}).get("Date vente"):
                update_fields["Date vente"] = datetime.now().strftime("%Y-%m-%d")

        # Plateforme si fournie
        if plateforme:
            update_fields["Plateforme vente"] = plateforme

        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS,
                json={"fields": update_fields}
            )

        if resp.status_code != 200:
            return f"⚠️ Erreur Airtable: {resp.status_code} — {resp.text[:100]}"

        fields_record = records[0].get("fields", {})
        qte_totale = int(fields_record.get("Quantite totale") or 1)
        qte_vendue = int(fields_record.get("Quantite vendue") or 0)

        # ── Actions selon le nouveau statut ──────────────────────────────────

        # Si vendu : décrémenter quantités
        if new_status == "vendu":
            nouvelle_qte_totale = max(0, qte_totale - 1)
            nouvelle_qte_vendue = qte_vendue + 1
            qte_fields = {
                "Quantite totale": nouvelle_qte_totale,
                "Quantite vendue": nouvelle_qte_vendue,
            }
            # Si lot soldé → garder statut vendu, sinon repasser en ligne
            if nouvelle_qte_totale > 0:
                qte_fields["Statut"] = "en ligne"  # Reste en ligne si stock restant
            async with httpx.AsyncClient(timeout=20) as http:
                await http.patch(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                    headers=HEADERS,
                    json={"fields": qte_fields}
                )
            # Déclencher archivage de la vente
            try:
                from modules.archive import traiter_vente
                pa = float(fields_record.get("Prix achat unitaire") or 0)
                pv = float(fields_record.get("Prix vente") or 0)
                ref_gestion = fields_record.get("Référence gestion", ref)
                if pv > 0:
                    asyncio.create_task(traiter_vente(
                        ref=ref_gestion, qte_vendue=1,
                        prix_vente=pv, plateforme=plateforme or "Hors ligne"
                    ))
            except Exception as e:
                logger.warning(f"Archivage vente {ref}: {e}")

        # Si retour après livraison → remettre en cours d'expédition
        elif new_status == "en cours d'expédition" and old_status == "livré":
            logger.info(f"↩️ Retour détecté: {ref} livré → en cours d'expédition")

        emoji_map = {
            "acheté": "🛒", "en ligne": "🟢", "en cours d'expédition": "📬",
            "livré": "📦", "vendu": "✅", "en stockage": "🏭", "en rénovation": "🔧", "retour en cours": "↩️"
        }
        emoji = emoji_map.get(new_status, "🔄")

        # Message contextuel selon transition
        qte_totale_new = int(records[0].get("fields", {}).get("Quantite totale") or qte_totale)
        msg = f"{emoji} *{ref}* : `{old_status}` → `{new_status}`"
        if plateforme:
            msg += f" ({plateforme})"
        if new_status == "vendu" and qte_totale > 1:
            restant = max(0, qte_totale - 1)
            msg += f"\n📦 Restant en stock : {restant} unité(s)"
        if new_status == "en cours d'expédition":
            msg += "\n📬 _Pense à marquer 'livré' quand le colis arrive_"
        if new_status == "livré":
            msg += "\n📦 _Attente confirmation acheteur → `/statut {ref} vendu` ou retour_"
        if old_status == "livré" and new_status == "en cours d'expédition":
            msg += "\n↩️ _Retour en cours — récupère le colis et décide de la suite_"

        return msg

    except Exception as e:
        return f"⚠️ Erreur: {str(e)}"


async def get_product_by_ref(ref: str) -> dict:
    """Retourne les données d'un produit Airtable sous forme de dict flux-compatible."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": f"{{Référence gestion}}='{ref}'",
                    "maxRecords": 1
                }
            )
        records = resp.json().get("records", [])
        if not records:
            return None
        f = records[0]["fields"]
        return {
            "objet": f.get("Description", ref),
            "caption": f.get("Description", ""),
            "prix_revente": f.get("Prix vente", 0),
            "prix_achat_unitaire": f.get("Prix achat unitaire", 0),
            "prix_moyen": f.get("Prix vente", 0),
            "demande": "MOYENNE",
            "vitesse": "NORMALE",
            "score": None,
            "titre": "",
            "description": "",
            "mots_cles": "",
        }
    except Exception as e:
        logger.error(f"get_product_by_ref error: {e}")
        return None


async def update_annonce(ref: str, annonce: str, etat: str = "", prix_vente: float = None) -> bool:
    """Met à jour l'annonce générée, le statut, l'état et le prix dans Airtable."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": f"{{Référence gestion}}='{ref}'",
                    "maxRecords": 1
                }
            )
        records = resp.json().get("records", [])
        if not records:
            return False
        record_id = records[0]["id"]
        fields = {
            "Annonce générée": annonce,
            "Statut": "en ligne",
        }
        if etat:
            fields["Notes"] = f"État : {etat}"
        if prix_vente is not None and prix_vente > 0:
            fields["Prix vente"] = round(float(prix_vente), 2)
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS,
                json={"fields": fields}
            )
        ok = resp.status_code == 200
        if not ok:
            logger.error(f"update_annonce {ref}: {resp.status_code} | {resp.text[:200]}")
        return ok
    except Exception as e:
        logger.error(f"update_annonce error: {e}")
        return False


async def get_produits_achetes() -> list:
    """Retourne tous les produits avec statut 'acheté' — en attente de listing."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": "{Statut}='acheté'",
                    "fields[]": ["Référence gestion", "Description", "Prix achat unitaire", "Prix vente", "Date achat"],
                    "maxRecords": 100,
                    "sort[0][field]": "Date achat",
                    "sort[0][direction]": "desc"
                }
            )
        records = resp.json().get("records", [])
        return [
            {
                "ref": r["fields"].get("Référence gestion", "?"),
                "description": r["fields"].get("Description", "")[:50],
                "prix_achat": r["fields"].get("Prix achat unitaire", 0),
                "prix_vente": r["fields"].get("Prix vente", 0),
                "date": r["fields"].get("Date achat", ""),
            }
            for r in records
        ]
    except Exception as e:
        logger.error(f"get_produits_achetes error: {e}")
        return []


async def get_produits_en_ligne_similaires(titre: str) -> list:
    """
    Retourne tous les articles 'en ligne' dont le titre (Description) est similaire.
    Utilisé pour détecter les lots et gérer la quantité eBay.
    """
    try:
        # Prendre les 4 premiers mots significatifs du titre comme clé de recherche
        mots = [m for m in titre.split() if len(m) > 2][:4]
        cle = " ".join(mots[:3]) if mots else titre[:20]
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": f"AND({{Statut}}='en ligne', FIND(LOWER('{cle.lower()}'), LOWER({{Description}}))>0)",
                    "fields[]": ["Référence gestion", "Description", "Prix vente",
                                 "Photos URLs", "eBay Item ID", "Prix achat unitaire"],
                    "maxRecords": 200,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc"
                }
            )
        records = resp.json().get("records", [])
        return [
            {
                "ref":          r["fields"].get("Référence gestion", "?"),
                "description":  r["fields"].get("Description", ""),
                "prix_vente":   r["fields"].get("Prix vente", 0),
                "photos_urls":  r["fields"].get("Photos URLs", ""),
                "ebay_item_id": r["fields"].get("eBay Item ID", ""),
                "prix_achat":   r["fields"].get("Prix achat unitaire", 0),
                "record_id":    r["id"],
            }
            for r in records
        ]
    except Exception as e:
        logger.error(f"get_produits_en_ligne_similaires error: {e}")
        return []


async def marquer_articles_vendus(refs: list, prix_vente: float, plateforme: str = "eBay") -> bool:
    """
    Passe les refs données de 'en ligne' à 'vendu' dans Airtable.
    Met à jour date de vente, plateforme, prix de vente.
    """
    from datetime import datetime
    date_vente = datetime.now().strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            for ref in refs:
                # Trouver le record
                resp = await http.get(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                    headers=HEADERS,
                    params={"filterByFormula": f"{{Référence gestion}}='{ref}'", "maxRecords": 1}
                )
                records = resp.json().get("records", [])
                if not records:
                    continue
                record_id = records[0]["id"]
                await http.patch(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                    headers=HEADERS,
                    json={"fields": {
                        "Statut":           "vendu",
                        "Date vente":       date_vente,
                        "Plateforme vente": plateforme,
                        "Prix vente":       prix_vente,
                    }}
                )
        return True
    except Exception as e:
        logger.error(f"marquer_articles_vendus error: {e}")
        return False


async def get_articles_prets_a_poster() -> list:
    """
    Retourne tous les articles avec statut 'acheté' ET Photos URLs renseignées.
    Ces articles ont toutes les infos nécessaires pour être postés.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": "AND({Statut}='acheté', {Photos URLs}!='')",
                    "fields[]": [
                        "Référence gestion", "Description", "Annonce générée",
                        "Prix vente", "Prix achat unitaire", "Photos URLs",
                        "Nombre de photos", "eBay Item ID"
                    ],
                    "maxRecords": 200,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc"
                }
            )
        records = resp.json().get("records", [])
        return [
            {
                "ref":           r["fields"].get("Référence gestion", "?"),
                "description":   r["fields"].get("Description", ""),
                "annonce":       r["fields"].get("Annonce générée", ""),
                "prix_vente":    r["fields"].get("Prix vente", 0),
                "prix_achat":    r["fields"].get("Prix achat unitaire", 0),
                "photos_urls":   r["fields"].get("Photos URLs", ""),
                "ebay_item_id":  r["fields"].get("eBay Item ID", ""),
                "record_id":     r["id"],
            }
            for r in records
        ]
    except Exception as e:
        logger.error(f"get_articles_prets_a_poster error: {e}")
        return []





async def update_annonce_airtable(record_id: str, annonce: str) -> bool:
    """Met à jour le champ 'Annonce générée' d'un record Airtable par son ID."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS,
                json={"fields": {"Annonce générée": annonce}}
            )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"update_annonce_airtable error: {e}")
        return False


async def get_articles_pour_annonce() -> list:
    """
    Pour /annonce : récupère tous les articles statut 'acheté' avec tous leurs champs.
    Détecte les lots : articles STRICTEMENT identiques sur
    (description, prix_achat_unitaire, prix_vente, quantite_totale, date_achat)
    ET dont les références gestion se suivent dans le tableur.
    Retourne une liste de groupes : chaque groupe = 1 annonce à créer.
    """
    fields = [
        "Référence gestion", "Description", "Prix achat unitaire",
        "Prix achat total", "Quantite totale", "Prix vente",
        "Date achat", "Statut", "Photos URLs", "Annonce générée",
        "Notes", "Nombre de photos",
    ]
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": "{Statut}='acheté'",
                    "fields[]": fields,
                    "maxRecords": 200,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc",
                }
            )
        records = resp.json().get("records", [])
    except Exception as e:
        logger.error(f"get_articles_pour_annonce error: {e}")
        return []

    articles = []
    for r in records:
        f = r["fields"]
        articles.append({
            "ref":        f.get("Référence gestion", "?"),
            "record_id":  r["id"],
            "description":    f.get("Description", ""),
            "prix_achat_u":   float(f.get("Prix achat unitaire") or 0),
            "prix_achat_tot": float(f.get("Prix achat total") or 0),
            "qte_totale":     int(f.get("Quantite totale") or 1),
            "prix_vente":     float(f.get("Prix vente") or 0),
            "date_achat":     f.get("Date achat", ""),
            "photos_urls":    f.get("Photos URLs", ""),
            "annonce":        f.get("Annonce générée", ""),
            "notes":          f.get("Notes", ""),
            "nb_photos":      f.get("Nombre de photos", 0),
        })

    if not articles:
        return []

    # ── Détection des lots ────────────────────────────────────────────────────
    # Clé d'identité stricte : description + prix_achat_u + prix_vente + qte_totale + date_achat
    def cle_identite(a):
        return (
            a["description"].strip().lower(),
            round(a["prix_achat_u"], 4),
            round(a["prix_vente"], 2),
            a["qte_totale"],
            a["date_achat"],
        )

    # Extraire le numéro de séquence depuis la ref (AV-20260316-NNNN → NNNN)
    import re as _re
    def seq_ref(ref):
        m = _re.search(r"-(\d+)$", ref)
        return int(m.group(1)) if m else 0

    groupes = []
    traites = set()

    for i, art in enumerate(articles):
        if art["ref"] in traites:
            continue
        cle = cle_identite(art)
        seq_i = seq_ref(art["ref"])

        # Chercher tous les articles avec la même clé ET refs consécutives
        groupe = [art]
        traites.add(art["ref"])

        for j, autre in enumerate(articles):
            if autre["ref"] in traites:
                continue
            if cle_identite(autre) == cle:
                seq_j = seq_ref(autre["ref"])
                # Vérifier que la séquence est consécutive au groupe actuel
                seqs_groupe = [seq_ref(a["ref"]) for a in groupe]
                if seq_j == max(seqs_groupe) + 1 or seq_j == min(seqs_groupe) - 1:
                    groupe.append(autre)
                    traites.add(autre["ref"])

        groupes.append({
            "refs":       [a["ref"] for a in groupe],
            "record_ids": [a["record_id"] for a in groupe],
            "description": art["description"],
            "prix_achat_u": art["prix_achat_u"],
            "prix_vente":   art["prix_vente"],
            "qte_totale":   art["qte_totale"],
            "date_achat":   art["date_achat"],
            "photos_urls":  next((a["photos_urls"] for a in groupe if a["photos_urls"]), ""),
            "annonce":      next((a["annonce"] for a in groupe if a["annonce"]), ""),
            "notes":        art["notes"],
            "nb_photos":    art["nb_photos"],
            "est_lot":      len(groupe) > 1,
            "quantite_lot": len(groupe),
        })

    return groupes


async def get_articles_prets_a_poster_v2() -> list:
    """
    Pour /post : articles statut 'acheté' avec TOUTES ces colonnes remplies :
    Description, Prix achat unitaire, Quantite totale, Prix vente,
    Date achat, Photos URLs, Nombre de photos, Annonce générée.
    Notes est optionnel.
    Regroupe également les lots identiques + consécutifs.
    """
    fields = [
        "Référence gestion", "Description", "Prix achat unitaire",
        "Quantite totale", "Prix vente", "Date achat", "Statut",
        "Photos URLs", "Nombre de photos", "Annonce générée", "Notes",
    ]
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={
                    "filterByFormula": (
                        "AND({Statut}='acheté',"
                        "{Description}!='',"
                        "{Prix achat unitaire}!='',"
                        "{Quantite totale}!='',"
                        "{Prix vente}!='',"
                        "{Date achat}!='',"
                        "{Photos URLs}!='',"
                        "{Nombre de photos}!='',"
                        "{Annonce générée}!='')"
                    ),
                    "fields[]": fields,
                    "maxRecords": 200,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc",
                }
            )
        records = resp.json().get("records", [])
    except Exception as e:
        logger.error(f"get_articles_prets_a_poster_v2 error: {e}")
        return []

    articles = []
    for r in records:
        f = r["fields"]
        articles.append({
            "ref":         f.get("Référence gestion", "?"),
            "record_id":   r["id"],
            "description": f.get("Description", ""),
            "prix_achat_u":float(f.get("Prix achat unitaire") or 0),
            "qte_totale":  int(f.get("Quantite totale") or 1),
            "prix_vente":  float(f.get("Prix vente") or 0),
            "date_achat":  f.get("Date achat", ""),
            "photos_urls": f.get("Photos URLs", ""),
            "nb_photos":   f.get("Nombre de photos", 0),
            "annonce":     f.get("Annonce générée", ""),
            "notes":       f.get("Notes", ""),
        })

    # Même logique de regroupement par lots
    def cle_id(a):
        return (
            a["description"].strip().lower(),
            round(a["prix_achat_u"], 4),
            round(a["prix_vente"], 2),
            a["qte_totale"],
            a["date_achat"],
        )

    import re as _re
    def seq_ref(ref):
        m = _re.search(r"-(\d+)$", ref)
        return int(m.group(1)) if m else 0

    groupes = []
    traites = set()
    for art in articles:
        if art["ref"] in traites:
            continue
        cle = cle_id(art)
        groupe = [art]
        traites.add(art["ref"])
        for autre in articles:
            if autre["ref"] in traites:
                continue
            if cle_id(autre) == cle:
                seqs = [seq_ref(a["ref"]) for a in groupe]
                sj = seq_ref(autre["ref"])
                if sj == max(seqs) + 1 or sj == min(seqs) - 1:
                    groupe.append(autre)
                    traites.add(autre["ref"])

        groupes.append({
            "refs":        [a["ref"] for a in groupe],
            "record_ids":  [a["record_id"] for a in groupe],
            "description": art["description"],
            "prix_vente":  art["prix_vente"],
            "qte_totale":  art["qte_totale"] * len(groupe),
            "photos_urls": next((a["photos_urls"] for a in groupe if a["photos_urls"]), ""),
            "annonce":     next((a["annonce"] for a in groupe if a["annonce"]), ""),
            "notes":       art["notes"],
            "est_lot":     len(groupe) > 1,
            "quantite_lot": len(groupe),
        })

    return groupes


def grouper_en_lots(articles: list) -> list:
    """
    Regroupe les articles en lots selon titre + annonce similaires.
    Retourne une liste de lots : [{"titre", "annonce", "prix", "photos", "refs", "quantite"}]
    """
    import re as _re

    def cle_lot(art):
        # Extraire le titre depuis l'annonce générée
        titre_match = _re.search(r"TITRE:\s*(.+)", art.get("annonce", ""))
        titre = titre_match.group(1).strip() if titre_match else art["description"][:40]
        return titre.lower().strip()

    lots = {}
    for art in articles:
        cle = cle_lot(art)
        if cle not in lots:
            lots[cle] = {
                "titre":    cle_lot(art),  # sera réécrit ci-dessous avec casse correcte
                "annonce":  art["annonce"],
                "prix":     art["prix_vente"],
                "photos":   art["photos_urls"],
                "refs":     [],
                "quantite": 0,
            }
            # Récupérer le titre avec la casse correcte
            titre_match = __import__("re").search(r"TITRE:\s*(.+)", art.get("annonce", ""))
            if titre_match:
                lots[cle]["titre"] = titre_match.group(1).strip()
            else:
                lots[cle]["titre"] = art["description"][:60]
        lots[cle]["refs"].append(art["ref"])
        lots[cle]["quantite"] += 1
        # Garder le prix le plus récent (tous devraient être identiques dans un lot)
        if art["prix_vente"]:
            lots[cle]["prix"] = art["prix_vente"]
        # Garder les photos si pas encore défini
        if not lots[cle]["photos"] and art["photos_urls"]:
            lots[cle]["photos"] = art["photos_urls"]

    return list(lots.values())


async def update_prix_vente_lot(record_ids: list, prix_vente: float) -> int:
    """Met à jour le Prix vente sur une liste de records Airtable. Retourne le nb de succès."""
    ok = 0
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            for rid in record_ids:
                resp = await http.patch(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{rid}",
                    headers=HEADERS,
                    json={"fields": {"Prix vente": prix_vente}}
                )
                if resp.status_code == 200:
                    ok += 1
    except Exception as e:
        logger.error(f"update_prix_vente_lot error: {e}")
    return ok


async def update_etat_lot(record_ids: list, etat: str) -> int:
    """
    Sauvegarde l'état de l'article dans la colonne Notes pour tout le lot.
    Format dans Airtable : "État : Très bon état"
    Si Notes contient déjà du contenu, l'état est ajouté en début de Notes.
    """
    ok = 0
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            for rid in record_ids:
                # Lire les Notes existantes pour ne pas les écraser
                resp_get = await http.get(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{rid}",
                    headers=HEADERS,
                    params={"fields[]": ["Notes"]}
                )
                notes_existantes = ""
                if resp_get.status_code == 200:
                    notes_existantes = resp_get.json().get("fields", {}).get("Notes", "") or ""

                # Retirer toute mention d'état précédente si elle existe
                import re as _re
                notes_nettoyees = _re.sub(r"État\s*:\s*[^\n]*\n?", "", notes_existantes).strip()

                # Construire les nouvelles Notes : état en premier
                if notes_nettoyees:
                    nouvelles_notes = "État : " + etat + "\n" + notes_nettoyees
                else:
                    nouvelles_notes = "État : " + etat

                resp = await http.patch(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{rid}",
                    headers=HEADERS,
                    json={"fields": {"Notes": nouvelles_notes}}
                )
                if resp.status_code == 200:
                    ok += 1
    except Exception as e:
        logger.error(f"update_etat_lot error: {e}")
    return ok
