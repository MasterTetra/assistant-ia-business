"""
MODULE SOURCING — recherche web en temps réel, optimisé
"""
import anthropic
import httpx
import base64
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SOURCING_PROMPT = """Tu es un expert en achat-revente d'objets d'occasion avec 20 ans d'expérience.

INFORMATIONS SUPPLÉMENTAIRES : {caption}

MISSION EN 2 ÉTAPES :

ÉTAPE 1 — Identifie précisément l'objet sur la photo.

ÉTAPE 2 — Utilise l'outil web_search pour chercher les prix réels de cet objet sur :
Vinted, Leboncoin, eBay France, Etsy, Catawiki, Interenchères, Facebook Marketplace, Drouot

Fais 3 recherches ciblées :
1. "[nom objet] prix vente site:vinted.fr OR site:leboncoin.fr"
2. "[nom objet] prix site:ebay.fr OR site:etsy.com"  
3. "[nom objet] enchères site:catawiki.com OR site:interencheres.com OR site:drouot.com"

Après les recherches, réponds EXACTEMENT dans ce format :

🔎 *OBJET IDENTIFIÉ*
[Nom précis, marque/artiste, époque, matière, état apparent]

🌐 *ANNONCES TROUVÉES*
[Liste 4-6 vraies annonces avec : plateforme — prix — état]

💰 *PRIX DU MARCHÉ* (basé sur les vraies annonces)
• Prix bas observé : XXX€
• Prix moyen : XXX€
• Prix haut : XXX€

✅ *RECOMMANDATION*
• Prix de revente conseillé : XXX€
• Prix d'achat maximum : XXX€
• Marge estimée : XX%

📈 *DEMANDE*
[FORTE / MOYENNE / FAIBLE] — [explication]

🏆 *MEILLEURES PLATEFORMES*
[Selon les résultats trouvés]

⚡ *CONSEIL*
[1 conseil pratique basé sur les vraies données]"""


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    """
    Analyse une photo avec recherche web intégrée.
    Claude fait lui-même les recherches via web_search.
    """
    try:
        # Télécharger la photo
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")

        content_type = resp.headers.get("content-type", "image/jpeg")
        media_type = "image/png" if "png" in content_type else "image/jpeg"

        # Un seul appel Claude avec web_search activé
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2000,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data
                        }
                    },
                    {
                        "type": "text",
                        "text": SOURCING_PROMPT.format(
                            caption=caption if caption else "Aucune information supplémentaire"
                        )
                    }
                ]
            }]
        )

        # Extraire le texte final de la réponse
        final_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                final_text = block.text

        return final_text if final_text else "⚠️ Aucune analyse retournée."

    except anthropic.APIError as e:
        return f"⚠️ Erreur API Claude : {str(e)}"
    except httpx.HTTPError as e:
        return f"⚠️ Impossible de télécharger la photo : {str(e)}"
    except Exception as e:
        return f"⚠️ Erreur : {str(e)}"
