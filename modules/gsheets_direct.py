"""
MODULE GOOGLE SHEETS DIRECT — Accès via Service Account
Utilise google-auth pour l'authentification (plus simple que JWT manuel).
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


def _get_credentials() -> dict:
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON non configuré")
    try:
        return json.loads(raw)
    except Exception as e:
        raise ValueError(f"GOOGLE_SERVICE_ACCOUNT_JSON invalide: {e}")


async def _get_access_token() -> str:
    """Obtient un token OAuth2 via le Service Account (JWT RS256)."""
    import time as _time
    import base64
    import json as _json

    creds = _get_credentials()
    private_key_pem = creds["private_key"]
    client_email = creds["client_email"]
    scope = "https://www.googleapis.com/auth/spreadsheets"
    token_url = "https://oauth2.googleapis.com/token"

    now = int(_time.time())

    # Construire le JWT
    header_b64 = base64.urlsafe_b64encode(
        _json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=")

    payload_b64 = base64.urlsafe_b64encode(
        _json.dumps({
            "iss": client_email,
            "scope": scope,
            "aud": token_url,
            "exp": now + 3600,
            "iat": now
        }).encode()
    ).rstrip(b"=")

    signing_input = header_b64 + b"." + payload_b64

    # Signer avec RSA-SHA256 via cryptography
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend

        private_key = serialization.load_pem_private_key(
            private_key_pem.encode(), password=None, backend=default_backend()
        )
        signature = base64.urlsafe_b64encode(
            private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        ).rstrip(b"=")
        logger.info("✅ JWT signé avec cryptography")

    except ImportError:
        # Fallback : utiliser google-auth
        logger.warning("cryptography non disponible, essai google-auth...")
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests

            creds_obj = service_account.Credentials.from_service_account_info(
                creds,
                scopes=["https://www.googleapis.com/auth/spreadsheets"]
            )
            request = google.auth.transport.requests.Request()
            creds_obj.refresh(request)
            logger.info("✅ Token obtenu via google-auth")
            return creds_obj.token
        except ImportError:
            raise ImportError("Ni cryptography ni google-auth disponibles. Installez l'un des deux.")

    jwt = (signing_input + b"." + signature).decode()

    async with httpx.AsyncClient(timeout=15) as http:
        resp = await http.post(token_url, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt
        })

    logger.info(f"Token request status: {resp.status_code}")
    if resp.status_code != 200:
        logger.error(f"Token error: {resp.text[:200]}")
        raise ValueError(f"Token OAuth2 échoué: {resp.status_code} {resp.text[:100]}")

    token = resp.json().get("access_token", "")
    if not token:
        raise ValueError(f"Token vide dans réponse: {resp.text[:200]}")
    return token


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
            logger.error(f"Sheets API erreur: {resp.text[:300]}")
            return {}

        rows = resp.json().get("values", [])
        snapshot = {}
        for row in rows:
            if len(row) < 3:
                continue
            ref = row[0].strip()
            if not ref:
                continue
            try:
                qte = int(row[2]) if row[2].strip().isdigit() else 0
            except Exception:
                qte = 0
            snapshot[ref] = {
                "description": row[1] if len(row) > 1 else "",
                "qte": qte,
                "statut": row[3] if len(row) > 3 else "",
                "prix_vente": float(row[4].replace(",", ".")) if len(row) > 4 and row[4].strip() else 0,
            }
        logger.info(f"✅ Snapshot lu: {len(snapshot)} records")
        return snapshot
    except Exception as e:
        logger.error(f"lire_snapshot: {e}", exc_info=True)
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
        logger.info(f"ecrire_snapshot: token OK, {len(records)} records à écrire")
        now = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")

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

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url_clear = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/SNAPSHOT!A1:F1000:clear"
        url_write = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/SNAPSHOT!A1?valueInputOption=RAW"

        async with httpx.AsyncClient(timeout=20) as http:
            r_clear = await http.post(url_clear, headers=headers)
            logger.info(f"Sheets clear: {r_clear.status_code}")
            if r_clear.status_code not in (200, 201):
                logger.error(f"Clear error: {r_clear.text[:200]}")

            resp = await http.put(url_write, headers=headers, json={"values": values})
            logger.info(f"Sheets write: {resp.status_code}")
            if resp.status_code not in (200, 201):
                logger.error(f"Write error: {resp.text[:300]}")

        ok = resp.status_code in (200, 201)
        logger.info(f"{"✅" if ok else "❌"} Snapshot écrit: {len(records)} records")
        return ok
    except Exception as e:
        logger.error(f"ecrire_snapshot: {e}", exc_info=True)
        return False
