"""
MODULE SOURCING — recherche web temps réel + format fiable
"""
import anthropic
import httpx
import base64
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SOURCING_PROMPT = """Tu es un expert en achat-revente d'objets d'occasion.
INFOS SUR L'OBJET : {caption}

ETAPE 1 : Identifie l'objet sur la photo.
ETAPE 2 : Fais 2 recherches web pour trouver les prix reels sur Vinted, Leboncoin, eBay, Etsy, Catawiki.

REGLE ABSOLUE : Ta reponse finale doit contenir UNIQUEMENT ces blocs dans cet ordre exact.
N'utilise PAS de Markdown (pas de **, pas de *, pas de #).
Utilise EXACTEMENT ces titres en majuscules :

OBJET IDENTIFIE
[description precise]

ANNONCES TROUVEES
[liste des vraies annonces trouvees avec plateforme et prix]

PRIX DU MARCHE
Prix bas : Xeuros
Prix moyen : Xeuros
Prix haut : Xeuros

RECOMMANDATION
Prix de revente conseille : Xeuros
Prix achat maximum : Xeuros
Marge estimee : X pourcent

DEMANDE
[FORTE ou MOYENNE ou FAIBLE] - [explication courte]

MEILLEURES PLATEFORMES
[liste des plateformes]

CONSEIL
[une seule phrase de conseil]

SEUIL
[uniquement le chiffre du prix achat maximum, ex: 45]"""


async def analyze_sourcing(photo_url: str, caption: str = "") -> str:
    try:
        # Télécharger la photo
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(photo_url)
            resp.raise_for_status()
            image_data = base64.standard_b64encode(resp.content).decode("utf-8")

        content_type = resp.headers.get("content-type", "image/jpeg")
        media_type = "image/png" if "png" in content_type else "image/jpeg"

        # Appel Claude avec web_search
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

        # Extraire uniquement le dernier bloc texte
        final_text = ""
        for block in response.content:
            if hasattr(block, "text") and block.text:
                final_text = block.text

        if not final_text:
            return "Analyse non disponible."

        # Reformater proprement avec emojis
        return _format_response(final_text)

    except anthropic.APIError as e:
        return f"Erreur API Claude : {str(e)}"
    except Exception as e:
        return f"Erreur : {str(e)}"


def _clean(text: str) -> str:
    """Supprime tout le Markdown de la réponse Claude."""
    import re
    # Supprimer ** gras **
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Supprimer * italique *
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Supprimer _ italique _
    text = re.sub(r'_(.+?)_', r'\1', text)
    # Supprimer # titres
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Supprimer ` code `
    text = re.sub(r'`(.+?)`', r'\1', text)
    return text.strip()


def _format_response(text: str) -> str:
    """Nettoie le Markdown et reformate avec emojis."""
    text = _clean(text)
    lines = text.strip().split("\n")
    output = []

    for line in lines:
        line = line.strip()
        if not line:
            output.append("")
            continue

        # Titres de sections — variantes avec/sans accent, majuscules
        lu = line.upper()
        if any(lu.startswith(x) for x in ["OBJET IDENTIFIE", "OBJET IDENTIFIÉ"]):
            output.append("\n🔎 OBJET IDENTIFIE")
        elif any(lu.startswith(x) for x in ["ANNONCES TROUV"]):
            output.append("\n🌐 ANNONCES TROUVEES")
        elif any(lu.startswith(x) for x in ["PRIX DU MARCH"]):
            output.append("\n💰 PRIX DU MARCHE")
        elif lu.startswith("RECOMMANDATION"):
            output.append("\n✅ RECOMMANDATION")
        elif lu.startswith("DEMANDE"):
            output.append("\n📈 DEMANDE")
        elif any(lu.startswith(x) for x in ["MEILLEURES PLATEFORME", "PLATEFORMES"]):
            output.append("\n🏆 MEILLEURES PLATEFORMES")
        elif lu.startswith("CONSEIL"):
            output.append("\n⚡ CONSEIL")
        elif lu.startswith("SEUIL"):
            prix_raw = line.replace("SEUIL", "").replace("seuil", "").replace(":", "").strip()
            try:
                import re
                nums = re.findall(r"[\d]+", prix_raw)
                prix = nums[0] if nums else "?"
                output.append(f"\n💡 Seuil decision : {prix} euros maximum")
            except:
                output.append(f"\n💡 {line}")
        elif any(line.lower().startswith(x) for x in ["prix bas", "prix moyen", "prix haut",
                                                        "prix de revente", "prix achat", "marge"]):
            output.append(f"• {line}")
        else:
            output.append(line)

    # Nettoyer les lignes vides multiples
    result = []
    prev_empty = False
    for line in output:
        if line == "":
            if not prev_empty:
                result.append(line)
            prev_empty = True
        else:
            prev_empty = False
            result.append(line)

    return "\n".join(result).strip()
