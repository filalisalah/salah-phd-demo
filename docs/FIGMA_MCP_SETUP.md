# Connexion Cursor ↔ Figma via le Figma MCP Server

Ce document décrit la configuration permettant à Cursor d'accéder à votre compte
Figma et d'importer le design de votre application web (AURA-Grid PoC).

> **Principe** : il n'existe pas de connexion native directe Cursor ↔ Figma.
> L'intégration passe par le **Figma MCP Server** (Model Context Protocol),
> supporté nativement par Cursor.

---

## 1. Variantes du serveur MCP

| Variante | Endpoint | Prérequis | Recommandation |
|---|---|---|---|
| **Remote** | `https://mcp.figma.com/mcp` | Compte Figma plan Starter+ | ✅ Recommandée |
| **Desktop** | `http://127.0.0.1:3845/mcp` | App Figma desktop + siège Dev/Full (plan payant) | Cas entreprise |

La configuration de ce projet utilise la variante **Remote** (voir
[`.cursor/mcp.json`](../.cursor/mcp.json)).

---

## 2. Prérequis

- Compte Figma avec un plan **Starter, Professional, Education, Organization ou Enterprise**.
- **Cursor** à jour.
- Accès (view ou edit) au fichier Figma contenant la maquette.
- Sur les plans Organization/Enterprise, une **approbation admin** peut être requise.

---

## 3. Installation (méthode recommandée : plugin)

1. Ouvrir la palette de commandes : `Ctrl+Shift+P` (Windows).
2. Chercher **Open chat**.
3. Dans le chat, saisir et valider :

   ```
   /add-plugin figma
   ```

4. Cliquer **Add Plugin**.
5. Ouvrir `Cursor Settings` → **Tools & MCP**.
6. Sous *Installed MCP Servers*, cliquer **Connect** à côté de **Figma**.
7. Cliquer **Allow access** pour autoriser Cursor à accéder au compte Figma (OAuth).

Le plugin inclut la configuration MCP + les *skills* Figma (implémentation de
designs, Code Connect, règles de design system).

---

## 4. Installation manuelle (alternative)

Si le plugin n'est pas disponible :

1. `Cursor Settings` → **Tools & MCP** → **Add Custom MCP**.
2. Ajouter la configuration suivante (déjà présente dans `.cursor/mcp.json`) :

   ```json
   {
     "mcpServers": {
       "figma": {
         "url": "https://mcp.figma.com/mcp"
       }
     }
   }
   ```

3. Sauvegarder, puis cliquer **Connect** à côté de **Figma** pour se connecter.

---

## 5. Vérification

- Le serveur **Figma** doit apparaître **Connecté** dans *Tools & MCP*.
- Ses outils doivent être listés (lecture du design context, génération de code, etc.).

---

## 6. Tester la connexion

1. Dans Figma, faire un clic droit sur un frame/layer → **Copy link to selection**.
2. Coller ce lien dans un prompt Cursor, par exemple :

   ```
   Analyse ce design Figma et liste les couleurs, la typographie et le layout :
   <coller le lien ici>
   ```

Le MCP extrait le `node ID` du lien et lit le design context correspondant.

---

## 7. Extraction des specs de la maquette

Une fois la connexion validée, demander à Cursor d'extraire :

- la **palette de couleurs** (variables / styles Figma) ;
- la **typographie** (familles, tailles, graisses) ;
- le **layout** (grilles, espacements, breakpoints) ;
- les **composants** réutilisables ;
- les **assets** (icônes, images).

---

## 8. Stratégie d'intégration (à décider)

Le projet AURA-Grid est une application **Streamlit/Python** (`app.py`), alors que
le Figma MCP génère naturellement du code **frontend React/HTML/CSS**. Deux options :

### Option 1 — Restyler l'UI Streamlit existante
- Injecter les couleurs/typographie Figma via du CSS custom Streamlit.
- Avantages : pas de nouvelle stack, effort minimal.
- Limites : personnalisation CSS Streamlit restreinte, fidélité partielle à la maquette.

### Option 2 — Créer une interface web séparée (React/HTML)
- Générer le frontend depuis la maquette Figma, garder le backend Python.
- Avantages : fidélité maximale au design, stack frontend moderne.
- Limites : ajoute une couche technique (build, API entre front et backend).

---

## 9. Dépannage

- **Appels MCP en échec** : les tokens de connexion ont peut-être expiré →
  se déconnecter/reconnecter le serveur Figma dans Cursor.
- **Aucun outil visible** : vérifier que le serveur est bien activé et connecté.
- **Accès refusé au fichier** : vérifier les permissions du fichier Figma
  (partage view/edit).
- **Plan Organization/Enterprise** : demander l'approbation admin si nécessaire.

---

## Références

- Figma — Cursor and Figma: Set up the MCP server (Help Center)
- Figma — Developer Docs: Figma MCP Server (remote / desktop installation)
- Figma — mcp-server-guide (GitHub)
