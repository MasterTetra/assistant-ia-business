"""
MODULE EBAY PUBLISH
# VERSION 24 — Fix ShippingDetails, description parsing, Site supprimé
Publication d'annonces sur eBay via API Trading XML.
Gère les lots (1 annonce multi-stock) et la mise à jour des quantités.
"""
import httpx
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from config.settings import (
    EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, EBAY_USER_TOKEN,
    AIRTABLE_API_KEY, AIRTABLE_BASE_ID, TABLE_PRODUITS
)

logger = logging.getLogger(__name__)

EBAY_API_URL = "https://api.ebay.com/ws/api.dll"
AIRTABLE_URL = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS_AT = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

EBAY_HEADERS = {
    "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
    "X-EBAY-API-DEV-NAME": EBAY_DEV_ID,
    "X-EBAY-API-APP-NAME": EBAY_APP_ID,
    "X-EBAY-API-CERT-NAME": EBAY_CERT_ID,
    "X-EBAY-API-SITEID": "71",  # 71 = eBay France
    "Content-Type": "text/xml",
}

# Catégories eBay France fréquentes (à affiner selon les articles)
# ─────────────────────────────────────────────────────────────
# CATÉGORIES EBAY FRANCE — CategoryID validés (feuilles)
# Testés et confirmés sur SiteID=71 (eBay.fr)
# ─────────────────────────────────────────────────────────────
CATEGORIES_EBAY = {
    # ── VÊTEMENTS ──────────────────────────────────────────────
    "vetement_homme":        ("11484", True),   # Vêtements homme
    "vetement_femme":        ("15724", True),   # Vêtements femme
    "vetement_enfant":       ("171146",True),   # Vêtements enfant
    "chaussure_homme":       ("93427", True),   # Chaussures homme
    "chaussure_femme":       ("3034",  True),   # Chaussures femme
    "casquette":             ("163543",True),   # Chapeaux, casquettes
    "sac_main":              ("169291",True),   # Sacs, bagages
    "montre":                ("31387", True),   # Montres
    "bijou":                 ("10968", True),   # Bijoux

    # ── ÉLECTRONIQUE ───────────────────────────────────────────
    "smartphone":            ("9355",  True),   # Téléphones mobiles
    "ordinateur_portable":   ("177",   True),   # PC portables
    "tablette":              ("171485",True),   # Tablettes
    "console_jeux":          ("139971",True),   # Consoles de jeux
    "jeux_video":            ("139973",True),   # Jeux vidéo
    "tv_ecran":              ("11071", True),   # TV, écrans
    "audio_casque":          ("112529",True),   # Casques audio
    "appareil_photo":        ("31388", True),   # Appareils photo
    "drone":                 ("179697",True),   # Drones
    "cable_chargeur":        ("44980", True),   # Câbles, chargeurs

    # ── MAISON & JARDIN ────────────────────────────────────────
    "electromenager":        ("20625", True),   # Électroménager
    "meuble":                ("20091", True),   # Meubles
    "deco_maison":           ("10033", True),   # Déco intérieure
    "literie":               ("20444", True),   # Literie
    "jardin_outillage":      ("1266",  True),   # Jardinage, outillage
    "cuisine_art_table":     ("20625", True),   # Arts de la table
    "luminaire":             ("112581",True),   # Luminaires

    # ── AUTO / MOTO ────────────────────────────────────────────
    "piece_auto":            ("6750",  True),   # Pièces auto
    "accessoire_auto":       ("14946", True),   # Accessoires voiture
    "accessoire_moto":       ("10063", True),   # Accessoires moto
    "pneu":                  ("66471", True),   # Pneus
    "porte_cles_auto":       ("79269", False),  # Porte-clés auto collection (pas ConditionID)
    "miniature_auto":        ("222",   False),  # Miniatures auto (pas ConditionID)

    # ── SPORT & LOISIRS ────────────────────────────────────────
    "velo":                  ("7294",  True),   # Vélos
    "fitness_musculation":   ("15273", True),   # Fitness, musculation
    "sport_collectif":       ("888",   True),   # Sport collectif
    "ski_montagne":          ("36265", True),   # Ski, montagne
    "peche":                 ("1492",  True),   # Pêche
    "camping":               ("16034", True),   # Camping, randonnée
    "surf_water_sport":      ("26429", True),   # Sports nautiques

    # ── JOUETS & ENFANTS ───────────────────────────────────────
    "lego":                  ("19006", False),  # LEGO (pas ConditionID)
    "figurine":              ("261068",False),  # Figurines
    "jeu_societe":           ("2551",  True),   # Jeux de société
    "jouet_enfant":          ("220",   True),   # Jouets divers
    "jeu_plein_air":         ("11743", True),   # Jeux plein air

    # ── LIVRES, MUSIQUE, FILMS ─────────────────────────────────
    "livre":                 ("267",   True),   # Livres
    "bd_manga":              ("156228",True),   # BD, mangas
    "cd_musique":            ("306",   True),   # CD, musique
    "vinyle":                ("176985",True),   # Vinyles
    "dvd_film":              ("11232", True),   # DVD, Blu-ray
    "instrument_musique":    ("619",   True),   # Instruments

    # ── COLLECTION & ART ───────────────────────────────────────
    "timbre":                ("260",   False),  # Timbres
    "monnaie_piece":         ("253",   False),  # Monnaies, pièces
    "carte_collection":      ("2536",  False),  # Cartes à collectionner
    "art_tableau":           ("360",   True),   # Art, tableaux
    "antiquite":             ("20081", True),   # Antiquités
    "porte_cles_collection": ("2562",  False),  # Porte-clés génériques (pas ConditionID)

    # ── BÉBÉ & PUÉRICULTURE ────────────────────────────────────
    "poussette":             ("100218",True),   # Poussettes
    "vetement_bebe":         ("3082",  True),   # Vêtements bébé
    "jouet_bebe":            ("19068", True),   # Jouets bébé

    # ── SANTÉ & BEAUTÉ ─────────────────────────────────────────
    "soin_visage":           ("26395", True),   # Soins visage
    "parfum":                ("180345",True),   # Parfums
    "materiel_medical":      ("36447", True),   # Matériel médical

    # ── INFORMATIQUE ───────────────────────────────────────────
    "composant_pc":          ("175672",True),   # Composants PC
    "imprimante":            ("1245",  True),   # Imprimantes
    "stockage":              ("165",   True),   # Stockage, disques

    # ── DEFAULT ────────────────────────────────────────────────
    "default":               ("625",   False),  # Objets divers (pas ConditionID)
}

