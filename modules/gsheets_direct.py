"""
MODULE GOOGLE SHEETS DIRECT — Accès via Service Account
Lecture/écriture directe sans passer par Make.com.
Utilisé pour : SNAPSHOT, lecture historique ventes.
"""
import os
import json
import logging
import httpx
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
PARIS_TZ = ZoneInfo("Europe/Paris")

SCOPES = "https://www.googleapis.com/auth/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _get_credentials() -> dict:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON non configuré")
    return json.loads(raw)


async def _get_access_token() -> str:
    """Obtient un token OAuth2 via le Service Account."""
    import time, base64, json as _json
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend

    creds = _get_credentials()
    private_key_pem = creds["private_key"]
    client_email = creds["client_email"]

    now = int(time.time())
    header = base64.urlsafe_b64encode(_json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(_json.dumps({
        "iss": client_email,
        "scope": SCOPES,
        "aud": TOKEN_URL,
        "exp": now + 3600,
        "iat": now
    }).encode()).rstrip(b"=")

    signing_input = header + b"." + payload
    private_key = serialization.load_pem_private_key(
        private_key_pem.encode(), password=None, backend=default_backend()
    )
    signature = base64.urlsafe_b64encode(
        private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    ).rstrip(b"=")

    jwt = (signing_input + b"." + signature).decode()

    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(TOKEN_URL, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt
        })
    return resp.json()["access_token"]


async def lire_snapshot() -> dict:
    """
    Lit l'onglet SNAPSHOT et retourne un dict :
    { "REF": {"qte": 59, "statut": "en ligne", "prix_vente": 8.5, "description": "..."} }
    """
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        logger.warning("GOOGLE_SHEET_ID non configuré")
        return {}
    try:
        token = await _get_access_token()
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/SNAPSHOT!A2:F1000"
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
        logger.info(f"Sheets lire_snapshot status: {resp.status_code}")
        if resp.status_code != 200:
            logger.error(f"Sheets API erreur: {resp.text[:200]}")
            return {}
        data = resp.json()
        rows = data.get("values", [])
        snapshot = {}
        for row in rows:
            if len(row) < 3:
                continue
            ref = row[0].strip()
            if not ref:
                continue
            snapshot[ref] = {
                "description": row[1] if len(row) > 1 else "",
                "qte": int(row[2]) if len(row) > 2 and row[2].isdigit() else 0,
                "statut": row[3] if len(row) > 3 else "",
                "prix_vente": float(row[4].replace(",", ".")) if len(row) > 4 and row[4] else 0,
            }
        logger.info(f"✅ Snapshot lu: {len(snapshot)} records")
        return snapshot
    except Exception as e:
        logger.error(f"lire_snapshot: {e}")
        return {}


async def ecrire_snapshot(records: list) -> bool:
    """
    Écrit le snapshot complet dans l'onglet SNAPSHOT.
    records = liste de dicts Airtable fields.
    """
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        logger.warning("ecrire_snapshot: GOOGLE_SHEET_ID manquant")
        return False
    try:
        token = await _get_access_token()
        logger.info(f"ecrire_snapshot: token obtenu OK, sheet_id={sheet_id[:20]}...")
        now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")

        # Header + données
        values = [["Référence", "Description", "Quantite", "Statut", "Prix vente", "Date snapshot"]]
        for f in records:
            values.append([
                f.get("Référence gestion", ""),
                f.get("Description", "")[:80],
                str(f.get("Quantite totale") or 0),
                f.get("Statut", ""),
                str(f.get("Prix vente") or ""),
                now
            ])

        # Effacer puis réécrire
        url_clear = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/SNAPSHOT!A1:F1000:clear"
        url_write = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/SNAPSHOT!A1?valueInputOption=RAW"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=20) as http:
            r_clear = await http.post(url_clear, headers=headers)
            logger.info(f"Sheets clear: {r_clear.status_code}")
            resp = await http.put(url_write, headers=headers, json={"values": values})
            logger.info(f"Sheets write: {resp.status_code}")
            if resp.status_code != 200:
                logger.error(f"Sheets write erreur: {resp.text[:300]}")

        ok = resp.status_code == 200
        logger.info(f"{'✅' if ok else '❌'} Snapshot écrit: {len(records)} records — {resp.status_code}")
        return ok
    except Exception as e:
        logger.error(f"ecrire_snapshot: {e}")
        return False
