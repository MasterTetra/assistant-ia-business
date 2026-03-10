"""
MODULE SOURCING
──────────────────────────────────────────────────────────
Analyse une photo d'objet et retourne une analyse complète
du marché : prix, marge estimée, recommandation d'achat.
"""
import anthropic
import httpx
import base64
import re
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, MIN_MARGIN_PERCENT

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SOURCING_PROMPT = """Tu es un expert en achat-revente d'objets d'occasion avec 20 ans d'expérience.
Tu analyses des objets pour un revendeur professionnel français.

CONTEXTE SUPPLÉMENTAIRE FOURNI PAR L'UTILISATEUR : {caption}

Analyse cet objet et fournis une estimation de marché RÉALISTE basée sur tes connaissances des plateformes françaises (eBay.fr, Vinted, Leboncoin, Facebook Marketplace).

Réponds EXACTEMENT dans ce format Telegram (avec les emojis) :

🔎 *OBJET IDENTIFIÉ*
[Nom précis, marque si visible, époque estimée, état apparent]

💰 *PRIX DU MARCHÉ*
• Prix bas observé : XXX€
• Prix moyen : XXX€  
• Prix haut : XXX€

✅ *RECOMMANDATION*
• Prix de revente conseillé : XXX€
• Prix d'achat maximum : XXX€
• Marge estimée : XX% (si acheté au prix max)

📈 *DEMANDE*
[FORTE / MOYENNE / FAIBLE] — [explication courte]

🏆 *MEILLEURES PLATEFORMES*
[eBay / Vinted / LBC / Facebook selon l'objet]

⚡ *CONSEIL*
[1 phrase d'avis pratique : acheter ou passer, pourquoi]

---
Sois précis et réaliste. Si tu ne peux pas identifier l'objet avec certitude, indique-le clairement."""

async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    """
    Analyse une photo depuis Telegram et retourne l'analyse marché.
    photo_url : URL du fichier Telegram (accessible publiquement temporairement)
    """
    try:
        # Télécharger l'image depuis Telegram
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")

        # Déterminer le type MIME
        content_type = resp.headers.get("content-type", "image/jpeg")
        if "png" in content_type:
            media_type = "image/png"
        elif "webp" in content_type:
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"

        prompt = SOURCING_PROMPT.format(
            caption=caption if caption else "Aucun contexte supplémentaire"
        )

        # Appel à Claude Vision
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ],
                }
            ],
        )

        analysis = response.content[0].text

        # Extraire le prix d'achat max pour ajouter une recommendation colorée
        max_buy = _extract_max_buy_price(analysis)
        if max_buy:
            footer = f"\n\n💡 *Seuil décision : {max_buy}€ maximum*"
            analysis += footer

        return analysis

    except anthropic.APIError as e:
        return f"⚠️ Erreur API Claude : {str(e)}"
    except httpx.HTTPError as e:
        return f"⚠️ Impossible de télécharger la photo : {str(e)}"
    except Exception as e:
        return f"⚠️ Erreur inattendue : {str(e)}"


def _extract_max_buy_price(text: str) -> float | None:
    """Extrait le prix d'achat maximum depuis la réponse."""
    patterns = [
        r"Prix d'achat maximum\s*:\s*(\d+(?:[.,]\d+)?)\s*€",
        r"achat max(?:imum)?\s*:\s*(\d+(?:[.,]\d+)?)\s*€",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return float(m.group(1).replace(",", "."))
    return None
