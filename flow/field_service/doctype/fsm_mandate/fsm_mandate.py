# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class FSMMandate(Document):

	def validate(self):
		self._sync_orders_status()

	def _sync_orders_status(self):
		"""Met à jour le statut du mandat selon l'état de ses ordres."""
		if not self.orders:
			return
		statuses = [
			frappe.db.get_value("Field Service Order", row.field_service_order, "status") or ""
			for row in self.orders
			if row.field_service_order
		]
		if not statuses:
			return
		if all(s in ("Terminé", "Facturé") for s in statuses):
			self.status = "Terminé"
		elif any(s in ("En cours", "Terminé", "Facturé") for s in statuses):
			self.status = "En cours"

	@frappe.whitelist()
	def create_order_from_mandate(self, title, fsm_template=None):
		"""Crée un nouveau FSO pré-rempli depuis les données du mandat."""
		order = frappe.new_doc("Field Service Order")
		order.title = title
		order.fsm_mandate = self.name
		order.customer = self.customer
		order.customer_name = self.customer_name
		order.project_name = self.project_name
		order.company = self.company
		order.fsm_location = self.fsm_location
		order.scheduled_date = self.scheduled_date
		if fsm_template:
			order.fsm_template = fsm_template
		for t in self.technicians:
			order.append("technicians", {
				"employee": t.employee,
				"employee_name": t.employee_name,
				"is_lead": t.is_lead,
				"user_id": t.user_id,
			})
		order.insert(ignore_permissions=True)
		# Enregistre dans la liste du mandat
		self.append("orders", {"field_service_order": order.name})
		self.save(ignore_permissions=True)
		return order.name

	@frappe.whitelist()
	def sync_shared_data_to_orders(self):
		"""Propage les données partagées (client, lieu, date, techs) à tous les FSO du mandat."""
		updated = []
		for row in self.orders:
			if not row.field_service_order:
				continue
			fso = frappe.get_doc("Field Service Order", row.field_service_order)
			if fso.status in ("Terminé", "Facturé", "Annulé"):
				continue
			fso.customer = self.customer
			fso.customer_name = self.customer_name
			if self.project_name:
				fso.project_name = self.project_name
			if self.company:
				fso.company = self.company
			if self.fsm_location:
				fso.fsm_location = self.fsm_location
			if self.scheduled_date:
				fso.scheduled_date = self.scheduled_date
			if self.technicians:
				fso.technicians = []
				for t in self.technicians:
					fso.append("technicians", {
						"employee": t.employee,
						"employee_name": t.employee_name,
						"is_lead": t.is_lead,
						"user_id": t.user_id,
					})
			fso.save(ignore_permissions=True)
			updated.append(fso.name)
		return updated
