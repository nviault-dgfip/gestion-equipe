# Gestion d'Équipe et Suivi de Consommation Prestataires

Cette application Flask permet de gérer une équipe (internes et prestataires) et de suivre la consommation des Bons de Commande (BC) des prestataires à partir d'un planning Excel.

## Fonctionnalités

- **Gestion de l'équipe** : Ajout, modification et suppression de membres (internes ou prestataires).
- **Suivi des BC** : Pour chaque prestataire, gestion de plusieurs Bons de Commande avec :
    - N° CHORUS et N° IBIS
    - Nombre de jours commandés
    - TJM (Tarif Journalier Moyen)
    - Date de début
- **Analyse du planning** : Import d'un fichier Excel de planning pour calculer automatiquement les jours consommés.
- **Tableau de bord interactif** : Visualisation de l'état de consommation, du montant consommé/restant et estimation de la date de fin des BC. Possibilité de choisir les colonnes à afficher.
- **Analyse Rétrospective** : Choix de la date d'analyse pour générer des rapports à n'importe quel point dans le temps.
- **Sécurité & Robustesse** : Protection CSRF, gestion sécurisée des fichiers (UUID), validation des entrées et mise en cache des calculs de jours fériés.
- **Export Excel** : Génération d'un rapport de suivi au format Excel.

## Installation

### Prérequis

- Python 3.x

### Dépendances

Installez les dépendances nécessaires avec pip :

```bash
pip install -r requirements.txt
```

## Utilisation

### 1. Lancer l'application

```bash
python app.py
```
L'application sera accessible sur `http://localhost:8080`.

### 2. Configurer l'équipe

Allez dans la section "Gérer l'équipe" pour ajouter vos collaborateurs. Pour les prestataires, renseignez leurs informations de société, leur pourcentage de présence et leurs bons de commande.

### 3. Générer le template de planning

Si vous n'avez pas encore de planning, vous pouvez utiliser le script `gen.py` pour générer un template Excel pour l'année en cours :

```bash
python gen.py
```
Le fichier `planning_equipe_2026.xlsx` sera généré.

### 4. Saisie du planning

Dans le fichier Excel :
- Chaque onglet correspond à un mois.
- Les colonnes correspondent aux membres de l'équipe.
- Pour chaque demi-journée travaillée, saisissez un **'X'** dans la cellule correspondante.
- L'application compte chaque 'X' comme 0.5 jour.

### 5. Importer et Analyser

Sur la page d'accueil de l'application, importez votre fichier de planning complété. L'application calculera automatiquement la consommation et affichera le rapport de suivi.

## Structure du Projet

- `app.py` : Application principale Flask.
- `gen.py` : Script utilitaire pour générer le template de planning.
- `equipe.json` : Base de données simplifiée stockant les membres et les BC.
- `templates/` : Dossier contenant les pages HTML de l'interface.
- `requirements.txt` : Liste des dépendances Python.
