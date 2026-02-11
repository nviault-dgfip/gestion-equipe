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
MARCHE_FILE = "marche.json"
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

def load_marche():
    """Charge le catalogue des UOs depuis marche.json."""
    if not os.path.exists(MARCHE_FILE): return {}
    with open(MARCHE_FILE, 'r') as f:
        try:
            return json.load(f)
        except:
            return {}

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

def calculate_end_date(start_date_str, start_moment, days_to_consume, presence_pct):
    """
    Calcule la date de fin estimée d'un bon de commande en fonction :
    - De la date de début et du moment (Matin/Après-midi)
    - Du nombre de jours à consommer
    - Du pourcentage de présence du prestataire
    - Des jours ouvrés (hors week-ends et jours fériés)
    Retourne une chaîne formatée "JJ/MM/AAAA (Moment)"
    """
    if days_to_consume <= 0: return "Terminé"
    # Burn par demi-journée
    half_day_burn = (presence_pct / 100.0) / 2.0
    if half_day_burn <= 0: return "Jamais"

    try:
        current_date = datetime.strptime(str(start_date_str), "%Y-%m-%d").date()
    except:
        current_date = date.today()

    current_moment = start_moment if start_moment in ["Matin", "Après-midi"] else "Matin"
    remaining_days = float(days_to_consume)

    max_iter = 365 * 10 # Sécurité 5 ans en demi-journées
    i = 0
    while remaining_days > 0.0001 and i < max_iter:
        if not is_holiday_or_weekend(current_date):
            remaining_days -= half_day_burn
            if remaining_days <= 0.0001:
                break

        # Passage à la demi-journée suivante
        if current_moment == "Matin":
            current_moment = "Après-midi"
        else:
            current_moment = "Matin"
            current_date += timedelta(days=1)
        i += 1
           
    return f"{current_date.strftime('%d/%m/%Y')} ({current_moment})"

def process_excel(filepath, limit_date=None):
    """
    Analyse le fichier Excel de planning pour calculer la consommation par membre.
    Retourne un dictionnaire : { nom_membre: { 'YYYY-MM': nb_jours } }
    """
    try:
        xls = pd.read_excel(filepath, sheet_name=None, engine='openpyxl')
        consumption = {}
        ignored = ["Paramètres_Equipe", "Parametres", "Config"]

        if limit_date:
            limit_dt = pd.to_datetime(limit_date).date()
        else:
            limit_dt = None

        for sheet, df in xls.items():
            if any(x in sheet for x in ignored): continue
            df.columns = df.columns.astype(str).str.strip()
            if "Date" not in df.columns: continue

            if df['Date'].isnull().all(): continue
            if pd.isna(df['Date'].iloc[0]):
                first_valid_idx = df['Date'].first_valid_index()
                if first_valid_idx is not None:
                    df.loc[0:first_valid_idx, 'Date'] = df['Date'].loc[first_valid_idx]

            df['Date'] = df['Date'].ffill()
            df['Date_dt'] = pd.to_datetime(df['Date']).dt.date

            # Filtrage par date si demandé
            if limit_dt:
                df = df[df['Date_dt'] <= limit_dt]

            df['Month'] = df['Date_dt'].apply(lambda x: x.strftime('%Y-%m'))

            cols = [c for c in df.columns if c not in ["Date", "Période", "Date_dt", "Month"] and "Unnamed" not in c]
            for member in cols:
                if member not in consumption: consumption[member] = {}

                # Groupement par mois pour ce membre sur cet onglet
                sub_df = df[df[member].astype(str).str.upper().str.strip() == 'X']
                monthly_counts = sub_df.groupby('Month').size() * 0.5

                for m_key, val in monthly_counts.items():
                    consumption[member][m_key] = consumption[member].get(m_key, 0.0) + val

        return consumption
    except Exception as e:
        print(f"Erreur process: {e}")
        return {}

