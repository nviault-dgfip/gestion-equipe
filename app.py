from flask import Flask, render_template, request, send_file, redirect, url_for, flash, session
from flask_wtf.csrf import CSRFProtect
import pandas as pd
import json
import os
import numpy as np
import uuid
from datetime import datetime, timedelta, date
from io import BytesIO
from functools import lru_cache
import jours_feries_france

app = Flask(__name__)
# Sécurité : Clé secrète via variable d'environnement
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev_key_chorus_ibis_gestion_change_me')
# Sécurité : Limite de taille de fichier (10 Mo)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
csrf = CSRFProtect(app)

# --- CONFIGURATION ---
JSON_FILE = "equipe.json"
UPLOAD_FOLDER = '/tmp'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- FONCTIONS UTILITAIRES ---

def load_team():
    """
    Charge la liste des membres de l'équipe depuis le fichier JSON.
    Retourne une liste vide si le fichier n'existe pas ou est invalide.
    """
    if not os.path.exists(JSON_FILE): return []
    with open(JSON_FILE, 'r') as f:
        try:
            data = json.load(f)
        except:
            return []
    return data

def save_team_json(data):
    """Sauvegarde la liste des membres de l'équipe dans le fichier JSON."""
    with open(JSON_FILE, 'w') as f:
        json.dump(data, f, indent=4)

@lru_cache(maxsize=32)
def get_holidays(year):
    """Cache les jours fériés par année pour améliorer les performances."""
    return jours_feries_france.JoursFeries.for_year(year).values()

def is_holiday_or_weekend(target_date):
    """
    Vérifie si une date donnée est un week-end ou un jour férié en France.
    """
    if target_date.weekday() >= 5: return True
    holidays = get_holidays(target_date.year)
    if target_date in holidays: return True
    return False

def calculate_end_date(start_date_str, days_to_consume, presence_pct):
    """
    Calcule la date de fin estimée d'un bon de commande en fonction :
    - De la date de début
    - Du nombre de jours à consommer
    - Du pourcentage de présence du prestataire
    - Des jours ouvrés (hors week-ends et jours fériés)
    """
    if days_to_consume <= 0: return "Terminé"
    daily_burn = presence_pct / 100.0
    if daily_burn == 0: return "Jamais"
    try:
        current_date = datetime.strptime(str(start_date_str), "%Y-%m-%d").date()
    except:
        current_date = date.today()

    remaining_days = days_to_consume
    max_iter = 365 * 5 # Sécurité pour éviter les boucles infinies
    i = 0
    while remaining_days > 0 and i < max_iter:
        current_date += timedelta(days=1)
        i += 1
        if not is_holiday_or_weekend(current_date):
            remaining_days -= daily_burn
           
    return current_date.strftime("%d/%m/%Y")

def process_excel(filepath):
    """
    Analyse le fichier Excel de planning pour calculer la consommation par membre.
    Le fichier doit contenir des colonnes correspondant aux noms des membres.
    Une cellule contenant 'X' (minuscule ou majuscule) compte pour 0.5 jour (une demi-journée).
    """
    try:
        xls = pd.read_excel(filepath, sheet_name=None, engine='openpyxl')
        consumption = {}
        ignored = ["Paramètres_Equipe", "Parametres", "Config"]
        for sheet, df in xls.items():
            if any(x in sheet for x in ignored): continue
            df.columns = df.columns.astype(str).str.strip()
            if "Date" not in df.columns: continue

            # Correction : gestion si la première date est manquante
            if df['Date'].isnull().all(): continue
            if pd.isna(df['Date'].iloc[0]):
                # On cherche la première date valide pour remplir le début si besoin
                first_valid_idx = df['Date'].first_valid_index()
                if first_valid_idx is not None:
                    df.loc[0:first_valid_idx, 'Date'] = df['Date'].loc[first_valid_idx]

            df['Date'] = df['Date'].ffill()
            cols = [c for c in df.columns if c not in ["Date", "Période"] and "Unnamed" not in c]
            for member in cols:
                if member not in consumption: consumption[member] = 0.0
                sub = df[member].dropna().astype(str).str.upper().str.strip()
                count_x = (sub == 'X').sum()
                consumption[member] += (count_x * 0.5)
        return consumption
    except Exception as e:
        print(f"Erreur process: {e}")
        return {}

