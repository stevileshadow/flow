# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, now_datetime, time_diff_in_hours


class FieldServiceOrder(Document):

	# ------------------------------------------------------------------ #
	#  Validation                                                          #
	# ------------------------------------------------------------------ #

	def validate(self):
		self.set_actual_duration()
		self.calculate_parts_total()
		self.calculate_timesheet_total()
		self.calculate_total_amount()
		self.sync_timesheet_hours()

	def set_actual_duration(self):
		"""Calcule la durée réelle entre actual_start et actual_end."""
		if self.actual_start and self.actual_end:
			self.actual_duration = flt(
				time_diff_in_hours(self.actual_end, self.actual_start), 2
			)

	def calculate_parts_total(self):
		"""Calcule le montant de chaque ligne pièce et le total."""
		total = 0.0
		for row in self.parts:
			row.amount = flt(row.qty) * flt(row.rate)
			total += row.amount
		self.total_parts_amount = total

	def calculate_timesheet_total(self):
		"""Calcule heures et montant facturable sur chaque ligne timesheet."""
		total_hours = 0.0
		total_amount = 0.0
		for row in self.timesheets:
			if row.from_time and row.to_time:
				row.hours = flt(time_diff_in_hours(row.to_time, row.from_time), 2)
			if row.is_billable:
				row.billing_hours = row.billing_hours or row.hours
				row.billing_amount = flt(row.billing_hours) * flt(row.hourly_rate)
				total_amount += row.billing_amount
			total_hours += flt(row.hours)
		self.total_hours = flt(total_hours, 2)
		self.total_timesheet_amount = total_amount

	def calculate_total_amount(self):
		"""Calcule le montant total selon le type de facturation."""
		if self.billing_type == "Forfait":
			self.total_amount = flt(self.fixed_price)
		elif self.billing_type == "Gratuit":
			self.total_amount = 0.0
		else:
			# Temps et Matériaux / Heures prépayées
			self.total_amount = flt(self.total_parts_amount) + flt(self.total_timesheet_amount)

	def sync_timesheet_hours(self):
		"""Met à jour actual_start/end depuis les lignes timesheet si non renseigné."""
		if not self.timesheets:
			return
		times = [
			(row.from_time, row.to_time)
			for row in self.timesheets
			if row.from_time
		]
		if not times:
			return
		if not self.actual_start:
			self.actual_start = min(t[0] for t in times)
		if not self.actual_end:
			ends = [t[1] for t in times if t[1]]
			if ends:
				self.actual_end = max(ends)

	# ------------------------------------------------------------------ #
	#  Actions métier                                                      #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def start_intervention(self):
		"""Démarre l'intervention : passe en 'En cours' et note l'heure de début."""
		if self.status not in ("Nouveau", "Planifié"):
			frappe.throw(_("Impossible de démarrer une intervention au statut '{0}'").format(self.status))
		self.actual_start = now_datetime()
		self.status = "En cours"
		self.save()
		frappe.msgprint(_("Intervention démarrée le {0}").format(self.actual_start), alert=True)

	@frappe.whitelist()
	def end_intervention(self):
		"""Termine l'intervention : passe en 'Terminé' et note l'heure de fin."""
		if self.status != "En cours":
			frappe.throw(_("L'intervention n'est pas en cours (statut actuel : {0})").format(self.status))
		if not self.actual_start:
			frappe.throw(_("La date de début réelle est manquante."))
		self.actual_end = now_datetime()
		self.set_actual_duration()
		self.status = "Terminé"
		self.save()
		frappe.msgprint(
			_("Intervention terminée. Durée : {0} h").format(self.actual_duration),
			alert=True,
		)

	@frappe.whitelist()
	def create_invoice(self):
		"""Crée une facture client (Sales Invoice) depuis l'ordre d'intervention."""
		if self.status not in ("Terminé",):
			frappe.throw(_("Veuillez d'abord terminer l'intervention avant de facturer."))
		if self.invoice:
			frappe.throw(_("Une facture {0} existe déjà pour cet ordre.").format(self.invoice))
		if self.billing_type == "Gratuit":
			frappe.throw(_("Cette intervention est gratuite — pas de facture à créer."))

		invoice = frappe.new_doc("Sales Invoice")
		invoice.customer = self.customer
		invoice.company = self.company
		invoice.field_service_order = self.name

		# Lignes pièces/produits
		for part in self.parts:
			invoice.append("items", {
				"item_code": part.item_code,
				"item_name": part.item_name,
				"description": part.description or part.item_name,
				"qty": part.qty,
				"uom": part.uom,
				"rate": part.rate,
				"amount": part.amount,
				"warehouse": part.warehouse,
			})

		# Lignes main-d'œuvre (temps facturables)
		if self.billing_type == "Temps et Matériaux":
			for ts in self.timesheets:
				if ts.is_billable and ts.billing_hours:
					invoice.append("items", {
						"item_name": _("Main-d'œuvre — {0}").format(ts.activity_type or ts.employee_name),
						"description": ts.description or _("Temps technicien"),
						"qty": ts.billing_hours,
						"uom": "Hour",
						"rate": ts.hourly_rate,
						"amount": ts.billing_amount,
					})
		elif self.billing_type == "Forfait":
			invoice.append("items", {
				"item_name": _("Forfait intervention — {0}").format(self.title),
				"description": self.description or self.title,
				"qty": 1,
				"rate": self.fixed_price,
				"amount": self.fixed_price,
			})

		invoice.insert(ignore_permissions=True)
		invoice.submit()

		self.db_set("invoice", invoice.name)
		self.db_set("status", "Facturé")

		frappe.msgprint(
			_("Facture {0} créée avec succès.").format(
				frappe.utils.get_link_to_form("Sales Invoice", invoice.name)
			),
			title=_("Facture créée"),
		)
		return invoice.name

	# ------------------------------------------------------------------ #
	#  Événements Frappe                                                   #
	# ------------------------------------------------------------------ #

	def on_submit(self):
		if self.status == "Nouveau":
			self.db_set("status", "Planifié")

	def on_cancel(self):
		self.db_set("status", "Annulé")
		if self.invoice:
			frappe.throw(
				_("Impossible d'annuler : la facture {0} est déjà émise. Annulez d'abord la facture.").format(
					self.invoice
				)
			)

	def before_print(self, settings=None):
		"""Prépare les données pour le rapport d'intervention PDF."""
		self.signature_html = (
			f'<img src="{self.customer_signature}" style="max-height:80px;"/>'
			if self.customer_signature
			else ""
		)


# ------------------------------------------------------------------ #
#  API publique                                                        #
# ------------------------------------------------------------------ #

@frappe.whitelist()
def get_open_orders_for_technician(employee):
	"""Retourne les interventions ouvertes pour un technicien (usage mobile/portail)."""
	return frappe.get_all(
		"Field Service Order",
		filters={
			"assigned_to": employee,
			"status": ["in", ["Planifié", "En cours", "En attente de pièces"]],
		},
		fields=[
			"name", "title", "status", "priority", "customer_name",
			"scheduled_date", "scheduled_time", "address_display", "activity_type",
		],
		order_by="scheduled_date asc, scheduled_time asc",
	)


@frappe.whitelist()
def get_dashboard_data():
	"""Données pour le tableau de bord Field Service."""
	statuses = ["Nouveau", "Planifié", "En cours", "En attente de pièces", "Terminé", "Facturé"]
	result = {}
	for status in statuses:
		result[status] = frappe.db.count("Field Service Order", {"status": status})
	return result
