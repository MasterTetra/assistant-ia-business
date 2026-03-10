# 🚀 GUIDE D'INSTALLATION — Assistant IA Business

## Ce que fait ce bot

- 📸 **Sourcing** : Envoie une photo → analyse prix marché automatique
- 📦 **Stock** : Gestion inventaire avec emplacements d'entrepôt
- 📝 **Annonces** : Génération SEO automatique + publication eBay
- 📊 **Rapports** : Bilans hebdo/mensuel sur commande
- 💰 **Finances** : Marges, TVA sur marge, bilan comptable

---

## ÉTAPE 1 — Créer le bot Telegram (5 min)

1. Ouvre Telegram sur ton téléphone
2. Cherche **@BotFather**
3. Tape `/newbot`
4. Nom du bot : ex. `MonBusinessIA`
5. Username : ex. `monbusiness_ia_bot` (doit finir par `bot`)
6. **Copie le TOKEN** affiché (format : `7123456789:AABBccDD...`)

---

## ÉTAPE 2 — Clé API Anthropic (Claude) (3 min)

1. Va sur https://console.anthropic.com
2. Crée un compte (gratuit)
3. Menu **API Keys** → **Create Key**
4. **Copie la clé** (format : `sk-ant-api03-...`)
5. Ajoute 5$ de crédit (suffisant pour des centaines d'analyses)

---

## ÉTAPE 3 — Créer la base Airtable (10 min)

1. Va sur https://airtable.com → Créer un compte gratuit
2. Crée une nouvelle **Base** : nom `Business IA`
3. Crée une table **Produits** avec ces colonnes :

| Nom de la colonne | Type |
|---|---|
| Référence | Texte (Champ principal) |
| Nom | Texte |
| Date achat | Date |
| Prix achat | Nombre (décimal) |
| Prix vente | Nombre (décimal) |
| Source | Texte |
| Statut | Sélection unique : acheté / en stockage / en ligne / vendu / expédié / livré |
| Emplacement | Texte |
| Photos URLs | Texte long |
| Nombre de photos | Nombre |
| Annonce générée | Texte long |
| Plateforme vente | Texte |
| Date vente | Date |
| Frais plateforme | Nombre |
| Frais transport | Nombre |
| Notes | Texte long |

4. **Obtenir l'API Key Airtable** :
   - Va sur https://airtable.com/account
   - Section **API** → Génère un token avec accès **data.records:write** sur ta base
   - Copie le token

5. **Obtenir le Base ID** :
   - Ouvre ta base Airtable
   - Regarde l'URL : `https://airtable.com/appXXXXXXXXXXXXXX/...`
   - Le `appXXXXXXXXXXXXXX` = ton **Base ID**

---

## ÉTAPE 4 — Déployer sur Render (10 min)

### 4a. Mettre le code sur GitHub
1. Crée un compte GitHub (https://github.com)
2. Crée un nouveau dépôt privé : `assistant-ia-business`
3. Upload tous les fichiers de ce dossier

### 4b. Déployer sur Render
1. Va sur https://render.com → Créer un compte
2. **New → Web Service**
3. Connecte ton dépôt GitHub
4. Configuration :
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `python bot.py`
   - **Instance Type** : Free

5. Dans **Environment Variables**, ajoute :
   ```
   TELEGRAM_TOKEN = [ton token BotFather]
   ANTHROPIC_API_KEY = [ta clé Anthropic]
   AIRTABLE_API_KEY = [ta clé Airtable]
   AIRTABLE_BASE_ID = [ton base ID Airtable]
   ```

6. Clique **Deploy** → Attends 2-3 minutes

---

## ÉTAPE 5 — Tester le bot

1. Ouvre Telegram → cherche ton bot par son username
2. Tape `/start` → tu dois voir le menu
3. Envoie une photo d'un objet → tu dois recevoir l'analyse en 15-30 secondes

---

## Commandes disponibles

| Commande | Action |
|---|---|
| `/start` | Menu principal |
| `/aide` | Toutes les commandes |
| 📸 Photo | Analyse sourcing automatique |
| `/acheter` | Enregistrer un achat |
| `/stock` | État du stock |
| `/chercher [terme]` | Localiser un objet |
| `/annonce [ref]` | Générer une annonce |
| `/rapport` | Rapport 7 jours |
| `/rapport mensuel` | Bilan du mois |
| `/finances` | Bilan financier complet |

---

## En cas de problème

- **Bot ne répond pas** : Vérifier les logs sur Render (Dashboard → Logs)
- **Erreur API Claude** : Vérifier le crédit sur console.anthropic.com
- **Erreur Airtable** : Vérifier les permissions du token API

---

## Prochaines étapes (optionnelles)

- [ ] Ajouter Cloudinary pour stocker les photos de façon permanente
- [ ] Configurer les clés eBay pour la publication automatique
- [ ] Activer le service client automatisé (réponses aux acheteurs)
- [ ] Passer à n8n self-hosted pour des workflows plus avancés
