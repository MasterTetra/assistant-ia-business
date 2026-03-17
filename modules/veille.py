"""
MODULE VEILLE — Réglementaire + Technique/Technologique
Fréquence : mensuelle (1er du mois)
Envoi : topic Audit (598)
"""
import os
import httpx
import logging
import json as _json
import re as _re
from datetime import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

import anthropic

logger = logging.getLogger(__name__)
PARIS_TZ = ZoneInfo("Europe/Paris")

ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL       = "claude-sonnet-4-20250514"
TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
SUPERGROUP_ID      = -1003827598521
TOPIC_AUDIT        = 598
MAKE_WEBHOOK_SHEETS = os.getenv("MAKE_WEBHOOK_SHEETS", "")
WEBHOOK_SECRET     = os.getenv("WEBHOOK_SECRET", "cashbert-secret-2026")

def _get_client():
    """Client Anthropic créé à la demande (évite crash si clé absente au démarrage)."""
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

# ── PROMPTS ───────────────────────────────────────────────────────────────────

PROMPT_VEILLE_REGLEMENTAIRE = """
Tu es un expert juridique et fiscal spécialisé dans les SAS françaises de revente d'occasion.

Effectue une veille réglementaire pour le mois de {mois} {annee}.

Recherche et analyse les évolutions sur ces sujets UNIQUEMENT liés à l'activité :
1. TVA sur marge (Art.297A CGI) — jurisprudence, modifications, précisions BOFiP
2. Obligations déclaratives plateformes numériques (DAC7, directive UE)
3. IS PME/SAS — taux, seuils, nouveautés
4. Réglementation e-commerce et marketplaces en France
5. Obligations légales revente occasion (TRACFIN si applicable)
6. CFE, cotisations sociales dirigeant SAS
7. Seuils TVA, franchise en base

FILTRE STRICT : uniquement ce qui impacte directement une SAS de revente d'occasion française.

Pour chaque point trouvé, réponds en JSON strict :
[
  {{
    "date": "{mois}/{annee}",
    "type": "TVA|IS|LEGAL|PLATEFORME|SOCIAL|AUTRE",
    "sujet": "titre court (max 60 caractères)",
    "resume": "description précise en 2 phrases",
    "impact": "HIGH|MEDIUM|LOW",
    "action": "action concrète à prendre",
    "source": "nom de la source (BOFiP, Légifrance, etc.)"
  }}
]

Si aucune nouveauté significative ce mois : retourne [].
Retourne UNIQUEMENT le JSON, sans texte avant ou après.
"""

PROMPT_VEILLE_TECHNO = """
Tu es un expert en optimisation business et technologie pour la revente d'occasion.

Effectue une veille technologique pour le mois de {mois} {annee} sur ces sujets :

1. NOUVEAUTÉS IA applicables au business de revente :
   - Outils IA de pricing automatique
   - Vision IA pour identification/estimation objets
   - Chatbots et automatisation relation client
   - Outils IA de génération d'annonces

2. FISCALITÉ SAS / TVA MARGE :
   - Nouveaux logiciels comptables adaptés SAS revente
   - Outils de calcul TVA marge automatisé
   - Solutions de reporting fiscal automatisé

FILTRE STRICT : pertinence directe avec la revente d'occasion + SAS française.
Score chaque item : (impact × pertinence × facilité_mise_en_place) / 3
Ne garder que score ≥ 6/10.

Format JSON strict :
[
  {{
    "date": "{mois}/{annee}",
    "type": "IA|FISCAL|OUTIL|AUTOMATISATION",
    "sujet": "titre court (max 60 caractères)",
    "resume": "description en 2 phrases",
    "impact": "HIGH|MEDIUM|LOW",
    "applicable": "OUI|NON|PARTIEL",
    "action": "action concrète",
    "source": "source",
    "score": 8.5
  }}
]

Si aucune nouveauté pertinente : retourne [].
Retourne UNIQUEMENT le JSON, sans texte avant ou après.
"""


# ── FONCTIONS PRINCIPALES ─────────────────────────────────────────────────────

async def _appel_claude_web(prompt: str) -> list:
    """Appel Claude avec web search, retourne une liste d'items JSON."""
    try:
        r = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        raw = ""
        for block in r.content:
            if getattr(block, "type", "") == "text" and getattr(block, "text", ""):
                raw += block.text

        # Extraire le JSON
        m = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if not m:
            return []
        items = _json.loads(m.group(0))
        return items if isinstance(items, list) else []
    except Exception as e:
        logger.error(f"_appel_claude_web: {e}")
        return []


async def _envoyer_telegram(message: str):
    """Envoie un message dans le topic Audit."""
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            await http.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id": SUPERGROUP_ID,
                    "message_thread_id": TOPIC_AUDIT,
                    "text": message,
                    "parse_mode": "Markdown"
                }
            )
    except Exception as e:
        logger.error(f"_envoyer_telegram veille: {e}")


