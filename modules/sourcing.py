"""
MODULE SOURCING — recherche web temps réel + format fiable
"""
import anthropic
import httpx
import base64
from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SOURCING_PROMPT = """Tu es un expert en achat-revente d'objets d'occasion avec 20 ans d'expérience.

INFORMATIONS SUPPLÉMENTAIRES SUR L'OBJET : {caption}

MISSION :
1. Identifie précisément l'objet sur la photo
2. Utilise l'outil web_search pour chercher les VRAIS prix actuels sur Vinted, Leboncoin, eBay France, Etsy, Catawiki, Interenchères
3. Fais 2 recherches maximum :
   - Recherche 1 : "[nom objet] vendre prix" 
   - Recherche 2 : "[nom objet] occasion prix"

IMPORTANT : Ta réponse finale doit OBLIGATOIREMENT suivre ce format exact, sans aucune déviation :

OBJET IDENTIFIE
[Nom précis, marque, époque, état]

ANNONCES TROUVEES
[3-5 vraies annonces : Plateforme - Prix - État]

PRIX DU MARCHE
Prix bas : XXX euros
Prix moyen : XXX euros
Prix haut : XXX euros

RECOMMANDATION
Prix de revente conseille : XXX euros
Prix achat maximum : XXX euros
Marge estimee : XX pourcent

DEMANDE
[FORTE / MOYENNE / FAIBLE] - [raison courte]

MEILLEURES PLATEFORMES
[liste]

CONSEIL
[1 phrase pratique]

SEUIL
[prix achat maximum en chiffre uniquement]"""


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


def _format_response(text: str) -> str:
    """Reformate la réponse brute en message Telegram propre sans Markdown."""
    lines = text.strip().split("\n")
    output = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Titres de sections
        if line.startswith("OBJET IDENTIFIE"):
            output.append("\n🔎 OBJET IDENTIFIE")
        elif line.startswith("ANNONCES TROUVEES"):
            output.append("\n🌐 ANNONCES TROUVEES")
        elif line.startswith("PRIX DU MARCHE"):
            output.append("\n💰 PRIX DU MARCHE")
        elif line.startswith("RECOMMANDATION"):
            output.append("\n✅ RECOMMANDATION")
        elif line.startswith("DEMANDE"):
            output.append("\n📈 DEMANDE")
        elif line.startswith("MEILLEURES PLATEFORMES"):
            output.append("\n🏆 MEILLEURES PLATEFORMES")
        elif line.startswith("CONSEIL"):
            output.append("\n⚡ CONSEIL")
        elif line.startswith("SEUIL"):
            # Extraire le prix pour le seuil
            prix = line.replace("SEUIL", "").replace(":", "").strip()
            try:
                prix_float = float(''.join(c for c in prix if c.isdigit() or c == '.'))
                output.append(f"\n💡 Seuil decision : {prix_float} euros maximum")
            except:
                output.append(f"\n💡 Seuil : {prix}")
        else:
            # Contenu normal
            if line.startswith("Prix bas"):
                output.append(f"• {line}")
            elif line.startswith("Prix moyen"):
                output.append(f"• {line}")
            elif line.startswith("Prix haut"):
                output.append(f"• {line}")
            elif line.startswith("Prix de revente"):
                output.append(f"• {line}")
            elif line.startswith("Prix achat"):
                output.append(f"• {line}")
            elif line.startswith("Marge"):
                output.append(f"• {line}")
            else:
                output.append(line)

    return "\n".join(output).strip()
