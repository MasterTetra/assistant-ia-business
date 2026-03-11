"""
MODULE SOURCING — recherche web ciblée eBay, LBC, Catawiki
"""
import anthropic
import httpx
import base64
import re
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


STEP1_PROMPT = """Regarde cette photo et identifie l'objet en UNE seule ligne.
Format : [Marque/Artiste] [type objet] [matiere] [epoque] [etat]
Exemple : Vase Daum verre grave Art Nouveau signe bon etat
Informations supplementaires : {caption}
Reponds avec UNE seule ligne, rien d'autre."""


STEP2_PROMPT = """Tu es un expert en achat-revente d'objets d'occasion.
Objet : {objet}

Cherche les prix actuels de cet objet sur ebay.com, leboncoin.fr et catawiki.com en utilisant web_search.
Fais exactement 2 recherches :
1. "{objet} prix"
2. "{objet} vendre"

Apres tes recherches, reponds avec UNIQUEMENT ce bloc de texte, sans rien d'autre avant ou apres :

---DEBUT---
OBJET: {objet}
ANNONCES:
[liste chaque annonce trouvee : site | prix | etat]
PRIX BAS: [chiffre]
PRIX MOYEN: [chiffre]
PRIX HAUT: [chiffre]
REVENTE: [chiffre]
ACHAT MAX: [chiffre]
MARGE: [chiffre]
DEMANDE: [FORTE ou MOYENNE ou FAIBLE]
RAISON: [une phrase]
PLATEFORMES: [liste]
CONSEIL: [une phrase]
---FIN---"""


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    try:
        # Telecharger la photo
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")
        media_type = "image/png" if "png" in resp.headers.get("content-type","") else "image/jpeg"

        # ETAPE 1 : Identification de l'objet (sans web search, rapide)
        r1 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                    {"type": "text", "text": STEP1_PROMPT.format(caption=caption or "aucune")}
                ]
            }]
        )
        objet = r1.content[0].text.strip().split("\n")[0]

        # ETAPE 2 : Recherche web + analyse prix
        r2 = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": STEP2_PROMPT.format(objet=objet)
            }]
        )

        # Extraire le dernier bloc texte
        raw = ""
        for block in r2.content:
            if hasattr(block, "text") and block.text:
                raw = block.text

        # Extraire uniquement ce qui est entre ---DEBUT--- et ---FIN---
        match = re.search(r'---DEBUT---(.*?)---FIN---', raw, re.DOTALL)
        if match:
            data = match.group(1).strip()
            return _build_message(data, objet)
        else:
            # Fallback : parser le texte brut quand meme
            return _build_message(raw, objet)

    except anthropic.APIError as e:
        return f"Erreur API Claude : {str(e)}"
    except Exception as e:
        return f"Erreur : {str(e)}"


def _extract(text: str, key: str) -> str:
    """Extrait la valeur d'une cle dans le texte structure."""
    pattern = rf'{key}\s*:\s*(.+)'
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1).strip() if m else "?"


def _extract_annonces(text: str) -> str:
    """Extrait le bloc annonces."""
    m = re.search(r'ANNONCES\s*:\s*\n(.*?)(?=PRIX BAS|PRIX MOYEN|\Z)', text, re.DOTALL | re.IGNORECASE)
    if m:
        lines = [l.strip() for l in m.group(1).strip().split("\n") if l.strip()]
        return "\n".join(f"  • {l}" for l in lines[:5])
    return "  • Voir plateformes recommandees"


def _build_message(data: str, objet: str) -> str:
    """Construit le message final propre pour Telegram."""
    # Nettoyer tout markdown
    data = re.sub(r'\*\*(.+?)\*\*', r'\1', data)
    data = re.sub(r'\*(.+?)\*', r'\1', data)
    data = re.sub(r'_(.+?)_', r'\1', data)
    data = re.sub(r'#{1,6}\s+', '', data)

    objet_final = _extract(data, "OBJET") 
    if objet_final == "?":
        objet_final = objet

    annonces = _extract_annonces(data)
    prix_bas = _extract(data, "PRIX BAS")
    prix_moyen = _extract(data, "PRIX MOYEN")
    prix_haut = _extract(data, "PRIX HAUT")
    revente = _extract(data, "REVENTE")
    achat_max = _extract(data, "ACHAT MAX")
    marge = _extract(data, "MARGE")
    demande = _extract(data, "DEMANDE")
    raison = _extract(data, "RAISON")
    plateformes = _extract(data, "PLATEFORMES")
    conseil = _extract(data, "CONSEIL")

    # Extraire le chiffre du prix max pour le seuil
    nums = re.findall(r'\d+', achat_max)
    seuil = nums[0] if nums else "?"

    msg = f"""🔎 OBJET IDENTIFIE
{objet_final}

🌐 ANNONCES TROUVEES (eBay / LBC / Catawiki)
{annonces}

💰 PRIX DU MARCHE
• Prix bas : {prix_bas} euros
• Prix moyen : {prix_moyen} euros
• Prix haut : {prix_haut} euros

✅ RECOMMANDATION
• Prix de revente conseille : {revente} euros
• Prix achat maximum : {achat_max} euros
• Marge estimee : {marge}%

📈 DEMANDE
{demande} - {raison}

🏆 MEILLEURES PLATEFORMES
{plateformes}

⚡ CONSEIL
{conseil}

💡 Seuil decision : {seuil} euros maximum"""

    return msg.strip()