def generate_report_dataframe(conso_map, team):
    """
    Génère un DataFrame Pandas contenant le rapport de suivi des prestataires.
    Associe les données de consommation issues de l'Excel aux informations des BC
    définies dans l'équipe.
    """
    report_data = []
    prestataires = [p for p in team if p.get('type') == 'prestataire']
   
    for p in prestataires:
        # Nom complet (Format "NOM Prénom")
        nom_complet_display = f"{p['nom'].upper()} {p['prenom']}"
        societe = p.get('societe', '-')
       
        # Consommation Totale - Matching amélioré pour éviter les ambiguïtés
        total_consumed = 0
        name_parts = [p['nom'].lower(), p['prenom'].lower()]

        for excel_name, val in conso_map.items():
            en_lower = excel_name.lower()
            # Matching exact (dans les deux sens) ou présence des deux parties du nom
            if en_lower == f"{p['prenom']} {p['nom']}".lower() or \
               en_lower == f"{p['nom']} {p['prenom']}".lower() or \
               (name_parts[0] in en_lower and name_parts[1] in en_lower):
                total_consumed = val
                break
       
        pct_presence = float(p.get('presence_pct', 100))
       
        # Gestion des BCs
        bcs = p.get('bons_commande', [])
        bcs.sort(key=lambda x: x.get('date_debut') or '9999-99-99')
       
        consumed_buffer = total_consumed
       
        for bc in bcs:
            days_ordered = float(bc.get('jours_commandes', 0))
            tjm = float(bc.get('tjm_ht', 0))
            start_date = bc.get('date_debut', date.today().strftime("%Y-%m-%d"))
           
            # Calcul du montant total du BC en K€ (HT)
            montant_k = (days_ordered * tjm) / 1000.0
           
            # Logique de consommation (Bucket)
            if consumed_buffer >= days_ordered:
                conso_bc = days_ordered
                etat = "Terminé"
                consumed_buffer -= days_ordered
                fin_estimee = "Clôturé"
            elif consumed_buffer > 0:
                conso_bc = consumed_buffer
                etat = "En cours"
                consumed_buffer = 0
                # Correction : Calcul de la fin estimée à partir d'aujourd'hui pour le reste à faire
                remaining_days = days_ordered - conso_bc
                fin_estimee = calculate_end_date(date.today().strftime("%Y-%m-%d"), remaining_days, pct_presence)
            else:
                conso_bc = 0
                etat = "Futur"
                fin_estimee = calculate_end_date(start_date, days_ordered, pct_presence)
           
            # Construction de la ligne selon vos propriétés demandées
            report_data.append({
                "État": etat, # Pour le filtre
                "n°Bon de Commande CHORUS": bc.get('chorus_id', '-'),
                "Prestataire": societe,
                "Montant BC (K€ HT)": f"{montant_k:.2f}", # Format K€
                "N° commande IBIS": bc.get('ibis_id', '-'),
                "Jours Commandés": days_ordered,
                "NOM Prénom": nom_complet_display,
                "TJM (HT) €": f"{tjm:.2f}",
                "Date début": start_date,
                "Jours Consommés": conso_bc,
                "Jours Restants": days_ordered - conso_bc,
                "Fin Estimée": fin_estimee
            })
           
    df = pd.DataFrame(report_data)
    return df

# --- ROUTES ---

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        file = request.files.get('file')
        if file:
            # Sécurité : Nom de fichier unique pour éviter les collisions
            filename = f"planning_{uuid.uuid4().hex}.xlsx"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            session['last_filepath'] = filepath
           
            conso_map = process_excel(filepath)
            team = load_team()
            df = generate_report_dataframe(conso_map, team)
           
            df_web = df.copy()
            if not df_web.empty:
                # Ordre des colonnes pour l'affichage Web
                cols = [
                    "n°Bon de Commande CHORUS", "Prestataire", "Montant BC (K€ HT)",
                    "N° commande IBIS", "Jours Commandés", "NOM Prénom",
                    "TJM (HT) €", "Date début", "Jours Consommés",
                    "Jours Restants", "Fin Estimée", "État"
                ]
                existing_cols = [c for c in cols if c in df_web.columns]
                df_web = df_web[existing_cols]

            table_html = df_web.to_html(classes="table table-striped table-bordered align-middle table-hover", index=False)
            return render_template('dashboard.html', table=table_html)

    return render_template('index.html')

