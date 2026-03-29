# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Intégrations ERPNext (Projets, RH, Paie, Comptabilité) + Rapport décompte mensuel XLSX."""

import io
import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now_datetime, today


# ═══════════════════════════════════════════════════════════════════════════════
#  PROJETS ERPNext
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def sync_mandate_to_erp_project(mandate_name):
	"""Crée ou met à jour un projet ERPNext depuis un FSM Mandate."""
	mandate = frappe.get_doc("FSM Mandate", mandate_name)
	company = mandate.company or frappe.defaults.get_global_default("company")

	if mandate.erp_project and frappe.db.exists("Project", mandate.erp_project):
		project = frappe.get_doc("Project", mandate.erp_project)
		project.project_name = f"FSM — {mandate.mandate_name}"
		project.customer = mandate.customer
		project.expected_start_date = mandate.scheduled_date
		project.save(ignore_permissions=True)
	else:
		project = frappe.new_doc("Project")
		project.project_name = f"FSM — {mandate.mandate_name}"
		project.customer = mandate.customer
		project.company = company
		project.expected_start_date = mandate.scheduled_date
		project.status = "Open"
		project.insert(ignore_permissions=True)
		mandate.db_set("erp_project", project.name)

	# Sync tâches pour chaque FSO du mandat
	for row in mandate.orders:
		if row.field_service_order:
			fso = frappe.get_doc("Field Service Order", row.field_service_order)
			_sync_fso_to_task(fso, project.name)

	frappe.msgprint(
		_("Projet ERPNext {0} synchronisé.").format(
			frappe.utils.get_link_to_form("Project", project.name)
		), alert=True,
	)
	return project.name


def sync_fso_task_status(order_name):
	"""Met à jour la tâche ERPNext liée au FSO (appelé depuis end_intervention)."""
	fso = frappe.get_doc("Field Service Order", order_name)
	if not fso.erp_task:
		# Cherche le projet via le mandat
		if fso.fsm_mandate:
			project_name = frappe.db.get_value("FSM Mandate", fso.fsm_mandate, "erp_project")
			if project_name:
				_sync_fso_to_task(fso, project_name)
		return
	_sync_fso_to_task(fso, None)


def _sync_fso_to_task(fso, project_name):
	"""Crée ou met à jour la tâche ERPNext pour un FSO."""
	status = _fso_to_task_status(fso.status)

	if fso.erp_task and frappe.db.exists("Task", fso.erp_task):
		frappe.db.set_value("Task", fso.erp_task, {
			"status": status,
			"act_start_date": getdate(fso.actual_start) if fso.actual_start else None,
			"completed_on": getdate(fso.actual_end) if fso.status in ("Terminé", "Facturé") and fso.actual_end else None,
			"actual_time": flt(fso.actual_duration),
		})
		return

	if not project_name:
		return

	task = frappe.new_doc("Task")
	task.project = project_name
	task.subject = fso.title
	task.status = status
	task.exp_start_date = fso.scheduled_date
	task.expected_time = flt(fso.scheduled_duration)
	if fso.assigned_to:
		user_id = frappe.db.get_value("Employee", fso.assigned_to, "user_id")
		if user_id:
			task.assigned_to = user_id
	task.insert(ignore_permissions=True)
	fso.db_set("erp_task", task.name, update_modified=False)


def _fso_to_task_status(status):
	return {
		"Nouveau": "Open", "Planifié": "Open",
		"En cours": "Working",
		"En attente de pièces": "Pending Review",
		"Terminé": "Completed", "Facturé": "Completed",
		"Annulé": "Cancelled",
	}.get(status, "Open")


