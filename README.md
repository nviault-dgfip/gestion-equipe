# Gestion d'Équipe et Suivi de Consommation Prestataires

Cette application Flask permet de gérer une équipe (internes et prestataires) et de suivre la consommation des Bons de Commande (BC) des prestataires à partir d'un planning Excel.

## Fonctionnalités

- **Gestion de l'équipe** : Ajout, modification et suppression de membres (internes ou prestataires). Support de la **consommation initiale** (jours consommés hors planning).
- **Suivi des BC** : Pour chaque prestataire, gestion de plusieurs Bons de Commande avec :
    - N° CHORUS et N° IBIS
    - Nombre de jours commandés et composition en Unités d'Oeuvre (UO)
    - TJM (Tarif Journalier Moyen) calculé automatiquement
    - Date de début et moment de début (Matin/Après-midi)
- **Analyse du planning multi-années** : Import de fichiers Excel de planning. Les données sont **mémorisées** et cumulées entre plusieurs fichiers (ex: 2025 et 2026).
- **Tableau de bord interactif** : Visualisation de l'état de consommation, du montant consommé/restant et estimation de la date de fin des BC. Filtrage par état (En cours, Terminé, Futur) et personnalisation des colonnes.
- **Suivi Budgétaire** : Module dédié pour suivre les coûts mensuels, les paiements effectués par UO ou pourcentage, et le reste à payer (HT/TTC).
- **Analyse Rétrospective** : Choix de la date d'analyse pour figer la consommation à une date passée et recalculer les projections.
- **Sécurité & Robustesse** : Protection CSRF, gestion sécurisée des fichiers (UUID), validation des entrées et mise en cache des calculs de jours fériés.
- **Export Excel** : Génération d'un rapport de suivi complet au format Excel.

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

Si vous n'avez pas encore de planning, vous pouvez utiliser le script `gen.py` pour générer un template Excel pour une année spécifique :

```bash
python gen.py 2026
```
Le fichier `planning_equipe_format_NN_2026.xlsx` sera généré.

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
- `consommation.json` : Historique mémorisé des jours travaillés et consommation initiale.
- `marche.json` : Catalogue des Unités d'Oeuvre (UO) et configurations financières.
- `templates/` : Dossier contenant les pages HTML de l'interface.
- `requirements.txt` : Liste des dépendances Python.