async def _envoyer_make_veille(event: str, items: list, date_veille: str):
    """Envoie les items vers Make.com pour archivage Google Sheets."""
    if not MAKE_WEBHOOK_SHEETS or not items:
        return
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            await http.post(MAKE_WEBHOOK_SHEETS, json={
                "secret": WEBHOOK_SECRET,
                "event": event,
                "date_veille": date_veille,
                "items": items,
            })
    except Exception as e:
        logger.error(f"_envoyer_make_veille: {e}")


async def generer_veille_mensuelle():
    """
    Génère et envoie la veille mensuelle complète :
    - Veille réglementaire
    - Veille technique/technologique
    Envoi Telegram + archivage Google Sheets via Make.com
    """
    now = datetime.now(PARIS_TZ)
    MOIS_FR = {1:"Janvier",2:"Février",3:"Mars",4:"Avril",5:"Mai",6:"Juin",
               7:"Juillet",8:"Août",9:"Septembre",10:"Octobre",11:"Novembre",12:"Décembre"}
    mois_label = MOIS_FR[now.month]
    date_veille = now.strftime("%d/%m/%Y")

    logger.info(f"🔍 Veille mensuelle — {mois_label} {now.year}")

    # ── 1. Veille réglementaire ───────────────────────────────────────────────
    prompt_reg = PROMPT_VEILLE_REGLEMENTAIRE.format(mois=mois_label, annee=now.year)
    items_reg = await _appel_claude_web(prompt_reg)

    # ── 2. Veille techno ─────────────────────────────────────────────────────
    prompt_tech = PROMPT_VEILLE_TECHNO.format(mois=mois_label, annee=now.year)
    items_tech = await _appel_claude_web(prompt_tech)

    # ── 3. Archivage Google Sheets ────────────────────────────────────────────
    if items_reg:
        await _envoyer_make_veille("veille_reglementaire", items_reg, date_veille)
    if items_tech:
        await _envoyer_make_veille("veille_techno", items_tech, date_veille)

    # ── 4. Message Telegram ───────────────────────────────────────────────────
    await _construire_et_envoyer_message(
        items_reg, items_tech, mois_label, now.year, date_veille
    )


async def _construire_et_envoyer_message(
    items_reg: list, items_tech: list,
    mois: str, annee: int, date_veille: str
):
    """Construit et envoie le résumé Telegram dans le topic Audit."""

    lignes = [
        f"🔍 *VEILLE MENSUELLE — {mois} {annee}*",
        f"📅 {date_veille}",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    # Section réglementaire
    lignes.append("\n📋 *RÉGLEMENTAIRE*")
    if not items_reg:
        lignes.append("_Aucune nouveauté significative ce mois_")
    else:
        high_reg = [i for i in items_reg if i.get("impact") == "HIGH"]
        autres_reg = [i for i in items_reg if i.get("impact") != "HIGH"]
        if high_reg:
            lignes.append("🔴 *Priorité HIGH :*")
            for item in high_reg[:3]:
                lignes.append(f"  • *{item.get('sujet', '')}*")
                lignes.append(f"    _{item.get('resume', '')}_")
                if item.get("action"):
                    lignes.append(f"    ✅ {item['action']}")
        if autres_reg:
            lignes.append(f"🟡 {len(autres_reg)} point(s) MEDIUM/LOW → voir Google Sheets")

    # Section techno
    lignes.append("\n🤖 *TECHNOLOGIE & IA*")
    if not items_tech:
        lignes.append("_Aucune nouveauté pertinente ce mois_")
    else:
        high_tech = [i for i in items_tech if i.get("impact") == "HIGH"]
        autres_tech = [i for i in items_tech if i.get("impact") != "HIGH"]
        if high_tech:
            lignes.append("🔴 *À tester en priorité :*")
            for item in high_tech[:3]:
                lignes.append(f"  • *{item.get('sujet', '')}*")
                lignes.append(f"    _{item.get('resume', '')}_")
                if item.get("action"):
                    lignes.append(f"    ✅ {item['action']}")
        if autres_tech:
            lignes.append(f"🟡 {len(autres_tech)} outil(s) à explorer → voir Google Sheets")

    lignes.append("\n━━━━━━━━━━━━━━━━━━━━")
    nb_total = len(items_reg) + len(items_tech)
    lignes.append(f"_Total : {nb_total} point(s) analysé(s) — détail complet dans Google Sheets_")

    message = "\n".join(lignes)

    # Découper si trop long
    if len(message) > 4000:
        await _envoyer_telegram(message[:4000])
        await _envoyer_telegram(message[4000:8000])
    else:
        await _envoyer_telegram(message)
