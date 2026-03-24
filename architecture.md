# Architecture — Flow (Application Frappe)

## Stack technologique

Flow est développé exclusivement sur la stack **Frappe Framework** :

- **Backend** : Python (Frappe)
- **Frontend** : JavaScript natif Frappe + Vue.js (composants Desk)
- **CSS** : Tailwind CSS (via les classes utilitaires dans les templates et composants)

Aucune technologie tierce ne doit être introduite sans accord explicite.

---

## Standards de codage

### 1. Server Scripts

Les **Server Scripts** (scripts côté serveur) sont utilisés pour toute logique métier liée aux DocTypes :

- Validation de données avant sauvegarde (`Before Save`, `Before Submit`, etc.)
- Calculs automatiques sur les champs
- Déclenchement d'actions sur des événements de document

Les Server Scripts sont créés directement dans l'interface Frappe (`Desk > Server Script`) ou versionnés sous forme de fichiers Python dans le dossier du DocType concerné :

```
flow/
  doctype/
    <nom_du_doctype>/
      <nom_du_doctype>.py   ← logique métier ici
```

**Règles :**
- Toute logique propre à un DocType reste dans son fichier `.py` associé.
- Pas de logique métier dans les fichiers de contrôleur génériques.
- Utiliser `frappe.throw()` pour les erreurs de validation, jamais `raise Exception`.

---

### 2. Hooks (`hooks.py`)

Le fichier `hooks.py` est le point d'entrée pour toutes les fonctions **globales** et transversales de l'application :

- **`doc_events`** : interception d'événements sur n'importe quel DocType
- **`scheduler_events`** : tâches planifiées (cron)
- **`on_login` / `on_logout`** : actions sur les sessions utilisateur
- **`jinja`** : fonctions utilitaires exposées dans les templates Jinja
- **`fixtures`** : données à exporter/importer avec l'app

**Exemple de structure :**

```python
doc_events = {
    "Sales Order": {
        "on_submit": "flow.events.sales_order.on_submit"
    }
}

scheduler_events = {
    "daily": [
        "flow.tasks.daily_sync"
    ]
}
```

**Règles :**
- Les hooks pointent vers des fonctions dans des modules dédiés (`flow/events/`, `flow/tasks/`).
- Aucune logique métier inline dans `hooks.py` — uniquement des références de chemin.
- Les hooks globaux (multi-DocTypes) vont dans `hooks.py`. Les hooks spécifiques à un DocType restent dans le `.py` du DocType.

---

### 3. CSS — Tailwind

**Tailwind CSS** est le seul système de style utilisé dans Flow.

- Les classes utilitaires Tailwind sont appliquées directement dans les templates HTML et les composants Vue.js.
- Aucune feuille de style CSS personnalisée n'est créée sans justification.
- Aucun framework CSS tiers (Bootstrap custom, Material, etc.) n'est utilisé.

**Règles :**
- Utiliser les classes Tailwind standard en priorité.
- Les composants Vue.js utilisent des classes Tailwind dans les balises `<template>`.
- Si un style ne peut pas être exprimé en Tailwind, documenter la raison avant d'ajouter du CSS custom.

---

## Organisation des fichiers

```
flow/
  hooks.py                  ← configuration globale de l'app
  doctype/                  ← DocTypes Frappe
    <nom_du_doctype>/
      <nom_du_doctype>.json ← définition du schéma
      <nom_du_doctype>.py   ← logique métier (Server Script)
      <nom_du_doctype>.js   ← logique client (form scripts)
  events/                   ← handlers pour les hooks doc_events
  tasks/                    ← tâches planifiées (scheduler_events)
  templates/                ← templates HTML/Jinja
  public/
    js/                     ← JavaScript global
    css/                    ← CSS custom (Tailwind only, si nécessaire)
```

---

## Principes généraux

- **Frappe en premier** : toujours utiliser les APIs Frappe natives avant de chercher une solution externe.
- **Pas de dépendance externe** sans accord explicite.
- **Logique dans le bon endroit** : DocType-specific → fichier `.py` du DocType ; global → `hooks.py` + modules dédiés.
- **Lisibilité** : nommer les fonctions et les hooks de façon explicite et en français ou en anglais de manière cohérente dans tout le projet.
