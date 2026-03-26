# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""Page de signature client — accessible via lien tokenisé (sans session obligatoire)."""

import frappe
from frappe import _


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False

	token = frappe.form_dict.get("token")
	if not token:
		frappe.throw(_("Lien de signature manquant ou invalide."), frappe.DoesNotExistError)

	order_name = frappe.db.get_value(
		"Field Service Order", {"signature_token": token}, "name"
	)
	if not order_name:
		context.invalid_token = True
		context.title = _("Lien expiré")
		return context

	order = frappe.get_doc("Field Service Order", order_name)
	context.order = order
	context.token = token
	context.title = _("Signature — {0}").format(order.title)
	context.invalid_token = False
	context.already_signed = bool(order.customer_signature) and not order.signature_token
	return context