def generate_report_dataframe(conso_map, team, analysis_date=None):
    """
    Génère un DataFrame Pandas contenant le rapport de suivi des prestataires.
    Associe les données de consommation issues de l'Excel aux informations des BC
    définies dans l'équipe.
    analysis_date: Date de référence pour le calcul de la fin estimée.
    """
    report_data = []
    prestataires = [p for p in team if p.get('type') == 'prestataire']
    ref_date = analysis_date if analysis_date else date.today().strftime("%Y-%m-%d")
   
    for p in prestataires:
        # Nom complet (Format "NOM Prénom")
        nom_complet_display = f"{p['nom'].upper()} {p['prenom']}"
        societe = p.get('societe', '-')
       
        # Consommation Totale - Matching amélioré pour éviter les ambiguïtés
        total_consumed = 0
        p_nom = p.get('nom', '').lower().strip()
        p_prenom = p.get('prenom', '').lower().strip()

        if not p_nom and not p_prenom:
            continue

        for excel_name, val_monthly in conso_map.items():
            en_lower = str(excel_name).lower().strip()
            # Matching exact (dans les deux sens) ou présence des deux parties du nom
            if en_lower == f"{p_prenom} {p_nom}" or \
               en_lower == f"{p_nom} {p_prenom}" or \
               (p_nom in en_lower and p_prenom in en_lower):
                total_consumed = sum(val_monthly.values())
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
            start_moment = bc.get('moment_debut', 'Matin')
           
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
                # Calcul de la fin estimée à partir de la date de référence
                remaining_days = days_ordered - conso_bc
                # On commence le calcul au lendemain de la date d'analyse (Matin)
                start_calc_dt = datetime.strptime(ref_date, "%Y-%m-%d") + timedelta(days=1)
                fin_estimee = calculate_end_date(start_calc_dt.strftime("%Y-%m-%d"), "Matin", remaining_days, pct_presence)
            else:
                conso_bc = 0
                etat = "Futur"
                # Pour un BC futur, on commence soit à la date de début du BC,
                # soit au lendemain de la date de référence si le BC a théoriquement déjà commencé
                if start_date > ref_date:
                    fin_estimee = calculate_end_date(start_date, start_moment, days_ordered, pct_presence)
                else:
                    start_calc_dt = datetime.strptime(ref_date, "%Y-%m-%d") + timedelta(days=1)
                    fin_estimee = calculate_end_date(start_calc_dt.strftime("%Y-%m-%d"), "Matin", days_ordered, pct_presence)
           
            # Détail des UOs pour affichage
            uos = bc.get('uos', [])
            uo_summary = " + ".join([f"{uo['quantite']} {uo['code']}" for uo in uos]) if uos else "-"

            # Construction de la ligne selon vos propriétés demandées
            report_data.append({
                "État": etat, # Pour le filtre
                "n°Bon de Commande CHORUS": bc.get('chorus_id', '-'),
                "Composition UO": uo_summary,
                "Prestataire": societe,
                "Montant BC (K€ HT)": f"{montant_k:.2f}", # Format K€
                "N° commande IBIS": bc.get('ibis_id', '-'),
                "Jours Commandés": days_ordered,
                "NOM Prénom": nom_complet_display,
                "TJM (HT) €": f"{tjm:.2f}",
                "Date début": f"{start_date} ({start_moment})",
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
        analysis_date = request.form.get('analysis_date')
        if not analysis_date:
            analysis_date = date.today().strftime("%Y-%m-%d")

        if file:
            # Sécurité : Nom de fichier unique pour éviter les collisions
            filename = f"planning_{uuid.uuid4().hex}.xlsx"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            session['last_filepath'] = filepath
            session['analysis_date'] = analysis_date
           
            conso_map = process_excel(filepath, limit_date=analysis_date)
            team = load_team()

            prestataires = [p for p in team if p.get('type') == 'prestataire']
            if not prestataires:
                flash("Attention : Aucun prestataire n'est défini dans la base équipe. Veuillez d'abord ajouter des membres de type 'prestataire'.", "warning")
                return redirect(url_for('equipe_index'))

            df = generate_report_dataframe(conso_map, team, analysis_date=analysis_date)
           
            if df.empty:
                flash("Aucune donnée de bon de commande trouvée pour les prestataires définis.", "info")
                return redirect(url_for('index'))

            df_web = df.copy()
            if not df_web.empty:
                # Ordre des colonnes pour l'affichage Web
                cols = [
                    "n°Bon de Commande CHORUS", "Prestataire", "Composition UO", "Montant BC (K€ HT)",
                    "N° commande IBIS", "Jours Commandés", "NOM Prénom",
                    "TJM (HT) €", "Date début", "Jours Consommés",
                    "Jours Restants", "Fin Estimée", "État"
                ]
                existing_cols = [c for c in cols if c in df_web.columns]
                df_web = df_web[existing_cols]

            table_html = df_web.to_html(classes="table table-striped table-bordered align-middle table-hover", index=False)
            return render_template('dashboard.html', table=table_html)

    return render_template('index.html', today=date.today().strftime("%Y-%m-%d"))

@app.route('/export_excel')
def export_excel():
    filepath = session.get('last_filepath')
    analysis_date = session.get('analysis_date')
    if not filepath or not os.path.exists(filepath):
        return "Aucun fichier. Importez d'abord.", 400
   
    conso_map = process_excel(filepath, limit_date=analysis_date)
    team = load_team()
    df = generate_report_dataframe(conso_map, team, analysis_date=analysis_date)
   
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
    marche = load_marche()
    return render_template('team.html', team=team, marche=marche)

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
        moments = data.getlist('bc_moment[]')
        tjms = data.getlist('bc_tjm[]')
        uos_json = data.getlist('bc_uos_json[]')
       
        bcs = []
        for i in range(len(chorus)):
            if chorus[i] or ibis[i]:
                try:
                    jours_val = float(jours[i] or 0)
                    tjm_val = float(tjms[i] or 0)
                    moment_val = moments[i] if i < len(moments) else "Matin"
                    # Parsing du JSON des UOs
                    bc_uos = []
                    if i < len(uos_json) and uos_json[i]:
                        try:
                            bc_uos = json.loads(uos_json[i])
                        except:
                            bc_uos = []
                except ValueError:
                    flash(f"Erreur : Valeurs numériques invalides pour le BC {chorus[i] or ibis[i]}.", "danger")
                    return redirect(url_for('equipe_index'))

                bcs.append({
                    "chorus_id": chorus[i],
                    "ibis_id": ibis[i],
                    "jours_commandes": jours_val,
                    "date_debut": debuts[i],
                    "moment_debut": moment_val,
                    "tjm_ht": tjm_val,
                    "uos": bc_uos
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

# --- BUDGET ---

def match_member_conso(member, conso_map):
    """Retrouve la consommation mensuelle d'un membre dans la map issue de l'Excel."""
    p_nom = member.get('nom', '').lower().strip()
    p_prenom = member.get('prenom', '').lower().strip()
    if not p_nom and not p_prenom: return {}

    for excel_name, val_monthly in conso_map.items():
        en_lower = str(excel_name).lower().strip()
        if en_lower == f"{p_prenom} {p_nom}" or \
           en_lower == f"{p_nom} {p_prenom}" or \
           (p_nom in en_lower and p_prenom in en_lower):
            return val_monthly
    return {}

@app.route('/budget')
def budget_index():
    team = load_team()
    marche = load_marche()
    tva_rate = marche.get('annexe_financiere', {}).get('tva_taux_percent', 20)

    # --- 1. CALCULS MENSUELS BASÉS SUR L'EXCEL ---
    last_filepath = session.get('last_filepath')
    analysis_date = session.get('analysis_date')
    conso_map = process_excel(last_filepath, limit_date=analysis_date) if last_filepath else {}

    monthly_costs_per_member = {}
    global_monthly_costs = {}
    all_months = set()

    for p in team:
        if p.get('type') != 'prestataire': continue
        member_name = f"{p['prenom']} {p['nom']}"
        member_conso_monthly = match_member_conso(p, conso_map)

        if not member_conso_monthly: continue

        sorted_months = sorted(member_conso_monthly.keys())
        all_months.update(sorted_months)

        p_bcs = p.get('bons_commande', [])
        # On trie les BCs par date de début pour la répartition
        sorted_p_bcs = sorted(p_bcs, key=lambda x: x.get('date_debut') or '9999-99-99')

        cumulative_days_distributed = 0
        monthly_costs_per_member[member_name] = {}

        for m_key in sorted_months:
            days_to_distribute = member_conso_monthly[m_key]
            cost_this_month = 0

            while days_to_distribute > 0.0001:
                current_bc = None
                bc_start_cumul = 0
                for bc in sorted_p_bcs:
                    bc_limit = bc_start_cumul + bc.get('jours_commandes', 0)
                    if cumulative_days_distributed < bc_limit - 0.0001:
                        current_bc = bc
                        available_in_bc = bc_limit - cumulative_days_distributed
                        portion = min(days_to_distribute, available_in_bc)

                        cost_this_month += portion * bc.get('tjm_ht', 0)
                        cumulative_days_distributed += portion
                        days_to_distribute -= portion
                        break
                    bc_start_cumul = bc_limit

                if not current_bc:
                    # Plus de BC disponible : on utilise le TJM du dernier BC par défaut
                    fallback_tjm = sorted_p_bcs[-1].get('tjm_ht', 0) if sorted_p_bcs else 0
                    cost_this_month += days_to_distribute * fallback_tjm
                    cumulative_days_distributed += days_to_distribute
                    days_to_distribute = 0

            monthly_costs_per_member[member_name][m_key] = cost_this_month
            global_monthly_costs[m_key] = global_monthly_costs.get(m_key, 0) + cost_this_month

    sorted_all_months = sorted(list(all_months))

    # --- 2. CALCULS DES TOTAUX PAR BC ---
    # Construction du catalogue UO pour accès rapide aux prix
    uo_catalog = {}
    for cat in marche.get('annexe_financiere', {}).get('lots_expertises', []):
        for item in cat.get('items', []):
            uo_catalog[item['code_uo']] = item['prix_unitaire_ht_eur']

    budget_data = []
    global_summary = {
        "total_ht": 0,
        "total_ttc": 0,
        "paid_ht": 0,
        "paid_ttc": 0,
        "remaining_ht": 0,
        "remaining_ttc": 0
    }

    for p in team:
        if p.get('type') != 'prestataire': continue

        for idx, bc in enumerate(p.get('bons_commande', [])):
            # 1. Montant Total du BC
            bc_total_ht = 0
            ordered_uos = bc.get('uos', [])
            for uo in ordered_uos:
                price = uo_catalog.get(uo['code'], 0)
                bc_total_ht += price * uo['quantite']

            bc_total_ttc = bc_total_ht * (1 + tva_rate / 100)

            # 2. Montants Déjà Payés
            bc_paid_ht = 0
            paid_uos_totals = {} # Tracking par code UO

            payments = bc.get('paiements', [])
            for pay in payments:
                if pay['type'] == 'uo':
                    for uo_p in pay.get('uos', []):
                        price = uo_catalog.get(uo_p['code'], 0)
                        bc_paid_ht += price * uo_p['quantite']
                        paid_uos_totals[uo_p['code']] = paid_uos_totals.get(uo_p['code'], 0) + uo_p['quantite']
                elif pay['type'] == 'percentage':
                    bc_paid_ht += (pay['percentage'] / 100.0) * bc_total_ht

            bc_paid_ttc = bc_paid_ht * (1 + tva_rate / 100)

            # 3. Restants
            bc_rem_ht = max(0, bc_total_ht - bc_paid_ht)
            bc_rem_ttc = max(0, bc_total_ttc - bc_paid_ttc)

            # Résumé UOs pour affichage (Commandé vs Payé)
            uo_status = []
            for uo in ordered_uos:
                uo_status.append({
                    "code": uo['code'],
                    "ordered": uo['quantite'],
                    "paid": paid_uos_totals.get(uo['code'], 0),
                    "remaining": max(0, uo['quantite'] - paid_uos_totals.get(uo['code'], 0))
                })

            bc_data = {
                "member_id": p['id'],
                "member_name": f"{p['prenom']} {p['nom']}",
                "bc_index": idx,
                "chorus_id": bc.get('chorus_id', '-'),
                "ibis_id": bc.get('ibis_id', '-'),
                "total_ht": bc_total_ht,
                "total_ttc": bc_total_ttc,
                "paid_ht": bc_paid_ht,
                "paid_ttc": bc_paid_ttc,
                "remaining_ht": bc_rem_ht,
                "remaining_ttc": bc_rem_ttc,
                "uo_status": uo_status,
                "payments": payments
            }
            budget_data.append(bc_data)

            # Global
            global_summary["total_ht"] += bc_total_ht
            global_summary["total_ttc"] += bc_total_ttc
            global_summary["paid_ht"] += bc_paid_ht
            global_summary["paid_ttc"] += bc_paid_ttc
            global_summary["remaining_ht"] += bc_rem_ht
            global_summary["remaining_ttc"] += bc_rem_ttc

    return render_template('budget.html',
                           budget=budget_data,
                           summary=global_summary,
                           marche=marche,
                           today=date.today().strftime("%Y-%m-%d"),
                           monthly_costs=monthly_costs_per_member,
                           global_monthly=global_monthly_costs,
                           months=sorted_all_months)

@app.route('/budget/payer', methods=['POST'])
def budget_payer():
    team = load_team()
    marche = load_marche()

    member_id = int(request.form.get('member_id'))
    bc_index = int(request.form.get('bc_index'))
    pay_type = request.form.get('pay_type') # 'uo' or 'percentage'
    date_demande = request.form.get('date_demande')
    sf_id = request.form.get('service_fait_id', '')

    # Catalogue UO pour validation
    uo_catalog = {}
    for cat in marche.get('annexe_financiere', {}).get('lots_expertises', []):
        for item in cat.get('items', []):
            uo_catalog[item['code_uo']] = item['prix_unitaire_ht_eur']

    member = next((m for m in team if m['id'] == member_id), None)
    if not member or 'bons_commande' not in member or bc_index >= len(member['bons_commande']):
        flash("BC Introuvable.", "danger")
        return redirect(url_for('budget_index'))

    bc = member['bons_commande'][bc_index]
    if 'paiements' not in bc: bc['paiements'] = []

    if pay_type == 'uo':
        codes = request.form.getlist('pay_uo_code[]')
        qtys = request.form.getlist('pay_uo_qty[]')
        pay_uos = []

        # Validation des quantités
        paid_totals = {}
        for p in bc['paiements']:
            if p['type'] == 'uo':
                for up in p.get('uos', []):
                    paid_totals[up['code']] = paid_totals.get(up['code'], 0) + up['quantite']

        for i in range(len(codes)):
            if codes[i] and qtys[i]:
                code = codes[i]
                qty = float(qtys[i])
                ordered = next((u['quantite'] for u in bc['uos'] if u['code'] == code), 0)
                already_paid = paid_totals.get(code, 0)

                if already_paid + qty > ordered:
                    flash(f"Erreur: Quantité payée ({already_paid + qty}) supérieure à commandée ({ordered}) pour {code}.", "danger")
                    return redirect(url_for('budget_index'))

                pay_uos.append({"code": code, "quantite": qty})

        if not pay_uos:
            flash("Aucune UO sélectionnée.", "warning")
            return redirect(url_for('budget_index'))

        bc['paiements'].append({
            "type": "uo",
            "date_demande": date_demande,
            "service_fait_id": sf_id,
            "uos": pay_uos
        })

    elif pay_type == 'percentage':
        try:
            pct = float(request.form.get('percentage'))
        except:
            flash("Pourcentage invalide.", "danger")
            return redirect(url_for('budget_index'))

        # Validation (Somme des % < 100% ? Optionnel mais prudent)
        total_pct = sum(p['percentage'] for p in bc['paiements'] if p['type'] == 'percentage')
        if total_pct + pct > 100.1: # 100.1 pour tolérance flottante
            flash(f"Erreur: Le total payé dépasse 100% ({total_pct + pct}%).", "danger")
            return redirect(url_for('budget_index'))

        bc['paiements'].append({
            "type": "percentage",
            "date_demande": date_demande,
            "service_fait_id": sf_id,
            "percentage": pct
        })

    save_team_json(team)
    flash("Paiement enregistré.", "success")
    return redirect(url_for('budget_index'))

@app.route('/budget/update_sf', methods=['POST'])
def budget_update_sf():
    team = load_team()
    member_id = int(request.form.get('member_id'))
    bc_index = int(request.form.get('bc_index'))
    pay_index = int(request.form.get('pay_index'))
    sf_id = request.form.get('service_fait_id')

    member = next((m for m in team if m['id'] == member_id), None)
    if member and bc_index < len(member['bons_commande']):
        bc = member['bons_commande'][bc_index]
        if 'paiements' in bc and pay_index < len(bc['paiements']):
            bc['paiements'][pay_index]['service_fait_id'] = sf_id
            save_team_json(team)
            flash("Identifiant Service Fait mis à jour.", "success")
        else:
            flash("Paiement introuvable.", "danger")
    else:
        flash("BC introuvable.", "danger")

    return redirect(url_for('budget_index'))

if __name__ == '__main__':
    # Sécurité : Pas de debug mode en production par défaut
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(host='0.0.0.0', port=8080, debug=debug_mode)