# Alias rapide CategoryID seul (pour compatibilité)
CATEGORIES_DEFAUT = {k: v[0] for k, v in CATEGORIES_EBAY.items()}
# Catégories sans ConditionID (collection, timbres, etc.)
CATEGORIES_SANS_CONDITION = {k for k, v in CATEGORIES_EBAY.items() if not v[1]}

# Frais eBay selon prix de vente
FRAIS_EBAY_PCT = 0.13  # 13%


def echapper_xml(texte: str) -> str:
    """Échappe les caractères spéciaux pour le XML eBay."""
    if not texte:
        return ""
    return (texte
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def convertir_liens_drive(photos_raw: str) -> list:
    """
    Convertit les liens Google Drive partagés en URLs directes compatibles eBay.
    Utilise le format /thumbnail?id=FILE_ID&sz=s1600 (sans redirection, stable).
    Formats acceptés :
      - https://drive.google.com/file/d/FILE_ID/view?...
      - https://drive.google.com/open?id=FILE_ID
      - https://drive.google.com/uc?export=view&id=FILE_ID
    """
    import re as _re
    if not photos_raw:
        return []
    urls = []
    for raw in photos_raw.split(","):
        raw = raw.strip()
        if not raw:
            continue
        # Extraire le FILE_ID depuis n'importe quel format Drive
        file_id = None
        m = _re.search(r"/file/d/([a-zA-Z0-9_-]+)", raw)
        if m:
            file_id = m.group(1)
        else:
            m = _re.search(r"[?&]id=([a-zA-Z0-9_-]+)", raw)
            if m:
                file_id = m.group(1)
        if file_id:
            # Format thumbnail — direct, sans redirection, sans & problématique
            urls.append(f"https://drive.google.com/thumbnail?id={file_id}&sz=s1600")
        elif raw.startswith("http"):
            urls.append(raw)
    return urls


def _ebay_call(call_name: str, xml_body: str) -> str:
    """Effectue un appel synchrone à l'API eBay Trading."""
    headers = {**EBAY_HEADERS, "X-EBAY-API-CALL-NAME": call_name}
    full_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<{call_name}Request xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  {xml_body}
</{call_name}Request>"""
    import httpx as _httpx
    import asyncio
    # Version synchrone pour compatibilité
    response = _httpx.post(EBAY_API_URL, headers=headers, content=full_xml, timeout=30)
    return response.text


async def _ebay_call_async(call_name: str, xml_body: str) -> str:
    """Effectue un appel asynchrone à l'API eBay Trading."""
    headers = {**EBAY_HEADERS, "X-EBAY-API-CALL-NAME": call_name}
    full_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<{call_name}Request xmlns="urn:ebay:apis:eBLBaseComponents">
  <RequesterCredentials>
    <eBayAuthToken>{EBAY_USER_TOKEN}</eBayAuthToken>
  </RequesterCredentials>
  {xml_body}
</{call_name}Request>"""
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(EBAY_API_URL, headers=headers, content=full_xml.encode("utf-8"))
    return resp.text


def _parse_xml(xml_text: str) -> ET.Element:
    try:
        # Supprimer le namespace pour simplifier
        xml_clean = re.sub(r' xmlns="[^"]+"', '', xml_text)
        return ET.fromstring(xml_clean)
    except Exception as e:
        logger.error(f"XML parse error: {e}\n{xml_text[:300]}")
        return None


def _get_xml_val(root: ET.Element, path: str) -> str:
    if root is None:
        return ""
    el = root.find(path)
    return el.text.strip() if el is not None and el.text else ""


def _build_photos_xml(photo_urls: list) -> str:
    """Génère le XML pour les photos eBay (max 12). Échappe les & dans les URLs."""
    if not photo_urls:
        return ""
    pics = "\n".join(
        f"<PictureURL>{url.strip().replace('&', '&amp;')}</PictureURL>"
        for url in photo_urls[:12]
        if url.strip()
    )
    return f"<PictureDetails>{pics}</PictureDetails>" if pics else ""


def _detect_categorie(titre: str, description: str) -> tuple:
    """
    Détecte automatiquement la catégorie eBay selon le contenu.
    Retourne (category_id, supporte_condition_id).
    """
    texte = (titre + " " + description).lower()

    # ── Porte-clés ──────────────────────────────────────────────
    if any(w in texte for w in ["porte-clé", "porte-cle", "porte clé", "keychain", "keyring", "key ring"]):
        if any(w in texte for w in ["auto", "voiture", "moto", "renault", "peugeot", "bmw", "audi", "mercedes", "ford", "vw", "volkswagen", "ferrari", "porsche", "citroen"]):
            return CATEGORIES_EBAY["porte_cles_auto"]
        return CATEGORIES_EBAY["porte_cles_collection"]

    # ── Vêtements ───────────────────────────────────────────────
    if any(w in texte for w in ["casquette", "bonnet", "chapeau", "bob "]):
        return CATEGORIES_EBAY["casquette"]
    if any(w in texte for w in ["chaussure", "basket", "sneaker", "boots", "botte", "mocassin", "escarpin"]):
        if any(w in texte for w in ["femme", "dame", "fille"]):
            return CATEGORIES_EBAY["chaussure_femme"]
        return CATEGORIES_EBAY["chaussure_homme"]
    if any(w in texte for w in ["sac ", "sac à main", "sac à dos", "bagage", "valise", "pochette"]):
        return CATEGORIES_EBAY["sac_main"]
    if any(w in texte for w in ["montre", "watch"]):
        return CATEGORIES_EBAY["montre"]
    if any(w in texte for w in ["bijou", "collier", "bracelet", "bague", "boucle d'oreille", "pendentif"]):
        return CATEGORIES_EBAY["bijou"]
    if any(w in texte for w in ["t-shirt", "tshirt", "veste", "manteau", "pantalon", "jean", "robe", "chemise", "pull", "sweat", "short", "maillot", "vêtement", "vetement", "habit"]):
        if any(w in texte for w in ["femme", "dame", "fille"]):
            return CATEGORIES_EBAY["vetement_femme"]
        if any(w in texte for w in ["enfant", "bébé", "bebe", "garçon"]):
            return CATEGORIES_EBAY["vetement_enfant"]
        return CATEGORIES_EBAY["vetement_homme"]

    # ── Auto / Moto ─────────────────────────────────────────────
    if any(w in texte for w in ["miniature", "modèle réduit", "voiture miniature", "die cast"]):
        return CATEGORIES_EBAY["miniature_auto"]
    if any(w in texte for w in ["pièce auto", "pièce détachée", "carrosserie", "moteur", "pare-choc", "phare auto"]):
        return CATEGORIES_EBAY["piece_auto"]
    if any(w in texte for w in ["accessoire moto", "casque moto", "gant moto"]):
        return CATEGORIES_EBAY["accessoire_moto"]
    if any(w in texte for w in ["accessoire auto", "autoradio", "gps voiture", "tapis voiture"]):
        return CATEGORIES_EBAY["accessoire_auto"]
    if any(w in texte for w in ["pneu", "jante", "roue"]):
        return CATEGORIES_EBAY["pneu"]

    # ── Électronique ────────────────────────────────────────────
    if any(w in texte for w in ["iphone", "samsung galaxy", "smartphone", "téléphone portable", "mobile"]):
        return CATEGORIES_EBAY["smartphone"]
    if any(w in texte for w in ["laptop", "pc portable", "macbook", "ultrabook"]):
        return CATEGORIES_EBAY["ordinateur_portable"]
    if any(w in texte for w in ["tablette", "ipad", "kindle"]):
        return CATEGORIES_EBAY["tablette"]
    if any(w in texte for w in ["playstation", "xbox", "nintendo", "switch", "ps5", "ps4", "wii"]):
        return CATEGORIES_EBAY["console_jeux"]
    if any(w in texte for w in ["jeu vidéo", "jeu video", "gaming"]):
        return CATEGORIES_EBAY["jeux_video"]
    if any(w in texte for w in ["télévision", "tv ", "écran", "moniteur"]):
        return CATEGORIES_EBAY["tv_ecran"]
    if any(w in texte for w in ["casque audio", "écouteur", "airpod", "enceinte", "bluetooth"]):
        return CATEGORIES_EBAY["audio_casque"]
    if any(w in texte for w in ["appareil photo", "reflex", "gopro", "objectif photo"]):
        return CATEGORIES_EBAY["appareil_photo"]
    if any(w in texte for w in ["drone", "fpv"]):
        return CATEGORIES_EBAY["drone"]
    if any(w in texte for w in ["câble", "chargeur", "adaptateur", "hub usb"]):
        return CATEGORIES_EBAY["cable_chargeur"]
    if any(w in texte for w in ["disque dur", "ssd", "clé usb", "stockage", "mémoire"]):
        return CATEGORIES_EBAY["stockage"]
    if any(w in texte for w in ["composant pc", "carte graphique", "processeur", "ram", "mémoire vive"]):
        return CATEGORIES_EBAY["composant_pc"]
    if any(w in texte for w in ["imprimante", "scanner", "cartouche"]):
        return CATEGORIES_EBAY["imprimante"]

    # ── Maison & Jardin ─────────────────────────────────────────
    if any(w in texte for w in ["canapé", "fauteuil", "chaise", "table basse", "bureau", "étagère", "meuble"]):
        return CATEGORIES_EBAY["meuble"]
    if any(w in texte for w in ["lave-linge", "réfrigérateur", "four", "micro-onde", "aspirateur", "électroménager"]):
        return CATEGORIES_EBAY["electromenager"]
    if any(w in texte for w in ["lampe", "luminaire", "plafonnier", "spot"]):
        return CATEGORIES_EBAY["luminaire"]
    if any(w in texte for w in ["déco", "vase", "cadre photo", "miroir", "tableau déco"]):
        return CATEGORIES_EBAY["deco_maison"]
    if any(w in texte for w in ["matelas", "couette", "oreiller", "drap", "housse"]):
        return CATEGORIES_EBAY["literie"]
    if any(w in texte for w in ["jardin", "tondeuse", "tronçonneuse", "outillage", "perceuse", "tournevis"]):
        return CATEGORIES_EBAY["jardin_outillage"]
    if any(w in texte for w in ["assiette", "verre", "couverts", "casserole", "poêle", "art de la table"]):
        return CATEGORIES_EBAY["cuisine_art_table"]

    # ── Sport & Loisirs ─────────────────────────────────────────
    if any(w in texte for w in ["vélo", "velo", "cyclisme", "trottinette"]):
        return CATEGORIES_EBAY["velo"]
    if any(w in texte for w in ["musculation", "haltère", "fitness", "tapis de course"]):
        return CATEGORIES_EBAY["fitness_musculation"]
    if any(w in texte for w in ["ski", "snowboard", "montagne", "randonnée"]):
        return CATEGORIES_EBAY["ski_montagne"]
    if any(w in texte for w in ["pêche", "canne à pêche", "moulinet"]):
        return CATEGORIES_EBAY["peche"]
    if any(w in texte for w in ["camping", "tente", "sac de couchage"]):
        return CATEGORIES_EBAY["camping"]
    if any(w in texte for w in ["surf", "kayak", "plongée", "natation"]):
        return CATEGORIES_EBAY["surf_water_sport"]
    if any(w in texte for w in ["ballon", "foot", "football", "tennis", "rugby", "basketball"]):
        return CATEGORIES_EBAY["sport_collectif"]

    # ── Jouets & Enfants ────────────────────────────────────────
    if any(w in texte for w in ["lego", "duplo"]):
        return CATEGORIES_EBAY["lego"]
    if any(w in texte for w in ["figurine", "action figure", "funko"]):
        return CATEGORIES_EBAY["figurine"]
    if any(w in texte for w in ["jeu de société", "puzzle", "monopoly"]):
        return CATEGORIES_EBAY["jeu_societe"]
    if any(w in texte for w in ["bébé", "bebe", "poussette", "siège auto bébé"]):
        return CATEGORIES_EBAY["poussette"]
    if any(w in texte for w in ["jouet", "doudou", "peluche"]):
        return CATEGORIES_EBAY["jouet_enfant"]

    # ── Livres, Musique, Films ──────────────────────────────────
    if any(w in texte for w in ["livre", "roman", "manga", "bande dessinée", "bd "]):
        if any(w in texte for w in ["manga", "bande dessinée", "bd "]):
            return CATEGORIES_EBAY["bd_manga"]
        return CATEGORIES_EBAY["livre"]
    if any(w in texte for w in ["vinyle", "vinyl", "33 tours", "45 tours"]):
        return CATEGORIES_EBAY["vinyle"]
    if any(w in texte for w in ["cd ", "album cd", "musique"]):
        return CATEGORIES_EBAY["cd_musique"]
    if any(w in texte for w in ["dvd", "blu-ray", "blu ray", "film"]):
        return CATEGORIES_EBAY["dvd_film"]
    if any(w in texte for w in ["guitare", "basse", "piano", "clavier", "batterie", "instrument"]):
        return CATEGORIES_EBAY["instrument_musique"]

    # ── Collection & Art ────────────────────────────────────────
    if any(w in texte for w in ["timbre", "philatélie"]):
        return CATEGORIES_EBAY["timbre"]
    if any(w in texte for w in ["pièce de monnaie", "monnaie", "billet", "numismatique"]):
        return CATEGORIES_EBAY["monnaie_piece"]
    if any(w in texte for w in ["carte pokémon", "pokemon", "magic the gathering", "yu-gi-oh", "panini"]):
        return CATEGORIES_EBAY["carte_collection"]
    if any(w in texte for w in ["tableau", "peinture", "aquarelle", "sculpture"]):
        return CATEGORIES_EBAY["art_tableau"]
    if any(w in texte for w in ["antiquité", "antique", "ancien", "vintage"]):
        return CATEGORIES_EBAY["antiquite"]

    # ── Santé & Beauté ──────────────────────────────────────────
    if any(w in texte for w in ["parfum", "eau de toilette", "cologne"]):
        return CATEGORIES_EBAY["parfum"]
    if any(w in texte for w in ["crème", "sérum", "soin", "skincare"]):
        return CATEGORIES_EBAY["soin_visage"]

    # ── Défaut ──────────────────────────────────────────────────
    return CATEGORIES_EBAY["default"]


async def publier_sur_ebay(
    titre: str,
    description: str,
    prix: float,
    quantite: int,
    etat: str,
    photo_urls: list,
    poids_grammes: int = 500,
    ref_principale: str = ""
) -> dict:
    """
    Publie une annonce sur eBay.
    Retourne {"success": bool, "item_id": str, "url": str, "error": str}
    """
    # Mapper l'état vers les valeurs eBay
    etat_ebay_map = {
        "Neuf": "New",
        "Tres bon etat": "Like New",
        "Très bon état": "Like New",
        "Bon etat": "Used",
        "Bon état": "Used",
        "Satisfaisant": "Acceptable",
        "Pour pieces": "For parts or not working",
        "Pour pièces": "For parts or not working",
    }
    condition_id_map = {
        "New": "1000",
        "Like New": "3000",
        "Used": "4000",
        "Acceptable": "5000",
        "For parts or not working": "7000",
    }
    etat_ebay = etat_ebay_map.get(etat, "Used")
    condition_id = condition_id_map.get(etat_ebay, "4000")

    categorie, supporte_condition = _detect_categorie(titre, description)
    # Convertir les liens Drive en URLs directes si nécessaire
    photo_urls_direct = convertir_liens_drive(",".join(photo_urls)) if photo_urls else []
    photos_xml = _build_photos_xml(photo_urls_direct)

    # Échapper tout le contenu texte pour XML
    # Nettoyer titre et description : supprimer tout tag XML parasite
    import re as _re
    def _nettoyer(texte):
        # Supprimer balises XML
        texte = _re.sub(r'<[^>]+>', '', texte)
        # Supprimer lignes parasites (TITRE:, PRIX:, MOTS-CLES:)
        texte = _re.sub(r'^(TITRE|PRIX|MOTS.CLES)\s*:.*$', '', texte, flags=_re.MULTILINE)
        # Nettoyer lignes vides multiples
        texte = _re.sub('\\n{3,}', '\\n\\n', texte).strip()
        return texte

    titre_safe = echapper_xml(_nettoyer(titre)[:80])
    desc_safe = echapper_xml(_nettoyer(description))

    # Construction XML par concaténation — évite les problèmes de f-string multiligne
    parts = []
    parts.append("<Item>")
    parts.append(f"<Title>{titre_safe}</Title>")
    parts.append(f"<Description>{desc_safe}</Description>")
    parts.append(f"<PrimaryCategory><CategoryID>{categorie}</CategoryID></PrimaryCategory>")
    parts.append(f'<StartPrice currencyID="EUR">{prix:.2f}</StartPrice>')
    # ConditionID seulement si la catégorie le supporte
    if supporte_condition:
        parts.append(f"<ConditionID>{condition_id}</ConditionID>")
    parts.append("<Country>FR</Country>")
    parts.append("<Location>France</Location>")
    parts.append("<Currency>EUR</Currency>")
    parts.append("<DispatchTimeMax>3</DispatchTimeMax>")
    parts.append("<ListingDuration>GTC</ListingDuration>")
    parts.append("<ListingType>FixedPriceItem</ListingType>")
    parts.append(f"<Quantity>{quantite}</Quantity>")
    parts.append("<ReturnPolicy>")
    parts.append("<ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>")
    parts.append("<ReturnsWithinOption>Days_30</ReturnsWithinOption>")
    parts.append("<ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>")
    parts.append("</ReturnPolicy>")
    parts.append("<ShippingDetails>")
    parts.append("<ShippingType>Flat</ShippingType>")
    parts.append("<ShippingServiceOptions>")
    parts.append("<ShippingServicePriority>1</ShippingServicePriority>")
    parts.append("<ShippingService>FR_Chronopost</ShippingService>")
    parts.append('<ShippingServiceCost currencyID="EUR">0.00</ShippingServiceCost>')
    parts.append('<ShippingServiceAdditionalCost currencyID="EUR">0.00</ShippingServiceAdditionalCost>')
    parts.append("<FreeShipping>true</FreeShipping>")
    parts.append("</ShippingServiceOptions>")
    parts.append("</ShippingDetails>")
    parts.append("<ShipToLocations>FR</ShipToLocations>")
    if photos_xml:
        parts.append(photos_xml)
    parts.append("</Item>")
    xml_body = "".join(parts)

    # Validation XML locale avant envoi
    try:
        ET.fromstring(f"<root>{xml_body}</root>")
        logger.info("✅ XML validé localement")
    except ET.ParseError as xml_err:
        logger.error(f"❌ XML INVALIDE localement: {xml_err}")
        return {"success": False, "item_id": "", "url": "", "error": f"XML invalide: {xml_err}"}

    try:
        logger.info(f"📤 XML COMPLET envoyé à eBay:\n{xml_body}")
        resp_xml = await _ebay_call_async("AddFixedPriceItem", xml_body)
        logger.info(f"eBay AddFixedPriceItem réponse: {resp_xml[:500]}")
        root = _parse_xml(resp_xml)
        ack = _get_xml_val(root, "Ack")
        item_id = _get_xml_val(root, "ItemID")
        errors = root.findall("Errors") if root is not None else []

        if ack in ("Success", "Warning") and item_id:
            url = f"https://www.ebay.fr/itm/{item_id}"
            logger.info(f"✅ eBay publié : {item_id} — {url}")
            return {"success": True, "item_id": item_id, "url": url, "error": ""}
        else:
            err_msgs = []
            for e in errors:
                code = _get_xml_val(e, "ErrorCode")
                msg = _get_xml_val(e, "LongMessage") or _get_xml_val(e, "ShortMessage")
                err_msgs.append(f"[{code}] {msg}")
            error = " | ".join(err_msgs) or f"Ack={ack}"
            logger.error(f"❌ eBay ECHEC: {error}")
            return {"success": False, "item_id": "", "url": "", "error": error}
    except Exception as e:
        logger.error(f"ebay publish error: {e}", exc_info=True)
        return {"success": False, "item_id": "", "url": "", "error": str(e)}


async def modifier_quantite_ebay(item_id: str, nouvelle_quantite: int) -> bool:
    """Décrémente la quantité d'une annonce eBay active."""
    if nouvelle_quantite <= 0:
        # Terminer l'annonce
        xml_body = f"<ItemID>{item_id}</ItemID><EndingReason>NotAvailable</EndingReason>"
        resp = await _ebay_call_async("EndFixedPriceItem", xml_body)
    else:
        xml_body = f"""
  <Item>
    <ItemID>{item_id}</ItemID>
    <Quantity>{nouvelle_quantite}</Quantity>
  </Item>"""
        resp = await _ebay_call_async("ReviseFixedPriceItem", xml_body)

    root = _parse_xml(resp)
    ack = _get_xml_val(root, "Ack")
    ok = ack in ("Success", "Warning")
    if not ok:
        logger.error(f"modifier_quantite_ebay ECHEC: {resp[:300]}")
    return ok


async def get_refs_lot(titre: str, statut: str = "en ligne") -> list:
    """
    Retourne toutes les refs Airtable avec le même titre et le statut donné.
    Utilisé pour détecter les lots et gérer les ventes.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={
                    "filterByFormula": f"AND({{Statut}}='{statut}', FIND('{titre[:30]}', {{Description}})>0)",
                    "fields[]": ["Référence gestion", "Description", "eBay Item ID", "Prix achat unitaire"],
                    "maxRecords": 200,
                    "sort[0][field]": "Référence gestion",
                    "sort[0][direction]": "asc"
                }
            )
        return resp.json().get("records", [])
    except Exception as e:
        logger.error(f"get_refs_lot error: {e}")
        return []


async def sauvegarder_ebay_item_id(ref: str, item_id: str, url: str) -> bool:
    """Sauvegarde l'eBay Item ID dans Airtable pour la référence donnée."""
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            # Trouver le record
            resp = await http.get(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}",
                headers=HEADERS_AT,
                params={"filterByFormula": f"{{Référence gestion}}='{ref}'", "maxRecords": 1}
            )
        records = resp.json().get("records", [])
        if not records:
            return False
        record_id = records[0]["id"]
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.patch(
                f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                headers=HEADERS_AT,
                json={"fields": {
                    "eBay Item ID": item_id,
                    "Notes": f"eBay: {url}",
                    "Plateforme vente": "eBay"
                }}
            )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"sauvegarder_ebay_item_id error: {e}")
        return False