@app.route('/export_excel')
def export_excel():
    filepath = session.get('last_filepath')
    if not filepath or not os.path.exists(filepath):
        return "Aucun fichier. Importez d'abord.", 400
   
    conso_map = process_excel(filepath)
    team = load_team()
    df = generate_report_dataframe(conso_map, team)
   
    # Nettoyage de la colonne 'État' pour l'export Excel (optionnel)
    if 'État' in df.columns:
        # On peut vouloir garder l'état ou le supprimer selon le besoin du fichier ODS final
        # Ici je le garde en dernière position
        pass

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Suivi Chorus IBIS')
       
        # Auto-ajustement largeurs colonnes
        worksheet = writer.sheets['Suivi Chorus IBIS']
        for column_cells in worksheet.columns:
            length = max(len(str(cell.value)) for cell in column_cells)
            worksheet.column_dimensions[column_cells[0].column_letter].width = length + 2

    output.seek(0)
    return send_file(output, download_name=f"Suivi_Prestataires_{datetime.now().strftime('%Y-%m-%d')}.xlsx", as_attachment=True, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route('/equipe')
def equipe_index():
    team = load_team()
    return render_template('team.html', team=team)

@app.route('/equipe/save', methods=['POST'])
def equipe_save():
    team = load_team()
    data = request.form
    member_id = data.get('id')
   
    # Validation des entrées numériques
    try:
        presence_pct = int(data.get('presence_pct') or 100)
    except ValueError:
        flash("Erreur : Le pourcentage de présence doit être un nombre entier.", "danger")
        return redirect(url_for('equipe_index'))

    new_member = {
        "type": data.get('type'),
        "nom": data.get('nom'),
        "prenom": data.get('prenom'),
        "societe": data.get('societe'),
        "presence_pct": presence_pct
    }
   
    if data.get('type') == 'prestataire':
        # Récupération des champs BC modifiés
        chorus = data.getlist('bc_chorus[]')
        ibis = data.getlist('bc_ibis[]')
        jours = data.getlist('bc_jours[]')
        debuts = data.getlist('bc_debut[]')
        tjms = data.getlist('bc_tjm[]')
       
        bcs = []
        for i in range(len(chorus)):
            if chorus[i] or ibis[i]:
                try:
                    jours_val = float(jours[i] or 0)
                    tjm_val = float(tjms[i] or 0)
                except ValueError:
                    flash(f"Erreur : Valeurs numériques invalides pour le BC {chorus[i] or ibis[i]}.", "danger")
                    return redirect(url_for('equipe_index'))

                bcs.append({
                    "chorus_id": chorus[i],
                    "ibis_id": ibis[i],
                    "jours_commandes": jours_val,
                    "date_debut": debuts[i],
                    "tjm_ht": tjm_val
                })
        new_member['bons_commande'] = bcs

    if member_id:
        for i, m in enumerate(team):
            if str(m.get('id')) == str(member_id):
                new_member['id'] = m['id']
                team[i] = new_member
                break
    else:
        new_id = 1
        if team: new_id = max(m.get('id', 0) for m in team) + 1
        new_member['id'] = new_id
        team.append(new_member)

    save_team_json(team)
    flash("Données mises à jour.", "success")
    return redirect(url_for('equipe_index'))

@app.route('/equipe/delete/<int:id>', methods=['POST'])
def equipe_delete(id):
    team = load_team()
    new_team = [m for m in team if m.get('id') != id]
    save_team_json(new_team)
    flash("Supprimé.", "warning")
    return redirect(url_for('equipe_index'))

if __name__ == '__main__':
    # Sécurité : Pas de debug mode en production par défaut
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=8080, debug=debug_mode)
