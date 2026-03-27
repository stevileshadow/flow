# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Portail décompte mensuel — gestionnaire, RH, comptable, chef de projet."""

import frappe
from frappe import _
from frappe.utils import today
import datetime


ALLOWED_ROLES = {"Field Service Manager", "HR Manager", "Accounts Manager",
                 "Projects Manager", "System Manager"}


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Accès réservé aux utilisateurs connectés."), frappe.PermissionError)

	user_roles = set(frappe.get_roles(frappe.session.user))
	if not user_roles.intersection(ALLOWED_ROLES):
		frappe.throw(_("Accès non autorisé."), frappe.PermissionError)

	context.no_cache = 1
	context.show_sidebar = True
	context.title = _("Décompte mensuel")

	# Paramètres du formulaire
	now = datetime.date.today()
	try:
		month = int(frappe.form_dict.get("month") or now.month)
		year = int(frappe.form_dict.get("year") or now.year)
	except ValueError:
		month, year = now.month, now.year

	month = max(1, min(12, month))
	year = max(2020, min(2100, year))

	context.month = month
	context.year = year
	context.is_hr_manager = "HR Manager" in user_roles
	context.is_accounts_manager = "Accounts Manager" in user_roles
	context.is_projects_manager = "Projects Manager" in user_roles
	context.is_fsm_manager = "Field Service Manager" in user_roles

	# Données si formulaire soumis
	if frappe.form_dict.get("month"):
		from flow.field_service.api import _collect_data
		try:
			context.data = _collect_data(month, year)
			context.has_data = True
		except Exception as e:
			context.error = str(e)
			context.has_data = False
	else:
		context.has_data = False

	# Années disponibles pour le sélecteur
	context.years = list(range(now.year, 2019, -1))
	context.months = [
		(1, "Janvier"), (2, "Février"), (3, "Mars"), (4, "Avril"),
		(5, "Mai"), (6, "Juin"), (7, "Juillet"), (8, "Août"),
		(9, "Septembre"), (10, "Octobre"), (11, "Novembre"), (12, "Décembre"),
	]
	return context
