# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Page portail client — détail d'une intervention."""

import frappe
from frappe import _


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(_("Connectez-vous pour accéder à cette page."), frappe.PermissionError)

	order_name = frappe.form_dict.get("name") or context.get("name")
	if not order_name:
		frappe.throw(_("Intervention introuvable."), frappe.DoesNotExistError)

	order = frappe.get_doc("Field Service Order", order_name)

	# Vérifier que l'ordre appartient bien au client connecté
	from flow.field_service.www.my.interventions import _get_customer_for_user
	customer = _get_customer_for_user(frappe.session.user)
	if order.customer != customer:
		frappe.throw(_("Accès non autorisé."), frappe.PermissionError)

	context.no_cache = 1
	context.show_sidebar = True
	context.title = order.title
	context.order = order
	context.maps_url = None

	if order.fsm_location:
		loc = frappe.get_cached_doc("FSM Location", order.fsm_location)
		context.maps_url = loc.get_google_maps_url()

	return context