async def traiter_vente_ebay(titre_vendu: str, quantite_vendue: int, prix_vente: float) -> dict:
    """
    Traite une vente eBay détectée via Make.com :
    1. Trouve les refs "en ligne" correspondant au titre
    2. Passe les X premières à "vendu"
    3. Décrémente la quantité sur eBay
    4. Retourne le résumé pour notification
    """
    try:
        records = await get_refs_lot(titre_vendu, statut="en ligne")
        if not records:
            return {"ok": False, "error": f"Aucun article '{titre_vendu[:30]}' en ligne trouvé"}

        refs_a_vendre = records[:quantite_vendue]
        item_id = records[0]["fields"].get("eBay Item ID", "")
        date_vente = datetime.now().strftime("%Y-%m-%d")
        refs_vendues = []

        async with httpx.AsyncClient(timeout=30) as http:
            for rec in refs_a_vendre:
                record_id = rec["id"]
                ref = rec["fields"].get("Référence gestion", "?")
                prix_achat = rec["fields"].get("Prix achat unitaire", 0)
                await http.patch(
                    f"{AIRTABLE_URL}/{TABLE_PRODUITS}/{record_id}",
                    headers=HEADERS_AT,
                    json={"fields": {
                        "Statut": "vendu",
                        "Date vente": date_vente,
                        "Plateforme vente": "eBay",
                        "Prix vente": prix_vente,
                    }}
                )
                refs_vendues.append({"ref": ref, "prix_achat": prix_achat})

        # Décrémenter quantité eBay
        nouvelle_qte = len(records) - quantite_vendue
        if item_id:
            await modifier_quantite_ebay(item_id, nouvelle_qte)

        # Calcul marge
        prix_achat_moy = sum(r["prix_achat"] for r in refs_vendues) / len(refs_vendues) if refs_vendues else 0
        frais = round(prix_vente * FRAIS_EBAY_PCT, 2)
        marge = round(prix_vente - prix_achat_moy - frais, 2)
        marge_pct = round(marge / prix_achat_moy * 100) if prix_achat_moy > 0 else 0

        return {
            "ok": True,
            "refs_vendues": [r["ref"] for r in refs_vendues],
            "quantite": quantite_vendue,
            "restant": nouvelle_qte,
            "prix_vente": prix_vente,
            "prix_achat_moy": prix_achat_moy,
            "frais": frais,
            "marge": marge,
            "marge_pct": marge_pct,
        }
    except Exception as e:
        logger.error(f"traiter_vente_ebay error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}
