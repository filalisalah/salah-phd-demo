# Connexion Roo Code ↔ Figma via le Figma MCP Server

Ce document décrit la configuration permettant à **Roo Code** (extension VS Code)
d'accéder à votre compte Figma et d'importer le design de l'application web
(AURA-Grid PoC).

> **Principe** : Roo Code ne lit **pas** le fichier `.cursor/mcp.json` (celui-ci
> est réservé à Cursor). Roo Code utilise son propre fichier global
> `mcp_settings.json`. L'intégration passe par le **Figma MCP Server** (Model
> Context Protocol), variante **Remote** (`https://mcp.figma.com/mcp`).

---

## 1. Emplacement du fichier `mcp_settings.json` de Roo Code

Roo Code stocke sa configuration MCP dans un fichier **global**, hors du
workspace :

| Éditeur | Chemin du fichier |
|---|---|
| **VS Code** | `C:/Users/salah/AppData/Roaming/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json` |
| **Cursor** | `C:/Users/salah/AppData/Roaming/Cursor/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json` |

> Le fichier effectivement utilisé est celui de l'éditeur dans lequel Roo Code
> est exécuté. Pour ce projet sous **VS Code**, le fichier actif est celui de
> `Code/...` (il a été mis à jour pour inclure le serveur `figma`).
>
> **Note** : le fichier **Cursor** (`Cursor/...`) a également été aligné pour
> contenir la même entrée `figma` (fusionnée avec `exa-search`), afin de rester
> cohérent si Roo Code est lancé depuis Cursor. Le schéma
> `type: "streamable-http"` (avec `url` et `headers` optionnels) est bien
> supporté par **Roo Code 3.54.0** : la configuration est donc valide.

Il peut également exister un `mcp.json` au niveau du workspace, mais la
configuration globale `mcp_settings.json` reste la référence.

---

## 2. Entrée `figma` à ajouter

Le fichier `mcp_settings.json` doit contenir, dans l'objet `mcpServers`, l'entrée
suivante (fusionnée avec les autres serveurs existants, sans les écraser) :

```json
{
  "mcpServers": {
    "figma": {
      "url": "https://mcp.figma.com/mcp",
      "disabled": false,
      "alwaysAllow": [],
      "type": "streamable-http"
    }
  }
}
```

Exemple de fichier complet (avec un autre serveur déjà présent) :

```json
{
  "mcpServers": {
    "exa-search": {
      "command": "npx",
      "args": ["-y", "exa-mcp-server"],
      "env": {
        "EXA_API_KEY": "<votre-clé>"
      },
      "alwaysAllow": ["web_search_exa", "web_fetch_exa"]
    },
    "figma": {
      "url": "https://mcp.figma.com/mcp",
      "disabled": false,
      "alwaysAllow": [],
      "type": "streamable-http"
    }
  }
}
```

Points d'attention :

- `disabled: false` → le serveur est activé.
- `type: "streamable-http"` → transport HTTP streaming attendu par le serveur
  distant Figma.
- `alwaysAllow: []` → aucune autorisation automatique d'outil ; Roo Code
  demandera confirmation à chaque appel (ajoutez des noms d'outils si vous
  souhaitez les auto-approuver).
- Le JSON doit rester valide : pas de virgule finale, indentation cohérente.

---

## 3. Authentification OAuth

Le serveur Figma distant utilise **OAuth**. La procédure :

1. Ouvrir le panneau **MCP Servers** de Roo Code (icône **MCP** dans la barre
   latérale de Roo Code).
2. Repérer le serveur **`figma`** dans la liste.
3. Cliquer sur **Connect** / **Authenticate** à côté du serveur `figma`.
4. Une page d'autorisation Figma s'ouvre dans le navigateur : se connecter au
   compte Figma et cliquer **Allow access** (autoriser l'accès).
5. Revenir dans VS Code : le serveur doit passer à l'état **Connected**.

> Sur les plans Organization/Enterprise, une **approbation admin** peut être
> nécessaire avant que l'autorisation OAuth ne soit accordée.

---

## 4. Vérification de la connexion

- Dans le panneau **MCP Servers**, le serveur **`figma`** doit apparaître
  **Connected** (point vert / statut connecté).
- Déplier le serveur : ses **outils** doivent être listés (lecture du design
  context, génération de code, etc.).
- Test rapide : dans Figma, faire un clic droit sur un frame/layer →
  **Copy link to selection**, puis coller ce lien dans un prompt Roo Code :

  ```
  Analyse ce design Figma et liste les couleurs, la typographie et le layout :
  <coller le lien ici>
  ```

  Le MCP extrait le `node ID` du lien et lit le design context correspondant.

---

## 5. Dépannage de base

| Symptôme | Cause probable | Action |
|---|---|---|
| Appels MCP en échec / erreur d'authentification | Token OAuth expiré | Se **déconnecter** puis **reconnecter** le serveur `figma` dans le panneau MCP Servers |
| Aucun outil visible pour `figma` | Serveur désactivé ou non connecté | Vérifier `"disabled": false` dans `mcp_settings.json`, puis reconnecter |
| Accès refusé à un fichier Figma | Permissions insuffisantes sur le fichier | Vérifier le partage du fichier Figma (accès **view** ou **edit**) |
| Serveur absent de la liste | Entrée manquante ou JSON invalide | Vérifier la validité du JSON et la présence de l'entrée `figma` |
| Serveur absent après ajout manuel | L'extension ne relit le fichier qu'au démarrage ou sur événement de changement de fichier | Recharger la fenêtre VS Code (Ctrl+Shift+P → **Developer: Reload Window**) |
| Plan Organization/Enterprise bloqué | Approbation admin requise | Demander l'approbation à l'administrateur Figma |

---

## 6. Notes

- Le fichier `.cursor/mcp.json` **n'est pas modifié** : il reste la configuration
  de référence pour **Cursor**.
- Ce document couvre uniquement la configuration MCP de Roo Code ; il ne traite
  pas de l'intégration UI Streamlit/Figma.

---

## Références

- Figma — Developer Docs : Figma MCP Server (remote / desktop installation)
- Figma — Help Center : MCP server setup
- Roo Code — Documentation MCP (MCP Servers panel, `mcp_settings.json`)
