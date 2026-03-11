"""
MODULE SOURCING — avec recherche web en temps réel
Analyse une photo + recherche les vrais prix sur les plateformes
"""
import anthropic
import httpx
import base64
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

IDENTIFICATION_PROMPT = """Tu es un expert en objets d'occasion, antiquités et objets de collection.

Analyse cette photo et identifie l'objet avec le maximum de précision.

INFORMATIONS SUPPLÉMENTAIRES FOURNIES : {caption}

Réponds UNIQUEMENT avec une description courte et précise de l'objet pour permettre une recherche sur les sites de vente.
Format : [Marque/Artiste si visible] [Type d'objet] [Matière] [Style/Époque] [Caractéristiques distinctives] [État apparent]

Exemple : "Vase Gallé verre soufflé Art Nouveau signé circa 1900 décor floral bon état"
Exemple : "Lampe César Baldaccini bouteille verre texturé luminaire années 70 électricité à refaire"

Sois précis et concis, maximum 2 lignes."""

ANALYSIS_PROMPT = """Tu es un expert en achat-revente d'objets d'occasion avec 20 ans d'expérience sur les marchés français et européens.

OBJET : {objet}
INFORMATIONS COMPLÉMENTAIRES : {caption}
RÉSULTATS DE RECHERCHE EN TEMPS RÉEL : 
{search_results}

En te basant sur ces vraies annonces trouvées sur les plateformes de vente, analyse le marché et réponds EXACTEMENT dans ce format :

🔎 *OBJET IDENTIFIÉ*
[Nom précis, marque, époque, état]

🌐 *ANNONCES TROUVÉES*
[Résume les vraies annonces trouvées : plateforme, prix, état — 3 à 5 exemples concrets]

💰 *PRIX DU MARCHÉ* (basé sur les vraies annonces)
• Prix bas observé : XXX€
• Prix moyen : XXX€
• Prix haut : XXX€

✅ *RECOMMANDATION*
• Prix de revente conseillé : XXX€
• Prix d'achat maximum : XXX€
• Marge estimée : XX% (si acheté au prix max)

📈 *DEMANDE*
[FORTE / MOYENNE / FAIBLE] — [explication basée sur le nombre d'annonces trouvées]

🏆 *MEILLEURES PLATEFORMES*
[Selon les résultats trouvés]

⚡ *CONSEIL*
[Conseil pratique basé sur les vraies données du marché]"""

SEARCH_QUERIES_PROMPT = """Tu es un expert en recherche d'objets d'occasion sur internet.

Objet identifié : {objet}

Génère 4 requêtes de recherche optimisées pour trouver cet objet sur les sites de vente français et européens.
Ces requêtes seront utilisées pour chercher sur Vinted, Leboncoin, eBay, Etsy, Catawiki.

Réponds UNIQUEMENT avec les 4 requêtes, une par ligne, sans numérotation ni explication.
Les requêtes doivent être courtes et précises (3-6 mots max).

Exemple pour un vase Gallé :
vase Gallé verre Art Nouveau
Gallé vase signé
vase soufflé Art Nouveau ancien
Émile Gallé verrerie"""


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    """
    Analyse une photo avec recherche web en temps réel.
    1. Identifie l'objet via Claude Vision
    2. Génère des requêtes de recherche
    3. Cherche les vraies annonces via web_search
    4. Analyse et retourne les prix réels
    """
    try:
        # ── Étape 1 : Télécharger la photo ──
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")

        content_type = resp.headers.get("content-type", "image/jpeg")
        media_type = "image/png" if "png" in content_type else "image/jpeg"

        # ── Étape 2 : Identifier l'objet ──
        id_response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_data}
                    },
                    {
                        "type": "text",
                        "text": IDENTIFICATION_PROMPT.format(
                            caption=caption if caption else "Aucune information supplémentaire"
                        )
                    }
                ]
            }]
        )
        objet_identifie = id_response.content[0].text.strip()

        # ── Étape 3 : Générer les requêtes de recherche ──
        queries_response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": SEARCH_QUERIES_PROMPT.format(objet=objet_identifie)
            }]
        )
        queries = [q.strip() for q in queries_response.content[0].text.strip().split("\n") if q.strip()][:4]

        # ── Étape 4 : Recherche web en temps réel ──
        search_results = await _search_prices(objet_identifie, queries)

        # ── Étape 5 : Analyse finale avec les vrais prix ──
        final_response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_data}
                    },
                    {
                        "type": "text",
                        "text": ANALYSIS_PROMPT.format(
                            objet=objet_identifie,
                            caption=caption if caption else "Aucune information supplémentaire",
                            search_results=search_results
                        )
                    }
                ]
            }]
        )

        return final_response.content[0].text

    except anthropic.APIError as e:
        return f"⚠️ Erreur API Claude : {str(e)}"
    except httpx.HTTPError as e:
        return f"⚠️ Impossible de télécharger la photo : {str(e)}"
    except Exception as e:
        return f"⚠️ Erreur : {str(e)}"


async def _search_prices(objet: str, queries: list) -> str:
    """
    Utilise l'API Claude avec web_search pour chercher
    les vraies annonces sur Vinted, LBC, eBay, Etsy, etc.
    """
    plateformes = [
        "vinted", "leboncoin", "ebay", "etsy",
        "catawiki", "facebook marketplace", "interencheres", "drouot"
    ]

    all_results = []

    for query in queries:
        try:
            # Recherche avec l'outil web_search de Claude
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Cherche le prix de vente de : {query}\n"
                        f"Cherche sur : Vinted, Leboncoin, eBay France, Etsy, Catawiki, Facebook Marketplace, Interenchères\n"
                        f"Donne-moi les prix trouvés avec la plateforme et l'état de l'objet.\n"
                        f"Format : [Plateforme] [Prix] [État] [Lien si possible]"
                    )
                }]
            )

            # Extraire le texte de la réponse
            for block in response.content:
                if hasattr(block, 'text') and block.text:
                    all_results.append(f"🔍 Recherche '{query}':\n{block.text}")

        except Exception as e:
            all_results.append(f"🔍 Recherche '{query}': Résultat non disponible ({str(e)[:50]})")

    if all_results:
        return "\n\n".join(all_results)
    return "Aucun résultat de recherche disponible — estimation basée sur l'expertise marché."
