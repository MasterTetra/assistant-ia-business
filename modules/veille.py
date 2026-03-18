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
import anthropic

logger = logging.getLogger(__name__)
def _paris_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Paris")
    except Exception:
        try:
            from backports.zoneinfo import ZoneInfo
            return ZoneInfo("Europe/Paris")
        except Exception:
            import datetime
            return datetime.timezone.utc

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

PROMPT_VEILLE_REGLEMENTAIRE = (
    "Tu es expert fiscal SAS france. Veille reglementaire {mois} {annee}.\n"
    "Recherche ce qui impacte une SAS de revente occasion : TVA marge Art.297A, IS PME, DAC7, obligations plateformes.\n"
    "Reponds en JSON strict : [{{\"date\":\"{mois}/{annee}\",\"type\":\"TVA|IS|LEGAL\",\"sujet\":\"titre\",\"resume\":\"2 phrases\",\"impact\":\"HIGH|MEDIUM|LOW\",\"action\":\"action\",\"source\":\"source\"}}]\n"
    "Si rien de notable : []. JSON seul."
)

PROMPT_VEILLE_TECHNO = (
    "Tu es expert optimisation business revente occasion. Veille techno {mois} {annee}.\n"
    "Cherche outils IA pricing/annonces, logiciels SAS TVA marge, automatisation eBay/Vinted.\n"
    "Score = impact x pertinence x facilite / 3. Garder score >= 6.\n"
    "Reponds en JSON strict : [{{\"date\":\"{mois}/{annee}\",\"type\":\"IA|OUTIL\",\"sujet\":\"titre\",\"resume\":\"2 phrases\",\"impact\":\"HIGH|MEDIUM|LOW\",\"applicable\":\"OUI|NON\",\"action\":\"action\",\"source\":\"source\",\"score\":8.5}}]\n"
    "Si rien : []. JSON seul."
)


# ── FONCTIONS PRINCIPALES ─────────────────────────────────────────────────────

async def _appel_claude_web(prompt: str) -> list:
    """Appel Claude (sans web search pour éviter rate limit), retourne une liste d'items JSON."""
    try:
        r = _get_client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
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
    import os as _os
    webhook = _os.getenv("MAKE_WEBHOOK_SHEETS", "")
    secret = _os.getenv("WEBHOOK_SECRET", "cashbert-secret-2026")
    if not webhook or not items:
        logger.warning(f"_envoyer_make_veille: webhook vide ou items vides ({event})")
        return
    try:
        payload = {
            "secret": secret,
            "event": event,
            "date_veille": date_veille,
            "items": items,
        }
        logger.info(f"📤 Envoi Make.com {event} — {len(items)} item(s)")
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(webhook, json=payload)
        logger.info(f"✅ Make.com {event} → {resp.status_code}")
    except Exception as e:
        logger.error(f"_envoyer_make_veille {event}: {e}")


async def generer_veille_mensuelle():
    """
    Génère et envoie la veille mensuelle complète :
    - Veille réglementaire
    - Veille technique/technologique
    Envoi Telegram + archivage Google Sheets via Make.com
    """
    now = datetime.now(_paris_tz())
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
    # Toujours envoyer une ligne bilan pour la traçabilité
    if not items_reg:
        items_reg = [{"date": date_veille, "type": "BILAN", "sujet": f"Veille {mois_label} {now.year}",
                      "resume": "Aucune nouveaute significative ce mois.", "impact": "LOW",
                      "action": "RAS", "source": "Cashbert Auto"}]
    if not items_tech:
        items_tech = [{"date": date_veille, "type": "BILAN", "sujet": f"Veille techno {mois_label} {now.year}",
                       "resume": "Aucune nouveaute pertinente ce mois.", "impact": "LOW",
                       "applicable": "NON", "action": "RAS", "source": "Cashbert Auto", "score": 0}]

    await _envoyer_make_veille("veille_reglementaire", items_reg, date_veille)
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
