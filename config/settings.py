"""
Configuration centrale — lit les variables d'environnement
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── TOKENS OBLIGATOIRES ───────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─── AIRTABLE ──────────────────────────────────────────────
AIRTABLE_API_KEY = os.getenv("AIRTABLE_API_KEY", "")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID", "")

# Noms des tables Airtable
TABLE_PRODUITS  = "Produits"
TABLE_VENTES    = "Ventes"
TABLE_FINANCES  = "Finances"
TABLE_STOCK     = "Stock"

# ─── CLOUDINARY (stockage photos) ─────────────────────────
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY    = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

# ─── PLATEFORMES DE VENTE (optionnel, pour publication auto) ─
EBAY_APP_ID         = os.getenv("EBAY_APP_ID", "")
EBAY_CERT_ID        = os.getenv("EBAY_CERT_ID", "")
EBAY_DEV_ID         = os.getenv("EBAY_DEV_ID", "")
EBAY_USER_TOKEN     = os.getenv("EBAY_USER_TOKEN", "")

# ─── SÉCURITÉ ─────────────────────────────────────────────
# Liste des IDs Telegram autorisés (laisser vide = tout le monde)
# Exemple: AUTHORIZED_USERS = [123456789, 987654321]
_raw = os.getenv("AUTHORIZED_USERS", "")
AUTHORIZED_USERS = [int(x.strip()) for x in _raw.split(",") if x.strip().isdigit()]

# ─── ENTREPÔT : définition des emplacements ───────────────
# Format : Étagère X — Niveau Y — Zone Z
WAREHOUSE_CONFIG = {
    "etageres": 10,    # Nombre d'étagères
    "niveaux": 5,      # Niveaux par étagère
    "zones": ["A", "B", "C", "D"],  # Zones
}

# ─── RÈGLES BUSINESS ──────────────────────────────────────
# Frais de plateforme (%)
PLATFORM_FEES = {
    "ebay":      13.0,
    "vinted":     5.0,
    "leboncoin":  3.5,
    "facebook":   0.0,
}

# Marge minimum acceptable pour achat (%)
MIN_MARGIN_PERCENT = 40.0

# ─── MODÈLE IA ────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS = 2000