# ═══════════════════════════════════════════════════════════════════════════════
#  RH — TIMESHEETS ERPNext (pour paie)
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def create_erp_timesheets(order_name):
	"""Crée les Timesheets ERPNext depuis les lignes consolidées du FSO."""
	fso = frappe.get_doc("Field Service Order", order_name)

	if not fso.timesheets:
		frappe.throw(_("Aucune ligne de timesheet sur cet ordre."))
	if fso.erp_timesheet:
		frappe.throw(_("Timesheet déjà créée ({0}).").format(fso.erp_timesheet))

	settings = _get_settings()
	project_name = frappe.db.get_value("FSM Mandate", fso.fsm_mandate, "erp_project") \
		if fso.fsm_mandate else None
	default_activity = getattr(settings, "timesheet_activity_type", None) or "Field Service"
	company = fso.company or frappe.defaults.get_global_default("company")

	# Regroupe les lignes par employé
	by_emp = {}
	for row in fso.timesheets:
		emp = row.employee or fso.assigned_to
		if emp:
			by_emp.setdefault(emp, []).append(row)

	created = []
	for employee, lines in by_emp.items():
		ts = frappe.new_doc("Timesheet")
		ts.employee = employee
		ts.company = company
		for line in lines:
			if not line.from_time:
				continue
			ts.append("time_logs", {
				"activity_type": line.activity_type or default_activity,
				"from_time": line.from_time,
				"to_time": line.to_time,
				"hours": flt(line.hours),
				"is_billable": line.is_billable,
				"billing_hours": flt(line.billing_hours or line.hours),
				"billing_rate": flt(line.hourly_rate),
				"project": project_name,
				"task": fso.erp_task or None,
				"is_break": 1 if getattr(line, "is_break", 0) else 0,
			})
		if not ts.time_logs:
			continue
		ts.insert(ignore_permissions=True)
		ts.submit()
		created.append(ts.name)

	if created:
		fso.db_set("erp_timesheet", created[0], update_modified=False)

	# Heures supplémentaires → Additional Salary
	if getattr(settings, "auto_create_additional_salary", 0) and getattr(settings, "overtime_salary_component", None):
		_create_overtime_additional_salary(fso, settings, by_emp)

	frappe.msgprint(
		_("{0} Timesheet(s) ERPNext créée(s) : {1}").format(
			len(created),
			", ".join(frappe.utils.get_link_to_form("Timesheet", n) for n in created),
		),
		title=_("Timesheets créées"),
	)
	return created


