# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.utils import today, add_months, getdate, fmt_money


def get_context(context):
	"""Portail fournisseur — accès aux commandes, factures et paiements."""
	# ------------------------------------------------------------------ #
	#  Authentification & rôle                                             #
	# ------------------------------------------------------------------ #
	if frappe.session.user == "Guest":
		frappe.throw(_("Veuillez vous connecter."), frappe.PermissionError)

	roles = frappe.get_roles()
	if not ({"Supplier", "System Manager"} & set(roles)):
		frappe.throw(_("Accès réservé aux fournisseurs."), frappe.PermissionError)

	# ------------------------------------------------------------------ #
	#  Résolution Supplier depuis l'utilisateur connecté                   #
	# ------------------------------------------------------------------ #
	supplier_name = _get_supplier_for_user(frappe.session.user)
	if not supplier_name and "System Manager" not in roles:
		frappe.throw(_("Aucun fournisseur lié à votre compte. Contactez l'administrateur."))

	# ------------------------------------------------------------------ #
	#  Données ERP (silencieux si ERPNext absent)                          #
	# ------------------------------------------------------------------ #
	purchase_orders = []
	invoices = []
	payments = []
	stats = frappe._dict(open_orders=0, pending_invoices=0, paid_amount=0, overdue_count=0)
	erp_available = frappe.db.table_exists("tabPurchase Order")

	if erp_available and supplier_name:
		purchase_orders = _get_purchase_orders(supplier_name)
		invoices = _get_invoices(supplier_name)
		payments = _get_recent_payments(supplier_name)
		stats = _compute_stats(purchase_orders, invoices)

	# ------------------------------------------------------------------ #
	#  Contexte template                                                   #
	# ------------------------------------------------------------------ #
	context.supplier_name = supplier_name
	context.purchase_orders = purchase_orders
	context.invoices = invoices
	context.payments = payments
	context.stats = stats
	context.erp_available = erp_available
	context.today = today()
	context.title = _("Mon espace fournisseur")
	context.no_cache = 1


# ------------------------------------------------------------------ #
#  Helpers privés                                                      #
# ------------------------------------------------------------------ #

def _get_supplier_for_user(user_email: str) -> str | None:
	"""Résout le Supplier lié à un utilisateur via Contact → Dynamic Link."""
	try:
		contact = frappe.db.get_value(
			"Contact Email",
			{"email_id": user_email, "parenttype": "Contact"},
			"parent",
		)
		if not contact:
			return None
		return frappe.db.get_value(
			"Dynamic Link",
			{"parent": contact, "parenttype": "Contact", "link_doctype": "Supplier"},
			"link_name",
		)
	except Exception:
		return None


def _get_purchase_orders(supplier: str) -> list[dict]:
	"""Retourne les commandes d'achat actives du fournisseur."""
	try:
		return frappe.db.get_all(
			"Purchase Order",
			filters={"supplier": supplier, "docstatus": 1, "status": ["!=", "Cancelled"]},
			fields=["name", "transaction_date", "schedule_date", "status",
					"grand_total", "currency", "per_received", "per_billed"],
			order_by="transaction_date desc",
			limit=50,
		)
	except Exception:
		return []


def _get_invoices(supplier: str) -> list[dict]:
	"""Retourne les factures d'achat du fournisseur."""
	try:
		return frappe.db.get_all(
			"Purchase Invoice",
			filters={"supplier": supplier, "docstatus": 1},
			fields=["name", "posting_date", "due_date", "status",
					"grand_total", "outstanding_amount", "currency"],
			order_by="posting_date desc",
			limit=50,
		)
	except Exception:
		return []


def _get_recent_payments(supplier: str) -> list[dict]:
	"""Retourne les 10 derniers paiements au fournisseur."""
	try:
		return frappe.db.get_all(
			"Payment Entry",
			filters={"party_type": "Supplier", "party": supplier, "docstatus": 1},
			fields=["name", "posting_date", "paid_amount", "currency",
					"mode_of_payment", "reference_no"],
			order_by="posting_date desc",
			limit=10,
		)
	except Exception:
		return []


def _compute_stats(purchase_orders: list, invoices: list) -> frappe._dict:
	stats = frappe._dict(open_orders=0, pending_invoices=0, paid_amount=0, overdue_count=0)
	stats.open_orders = sum(
		1 for o in purchase_orders if o.get("status") in ("To Receive and Bill", "To Bill", "To Receive")
	)
	for inv in invoices:
		outstanding = inv.get("outstanding_amount") or 0
		if outstanding > 0:
			stats.pending_invoices += 1
			if inv.get("due_date") and getdate(inv["due_date"]) < getdate(today()):
				stats.overdue_count += 1
		else:
			total = inv.get("grand_total") or 0
			stats.paid_amount += total
	return stats
