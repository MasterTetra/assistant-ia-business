"""
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
STATUTS = ["acheté", "en transport", "en stockage", "en rénovation", "en ligne", "vendu", "expédié", "livré"]

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
    """Génère la prochaine référence interne (REF-YYYY-NNNN)."""
    year = datetime.now().year
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            params = {
                "filterByFormula": f"FIND('{year}', {{Référence}})",
                "fields[]": ["Référence"],
                "sort[0][field]": "Référence",
                "sort[0][direction]": "desc",
                "maxRecords": 1
            }
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params=params
            )
            if resp.status_code == 200:
                records = resp.json().get("records", [])
                if records:
                    last_ref = records[0]["fields"].get("Référence", "")
                    # REF-2025-0047 → extraire 47
                    parts = last_ref.split("-")
                    if len(parts) == 3:
                        num = int(parts[2]) + 1
                        return f"REF-{year}-{num:04d}"
    except Exception:
        pass
    return f"REF-{year}-0001"


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
                         "vendu": "✅", "expédié": "🚚", "livré": "🏠",
                         "en transport": "🚛", "en rénovation": "🔧"}.get(statut, "•")
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

async def update_status(ref: str, new_status: str) -> str:
    """Met à jour le statut d'un produit."""
    if new_status not in STATUTS:
        return f"⚠️ Statut invalide. Valeurs possibles: {', '.join(STATUTS)}"

    try:
        # Trouver l'ID Airtable par référence
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS,
                params={"filterByFormula": f"{{Référence}}='{ref}'", "maxRecords": 1}
            )

        records = resp.json().get("records", [])
        if not records:
            return f"⚠️ Produit {ref} non trouvé."

        record_id = records[0]["id"]
        update_fields = {"Statut": new_status}

        # Si vendu : enregistrer date vente + plateforme
        if new_status == "vendu":
            update_fields["Date vente"] = datetime.now().strftime("%Y-%m-%d")
            if plateforme:
                update_fields["Plateforme vente"] = plateforme

        # Si livré, libérer l'emplacement
        if new_status == "livré":
            update_fields["Date vente"] = datetime.now().strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS,
                json={"fields": update_fields}
            )

        if resp.status_code == 200:
            return f"✅ Statut de `{ref}` mis à jour : *{new_status}*"
        return f"⚠️ Erreur mise à jour: {resp.status_code}"

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


async def update_annonce(ref: str, annonce: str, etat: str = "") -> bool:
    """Met à jour l'annonce générée et le statut dans Airtable."""
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
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS,
                json={"fields": fields}
            )
        return resp.status_code == 200
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