def _create_overtime_additional_salary(fso, settings, by_emp):
	"""Crée des Additional Salary pour les heures supplémentaires si seuil dépassé."""
	threshold = flt(getattr(settings, "overtime_threshold", 8)) or 8.0
	component = settings.overtime_salary_component
	company = fso.company or frappe.defaults.get_global_default("company")
	payroll_date = getdate(fso.actual_end) if fso.actual_end else getdate(today())

	for employee, lines in by_emp.items():
		total_h = sum(flt(l.hours) for l in lines if not l.is_break)
		overtime_h = max(0.0, total_h - threshold)
		if overtime_h <= 0:
			continue
		rate = flt(getattr(settings, "default_hourly_rate", 0))
		sal = frappe.new_doc("Additional Salary")
		sal.employee = employee
		sal.salary_component = component
		sal.amount = overtime_h * rate
		sal.payroll_date = payroll_date
		sal.company = company
		sal.ref_doctype = "Field Service Order"
		sal.ref_docname = fso.name
		sal.overwrite_salary_structure_amount = 0
		sal.insert(ignore_permissions=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  COMPTABILITÉ — Journal Entry main-d'œuvre
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def create_labor_journal_entry(order_name):
	"""Crée une écriture comptable pour les coûts MO d'un FSO clôturé."""
	fso = frappe.get_doc("Field Service Order", order_name)

	if fso.status not in ("Terminé", "Facturé"):
		frappe.throw(_("L'intervention doit être terminée avant de créer l'écriture."))
	if fso.erp_journal_entry:
		frappe.throw(_("Écriture déjà créée : {0}").format(fso.erp_journal_entry))

	settings = _get_settings()
	if not getattr(settings, "labor_expense_account", None):
		frappe.throw(_("Configurez le compte de charge MO dans FSM Settings."))
	if not getattr(settings, "employee_payable_account", None):
		frappe.throw(_("Configurez le compte à payer employés dans FSM Settings."))

	total_mo = flt(fso.total_timesheet_amount)
	if not total_mo:
		rate = flt(getattr(settings, "default_hourly_rate", 0))
		total_mo = flt(fso.total_hours) * rate
	if not total_mo:
		frappe.throw(_("Aucun montant MO calculable (saisissez des heures ou configurez le taux horaire)."))

	company = fso.company or frappe.defaults.get_global_default("company")
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = getdate(fso.actual_end) if fso.actual_end else getdate(today())
	je.user_remark = _("Coût MO — {0} : {1}").format(fso.name, fso.title)
	je.append("accounts", {
		"account": settings.labor_expense_account,
		"debit_in_account_currency": total_mo,
		"cost_center": getattr(settings, "default_cost_center", None) or None,
		"reference_type": "Field Service Order",
		"reference_name": fso.name,
	})
	je.append("accounts", {
		"account": settings.employee_payable_account,
		"credit_in_account_currency": total_mo,
		"reference_type": "Field Service Order",
		"reference_name": fso.name,
	})
	je.insert(ignore_permissions=True)
	fso.db_set("erp_journal_entry", je.name, update_modified=False)

	frappe.msgprint(
		_("Écriture comptable MO créée : {0}").format(
			frappe.utils.get_link_to_form("Journal Entry", je.name)
		), alert=True,
	)
	return je.name


# ═══════════════════════════════════════════════════════════════════════════════
#  DÉCOMPTE MENSUEL — données + téléchargement XLSX
# ═══════════════════════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_monthly_decompte_data(month, year):
	"""Retourne les données de décompte en JSON (aperçu portail)."""
	return _collect_data(int(month), int(year))


@frappe.whitelist()
def download_monthly_decompte(month, year):
	"""Génère et télécharge le rapport XLSX multi-feuilles."""
	month, year = int(month), int(year)
	data = _collect_data(month, year)
	xlsx = _build_xlsx(data, month, year)
	month_names = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
	               "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
	frappe.local.response["filename"] = f"decompte_{year}_{month:02d}_{month_names[month]}.xlsx"
	frappe.local.response["filecontent"] = xlsx
	frappe.local.response["type"] = "download"
	frappe.local.response["content_type"] = (
		"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
	)


# ─── Collecte des données ──────────────────────────────────────────────────────

def _collect_data(month, year):
	"""
	Collecte consolidée Odoo-style : toutes les sources d'heures employé
	(terrain FSM + internes ERPNext + congés + primes) sont agrégées
	pour produire un décompte de paie réel par employé.

	Architecture (inspirée D365 / Odoo) :
	  GPS Punch → FSM Timesheet → ERPNext Timesheet   (heures terrain)
	  ERPNext Timesheet (sans FSO)                     (heures internes)
	  Leave Application                                (absences)
	  Additional Salary                                (primes/commissions)
	  ─────────────────────────────────────────────────────────────────
	  → total_payable_hours, estimated_pay par employé
	"""
	import datetime
	import calendar as _cal
	start = datetime.date(year, month, 1)
	_, last_day = _cal.monthrange(year, month)
	end = datetime.date(year, month, last_day)
	start_str, end_str = str(start), str(end)

	orders = frappe.get_all(
		"Field Service Order",
		filters={"scheduled_date": ["between", [start_str, end_str]], "docstatus": ["!=", 2]},
		fields=[
			"name", "title", "status", "priority", "billing_type",
			"customer", "customer_name",
			"assigned_to", "assigned_to_name",
			"fsm_mandate", "fsm_team", "activity_type",
			"scheduled_date", "actual_start", "actual_end", "actual_duration",
			"total_hours", "total_parts_amount", "total_rental_amount",
			"total_timesheet_amount", "total_amount",
			"invoice", "company", "erp_task", "erp_timesheet",
		],
		order_by="scheduled_date asc",
	)

	# ── Techniciens — heures terrain FSM ────────────────────────────────
	tech_map = {}
	for o in orders:
		emp = o.assigned_to or "_sans"
		name = o.assigned_to_name or emp
		t = tech_map.setdefault(emp, {
			"employee": emp, "name": name,
			"nb_orders": 0, "nb_completed": 0,
			"terrain_hours": 0.0, "billable_hours": 0.0,
			"labor_amount": 0.0, "mandats": set(),
			# Sources additionnelles (remplies ci-dessous)
			"internal_hours": 0.0,
			"leave_hours": 0.0,
			"additional_salary": 0.0,
			"total_payable_hours": 0.0,
			"estimated_pay": 0.0,
		})
		t["nb_orders"] += 1
		if o.status in ("Terminé", "Facturé"):
			t["nb_completed"] += 1
		t["terrain_hours"] += flt(o.total_hours)
		t["labor_amount"] += flt(o.total_timesheet_amount)
		if o.fsm_mandate:
			t["mandats"].add(o.fsm_mandate)

	# Heures facturables depuis les lignes FSM Timesheet
	order_names = [o.name for o in orders]
	if order_names:
		ts_rows = frappe.db.sql("""
			SELECT parent, employee, SUM(billing_hours) as bh, SUM(hours) as h
			FROM `tabField Service Timesheet Line`
			WHERE parent IN %(names)s AND is_billable=1
			GROUP BY parent, employee
		""", {"names": order_names}, as_dict=True)
		for row in ts_rows:
			emp = row.employee or "_sans"
			if emp in tech_map:
				tech_map[emp]["billable_hours"] += flt(row.bh or row.h)

	# ── Source 2 : Heures internes ERPNext Timesheet (non-FSO) ──────────
	try:
		erp_ts = frappe.db.sql("""
			SELECT tsd.employee, SUM(tsd.hours) as h
			FROM `tabTimesheet Detail` tsd
			JOIN `tabTimesheet` ts ON ts.name = tsd.parent
			WHERE ts.start_date >= %(start)s
			  AND ts.end_date   <= %(end)s
			  AND ts.docstatus  != 2
			  AND (tsd.project IS NULL OR tsd.project = '')
			GROUP BY tsd.employee
		""", {"start": start_str, "end": end_str}, as_dict=True)
		for row in erp_ts:
			emp = row.employee or "_sans"
			if emp in tech_map:
				tech_map[emp]["internal_hours"] += flt(row.h)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Decompte: ERPNext Timesheet query")

	# ── Source 3 : Congés ERPNext (Leave Application approuvées) ────────
	all_employees = [e for e in tech_map if e != "_sans"]
	if all_employees:
		try:
			leaves = frappe.db.sql("""
				SELECT employee, SUM(total_leave_days) as days
				FROM `tabLeave Application`
				WHERE employee IN %(emps)s
				  AND from_date <= %(end)s
				  AND to_date   >= %(start)s
				  AND status    = 'Approved'
				  AND docstatus = 1
				GROUP BY employee
			""", {"emps": all_employees, "start": start_str, "end": end_str}, as_dict=True)
			for row in leaves:
				emp = row.employee
				if emp in tech_map:
					# 1 jour congé = 8 h
					tech_map[emp]["leave_hours"] += flt(row.days) * 8.0
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Decompte: Leave Application query")

	# ── Source 4 : Primes / Additional Salary ────────────────────────────
	if all_employees:
		try:
			bonuses = frappe.db.sql("""
				SELECT employee, SUM(amount) as total
				FROM `tabAdditional Salary`
				WHERE employee IN %(emps)s
				  AND payroll_date >= %(start)s
				  AND payroll_date <= %(end)s
				  AND docstatus != 2
				GROUP BY employee
			""", {"emps": all_employees, "start": start_str, "end": end_str}, as_dict=True)
			for row in bonuses:
				emp = row.employee
				if emp in tech_map:
					tech_map[emp]["additional_salary"] += flt(row.total)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Decompte: Additional Salary query")

	# ── Source 5 : Taux horaire depuis contrat employé ──────────────────
	hourly_rates = {}
	if all_employees:
		try:
			contracts = frappe.db.sql("""
				SELECT employee, hour_rate
				FROM `tabEmployee`
				WHERE name IN %(emps)s AND hour_rate IS NOT NULL AND hour_rate > 0
			""", {"emps": all_employees}, as_dict=True)
			for c in contracts:
				hourly_rates[c.employee] = flt(c.hour_rate)
		except Exception:
			pass

	# ── Consolidation paie par employé ───────────────────────────────────
	for emp, t in tech_map.items():
		t["mandats"] = len(t["mandats"])
		t["total_hours"] = t["terrain_hours"]  # compatibilité affichage
		t.setdefault("billable_hours", t["terrain_hours"])
		# Heures payables = terrain + internes - congés (déjà payés séparément)
		t["total_payable_hours"] = max(
			0.0,
			t["terrain_hours"] + t["internal_hours"] - t["leave_hours"]
		)
		# Estimation salaire : heures × taux + primes
		rate = hourly_rates.get(emp, 0.0)
		t["estimated_pay"] = flt(t["total_payable_hours"] * rate) + t["additional_salary"]
		t["hourly_rate"] = rate

	# ── Clients ──────────────────────────────────────────────────────────
	client_map = {}
	for o in orders:
		cust = o.customer or "_sans"
		c = client_map.setdefault(cust, {
			"customer": cust, "name": o.customer_name or cust,
			"nb_orders": 0, "nb_invoiced": 0,
			"parts": 0.0, "rental": 0.0, "labor": 0.0, "total": 0.0,
		})
		c["nb_orders"] += 1
		if o.status == "Facturé":
			c["nb_invoiced"] += 1
		c["parts"] += flt(o.total_parts_amount)
		c["rental"] += flt(o.total_rental_amount)
		c["labor"] += flt(o.total_timesheet_amount)
		c["total"] += flt(o.total_amount)

	# ── Mandats / Projets ─────────────────────────────────────────────────
	mandate_names = list({o.fsm_mandate for o in orders if o.fsm_mandate})
	mandate_list = []
	for mn in mandate_names:
		m = frappe.get_cached_doc("FSM Mandate", mn)
		m_orders = [o for o in orders if o.fsm_mandate == mn]
		mandate_list.append({
			"name": mn,
			"mandate_name": m.mandate_name,
			"customer_name": m.customer_name,
			"erp_project": getattr(m, "erp_project", None),
			"status": m.status,
			"nb_orders": len(m_orders),
			"nb_completed": sum(1 for o in m_orders if o.status in ("Terminé", "Facturé")),
			"total_hours": sum(flt(o.total_hours) for o in m_orders),
			"total_amount": sum(flt(o.total_amount) for o in m_orders),
		})

	# ── Résumé global ────────────────────────────────────────────────────
	summary = {
		"month": month, "year": year,
		"nb_orders": len(orders),
		"nb_completed": sum(1 for o in orders if o.status in ("Terminé", "Facturé")),
		"nb_invoiced": sum(1 for o in orders if o.status == "Facturé"),
		"total_hours": sum(flt(o.total_hours) for o in orders),
		"total_revenue": sum(flt(o.total_amount) for o in orders),
		"total_parts": sum(flt(o.total_parts_amount) for o in orders),
		"total_rental": sum(flt(o.total_rental_amount) for o in orders),
		"total_labor": sum(flt(o.total_timesheet_amount) for o in orders),
		"nb_technicians": len(tech_map),
		"nb_clients": len(client_map),
		"nb_mandats": len(mandate_names),
		# Consolidation paie globale
		"total_payable_hours": sum(t["total_payable_hours"] for t in tech_map.values()),
		"total_estimated_pay": sum(t["estimated_pay"] for t in tech_map.values()),
		"total_additional_salary": sum(t["additional_salary"] for t in tech_map.values()),
		"total_leave_hours": sum(t["leave_hours"] for t in tech_map.values()),
		"total_internal_hours": sum(t["internal_hours"] for t in tech_map.values()),
	}

	return {
		"summary": summary,
		"orders": [dict(o) for o in orders],
		"technicians": sorted(tech_map.values(), key=lambda x: x["name"]),
		"clients": sorted(client_map.values(), key=lambda x: -x["total"]),
		"mandats": sorted(mandate_list, key=lambda x: x["mandate_name"]),
	}


# ─── Génération XLSX ───────────────────────────────────────────────────────────

def _build_xlsx(data, month, year):
	import xlsxwriter

	MONTHS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
	             "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]
	period = f"{MONTHS_FR[month]} {year}"

	output = io.BytesIO()
	wb = xlsxwriter.Workbook(output, {"in_memory": True})

	# ── Formats ────────────────────────────────────────────────────────────
	F = {
		"title":    wb.add_format({"bold": True, "font_size": 14, "font_color": "#1a3c5e", "valign": "vcenter"}),
		"section":  wb.add_format({"bold": True, "font_size": 11, "font_color": "#1a3c5e"}),
		"header":   wb.add_format({"bold": True, "bg_color": "#1a3c5e", "font_color": "white",
		                           "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True}),
		"kpi_lbl":  wb.add_format({"bold": True, "bg_color": "#e8f0fe", "border": 1}),
		"kpi_val":  wb.add_format({"bold": True, "font_size": 13, "align": "center", "border": 1, "num_format": "#,##0.0"}),
		"kpi_cur":  wb.add_format({"bold": True, "font_size": 13, "align": "center", "border": 1, "num_format": "#,##0.00 $"}),
		"kpi_int":  wb.add_format({"bold": True, "font_size": 13, "align": "center", "border": 1, "num_format": "0"}),
		"alt":      wb.add_format({"bg_color": "#f0f4ff"}),
		"cur":      wb.add_format({"num_format": "#,##0.00", "align": "right"}),
		"cur_alt":  wb.add_format({"num_format": "#,##0.00", "align": "right", "bg_color": "#f0f4ff"}),
		"num":      wb.add_format({"num_format": "#,##0.0", "align": "right"}),
		"num_alt":  wb.add_format({"num_format": "#,##0.0", "align": "right", "bg_color": "#f0f4ff"}),
		"int":      wb.add_format({"num_format": "0", "align": "center"}),
		"int_alt":  wb.add_format({"num_format": "0", "align": "center", "bg_color": "#f0f4ff"}),
		"tot":      wb.add_format({"bold": True, "top": 2, "bg_color": "#d0dff7", "num_format": "#,##0.00"}),
		"tot_int":  wb.add_format({"bold": True, "top": 2, "bg_color": "#d0dff7", "num_format": "0"}),
		"tot_lbl":  wb.add_format({"bold": True, "top": 2, "bg_color": "#d0dff7"}),
		"ok":       wb.add_format({"bg_color": "#d4edda"}),
		"warn":     wb.add_format({"bg_color": "#fff3cd", "bold": True}),
		"danger":   wb.add_format({"bg_color": "#f8d7da", "bold": True, "font_color": "#721c24"}),
		"date":     wb.add_format({"num_format": "yyyy-mm-dd", "align": "center"}),
	}
	STATUS_FMT = {
		"Terminé": wb.add_format({"bg_color": "#d4edda", "bold": True}),
		"Facturé": wb.add_format({"bg_color": "#cce5ff", "bold": True}),
		"En cours": wb.add_format({"bg_color": "#fff3cd"}),
		"Annulé":  wb.add_format({"bg_color": "#f8d7da", "font_color": "#721c24"}),
		"Planifié": wb.add_format({"bg_color": "#e2e3e5"}),
	}

	s = data["summary"]

	# ════════════════════════════════════════════════════════════════════
	#  Feuille 1 — Résumé exécutif
	# ════════════════════════════════════════════════════════════════════
	ws = wb.add_worksheet("Résumé exécutif")
	ws.set_column("A:A", 38); ws.set_column("B:B", 20)
	ws.merge_range("A1:B1", f"DÉCOMPTE MENSUEL — {period}", F["title"])
	ws.set_row(0, 28)

	kpis = [
		("Interventions (total)", s["nb_orders"], "int"),
		("Terminées + Facturées", s["nb_completed"], "int"),
		("Facturées", s["nb_invoiced"], "int"),
		("Techniciens actifs", s["nb_technicians"], "int"),
		("Clients servis", s["nb_clients"], "int"),
		("Mandats", s["nb_mandats"], "int"),
		("Heures totales", s["total_hours"], "num"),
		("Chiffre d'affaires (€)", s["total_revenue"], "cur"),
		("dont Pièces (€)", s["total_parts"], "cur"),
		("dont Location (€)", s["total_rental"], "cur"),
		("dont Main-d'œuvre (€)", s["total_labor"], "cur"),
	]
	for r, (lbl, val, fmt) in enumerate(kpis, start=2):
		ws.write(r, 0, lbl, F["kpi_lbl"])
		ws.write(r, 1, val, F[f"kpi_{fmt}"])

	r += 2
	ws.write(r, 0, "Répartition par statut", F["section"])
	for status in ["Nouveau", "Planifié", "En cours", "En attente de pièces", "Terminé", "Facturé", "Annulé"]:
		cnt = sum(1 for o in data["orders"] if o["status"] == status)
		if cnt:
			r += 1
			ws.write(r, 0, status, STATUS_FMT.get(status))
			ws.write(r, 1, cnt, F["int"])

	# ════════════════════════════════════════════════════════════════════
	#  Feuille 2 — Techniciens (RH / Paie)
	# ════════════════════════════════════════════════════════════════════
	ws2 = wb.add_worksheet("Techniciens — RH & Paie")
	ws2.set_column("A:A", 30); ws2.set_column("B:G", 18)
	ws2.merge_range("A1:G1", f"DÉCOMPTE TECHNICIENS — {period}", F["title"])
	ws2.set_row(0, 24); ws2.set_row(2, 30)

	hdrs = ["Technicien", "# Interventions", "# Terminées", "H. Totales", "H. Facturables", "Montant MO (€)", "# Mandats"]
	for c, h in enumerate(hdrs):
		ws2.write(2, c, h, F["header"])

	tot = [0.0] * len(hdrs)
	for i, t in enumerate(data["technicians"]):
		alt = i % 2 == 0
		vals = [t["name"], t["nb_orders"], t["nb_completed"], t["total_hours"], t["billable_hours"], t["labor_amount"], t["mandats"]]
		for c, v in enumerate(vals):
			if c == 0:
				ws2.write(3 + i, c, v, F["alt"] if alt else None)
			elif c in (1, 2, 6):
				ws2.write(3 + i, c, v, F["int_alt"] if alt else F["int"])
			elif c in (3, 4):
				ws2.write(3 + i, c, v, F["num_alt"] if alt else F["num"])
			else:
				ws2.write(3 + i, c, v, F["cur_alt"] if alt else F["cur"])
			if c > 0:
				tot[c] += flt(v)

	tr = 3 + len(data["technicians"]) + 1
	ws2.write(tr, 0, "TOTAL", F["tot_lbl"])
	for c in range(1, len(hdrs)):
		ws2.write(tr, c, tot[c], F["tot_int"] if c in (1, 2, 6) else F["tot"])

	# ════════════════════════════════════════════════════════════════════
	#  Feuille 3 — Clients (Comptabilité)
	# ════════════════════════════════════════════════════════════════════
	ws3 = wb.add_worksheet("Clients — Comptabilité")
	ws3.set_column("A:A", 32); ws3.set_column("B:G", 18)
	ws3.merge_range("A1:G1", f"FACTURATION CLIENTS — {period}", F["title"])
	ws3.set_row(0, 24); ws3.set_row(2, 30)

	hdrs3 = ["Client", "# Ordres", "# Facturés", "Pièces (€)", "Location (€)", "Main-d'œuvre (€)", "Total (€)"]
	for c, h in enumerate(hdrs3):
		ws3.write(2, c, h, F["header"])

	tot3 = [0.0] * len(hdrs3)
	for i, cli in enumerate(data["clients"]):
		alt = i % 2 == 0
		vals = [cli["name"], cli["nb_orders"], cli["nb_invoiced"], cli["parts"], cli["rental"], cli["labor"], cli["total"]]
		for c, v in enumerate(vals):
			if c == 0:
				ws3.write(3 + i, c, v, F["alt"] if alt else None)
			elif c in (1, 2):
				ws3.write(3 + i, c, v, F["int_alt"] if alt else F["int"])
			else:
				ws3.write(3 + i, c, v, F["cur_alt"] if alt else F["cur"])
			if c > 0:
				tot3[c] += flt(v)

	tr3 = 3 + len(data["clients"]) + 1
	ws3.write(tr3, 0, "TOTAL", F["tot_lbl"])
	for c in range(1, len(hdrs3)):
		ws3.write(tr3, c, tot3[c], F["tot_int"] if c in (1, 2) else F["tot"])

	# ════════════════════════════════════════════════════════════════════
	#  Feuille 4 — Projets / Mandats
	# ════════════════════════════════════════════════════════════════════
	ws4 = wb.add_worksheet("Projets — Mandats")
	ws4.set_column("A:B", 28); ws4.set_column("C:G", 16)
	ws4.merge_range("A1:G1", f"SUIVI PROJETS & MANDATS — {period}", F["title"])
	ws4.set_row(0, 24); ws4.set_row(2, 30)

	hdrs4 = ["Mandat", "Client", "Statut", "# FSO", "# Terminés", "Heures", "Total (€)"]
	for c, h in enumerate(hdrs4):
		ws4.write(2, c, h, F["header"])

	for i, m in enumerate(data["mandats"]):
		alt = i % 2 == 0
		vals = [m["mandate_name"], m["customer_name"], m["status"], m["nb_orders"], m["nb_completed"], m["total_hours"], m["total_amount"]]
		for c, v in enumerate(vals):
			if c in (0, 1, 2):
				ws4.write(3 + i, c, v, F["alt"] if alt else None)
			elif c in (3, 4):
				ws4.write(3 + i, c, v, F["int_alt"] if alt else F["int"])
			elif c == 5:
				ws4.write(3 + i, c, v, F["num_alt"] if alt else F["num"])
			else:
				ws4.write(3 + i, c, v, F["cur_alt"] if alt else F["cur"])

	# ════════════════════════════════════════════════════════════════════
	#  Feuille 5 — Détail complet des interventions
	# ════════════════════════════════════════════════════════════════════
	ws5 = wb.add_worksheet("Détail interventions")
	ws5.set_column("A:A", 16); ws5.set_column("B:B", 30)
	ws5.set_column("C:C", 16); ws5.set_column("D:E", 26)
	ws5.set_column("F:F", 13); ws5.set_column("G:K", 16); ws5.set_column("L:L", 18)
	ws5.merge_range("A1:L1", f"DÉTAIL DES INTERVENTIONS — {period}", F["title"])
	ws5.set_row(0, 24); ws5.set_row(2, 30)
	ws5.freeze_panes(3, 0)

	hdrs5 = ["Référence", "Titre", "Statut", "Technicien", "Client",
	         "Date planif.", "Heures", "Pièces (€)", "Location (€)", "MO (€)", "Total (€)", "Facture"]
	for c, h in enumerate(hdrs5):
		ws5.write(2, c, h, F["header"])

	for i, o in enumerate(data["orders"]):
		alt = i % 2 == 0
		row_f = F["alt"] if alt else None
		st_f = STATUS_FMT.get(o["status"], row_f)
		vals = [
			o["name"], o["title"], o["status"],
			o["assigned_to_name"] or o["assigned_to"] or "—",
			o["customer_name"] or "—",
			o["scheduled_date"],
			flt(o["total_hours"]),
			flt(o["total_parts_amount"]), flt(o["total_rental_amount"]),
			flt(o["total_timesheet_amount"]), flt(o["total_amount"]),
			o["invoice"] or "—",
		]
		for c, v in enumerate(vals):
			if c == 2:
				ws5.write(3 + i, c, v, st_f)
			elif c == 5:
				ws5.write(3 + i, c, str(v) if v else "—", F["date"] if v else row_f)
			elif c == 6:
				ws5.write(3 + i, c, v, F["num_alt"] if alt else F["num"])
			elif c in (7, 8, 9, 10):
				ws5.write(3 + i, c, v, F["cur_alt"] if alt else F["cur"])
			else:
				ws5.write(3 + i, c, v, row_f)

	# ════════════════════════════════════════════════════════════════════
	#  Feuille 6 — Alertes heures supplémentaires (RH)
	# ════════════════════════════════════════════════════════════════════
	ws6 = wb.add_worksheet("Heures sup — Alertes RH")
	ws6.set_column("A:A", 30); ws6.set_column("B:F", 20)
	ws6.merge_range("A1:F1", f"ANALYSE HEURES SUPPLÉMENTAIRES — {period}", F["title"])
	ws6.set_row(0, 24); ws6.set_row(2, 30)

	try:
		settings = frappe.get_single("FSM Settings")
		threshold = flt(getattr(settings, "overtime_threshold", 8)) or 8.0
	except Exception:
		threshold = 8.0

	ws6.write(2, 0, f"Seuil : {threshold} h/jour", F["section"])

	hdrs6 = ["Technicien", "H. Normales", "H. Supplémentaires", "# Jours dépassés", "Montant MO (€)", "Alerte"]
	for c, h in enumerate(hdrs6):
		ws6.write(3, c, h, F["header"])

	for i, t in enumerate(data["technicians"]):
		nb_days = max(1, t["nb_completed"])
		normal_h = min(t["total_hours"], threshold * nb_days)
		overtime_h = max(0.0, t["total_hours"] - normal_h)
		days_over = max(0, int(overtime_h / threshold)) if overtime_h > 0 else 0
		has_ot = overtime_h > 0
		rfmt = F["danger"] if has_ot else F["ok"]
		row_vals = [t["name"], normal_h, overtime_h, days_over, t["labor_amount"],
		            "⚠ Heures sup." if has_ot else "✓ Normal"]
		for c, v in enumerate(row_vals):
			if c in (1, 2):
				ws6.write(4 + i, c, v, F["num"])
			elif c == 3:
				ws6.write(4 + i, c, v, F["int"])
			elif c == 4:
				ws6.write(4 + i, c, v, F["cur"])
			else:
				ws6.write(4 + i, c, v, rfmt)

	wb.close()
	return output.getvalue()


# ─── Helper ────────────────────────────────────────────────────────────────────

def _get_settings():
	try:
		return frappe.get_single("FSM Settings")
	except Exception:
		return frappe._dict()
