# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class FSMMandate(Document):

	def validate(self):
		self._sync_orders_status()

	def after_insert(self):
		"""Crée automatiquement les FSO définis dans le gabarit dès la première sauvegarde."""
		if self.fsm_mandate_template and not self.orders:
			self.create_orders_from_template()

	def _sync_orders_status(self):
		"""Met à jour le statut du mandat selon l'avancement de ses ordres."""
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

	# ------------------------------------------------------------------ #
	#  Actions métier                                                      #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def create_orders_from_template(self):
		"""Crée tous les FSO définis dans le gabarit de mandat.
		Ne re-crée pas si les ordres existent déjà.
		Retourne la liste des noms de FSO créés.
		"""
		if not self.fsm_mandate_template:
			frappe.throw(_("Aucun gabarit de mandat sélectionné."))
		if self.orders:
			frappe.throw(
				_("Des bons de travail existent déjà pour ce mandat. "
				  "Utilisez 'Créer un bon' pour en ajouter un manuellement.")
			)

		tpl = frappe.get_cached_doc("FSM Mandate Template", self.fsm_mandate_template)
		if not tpl.template_orders:
			frappe.throw(_("Le gabarit '{0}' ne contient aucun bon de travail.").format(tpl.template_name))

		created = []
		for line in tpl.template_orders:
			order = frappe.new_doc("Field Service Order")
			order.title = line.title
			order.fsm_mandate = self.name
			order.priority = line.priority or "Normal"

			# Gabarit FSO : les champs du FSM Template seront appliqués dans apply_template_on_new
			if line.fsm_template:
				order.fsm_template = line.fsm_template

			# Remplace l'activité du gabarit si spécifiée sur la ligne
			if line.activity_type:
				order.activity_type = line.activity_type

			if line.scheduled_duration:
				order.scheduled_duration = line.scheduled_duration

			if line.description:
				order.description = line.description

			# Données communes héritées du mandat
			order.customer = self.customer
			order.customer_name = self.customer_name
			if self.project_name:
				order.project_name = self.project_name
			if self.company:
				order.company = self.company
			if self.fsm_location:
				order.fsm_location = self.fsm_location
			if self.scheduled_date:
				order.scheduled_date = self.scheduled_date

			# Techniciens : selon la règle de la ligne
			role_rule = getattr(line, "assigned_technician_role", None) or "Tous les techniciens du mandat"
			if role_rule == "Lead seulement":
				lead = next((t for t in self.technicians if t.is_lead), None)
				if lead:
					order.append("technicians", {
						"employee": lead.employee,
						"employee_name": lead.employee_name,
						"is_lead": 1,
						"user_id": lead.user_id,
					})
			else:
				for t in self.technicians:
					order.append("technicians", {
						"employee": t.employee,
						"employee_name": t.employee_name,
						"is_lead": t.is_lead,
						"user_id": t.user_id,
					})

			order.insert(ignore_permissions=True)
			created.append(order.name)

		# Lie les FSO créés au mandat
		for name in created:
			self.append("orders", {"field_service_order": name})
		self.db_update()
		frappe.db.commit()

		frappe.msgprint(
			_("{0} bon(s) de travail créé(s) : {1}").format(
				len(created),
				", ".join(
					frappe.utils.get_link_to_form("Field Service Order", n) for n in created
				),
			),
			title=_("Bons de travail générés"),
		)
		return created

	@frappe.whitelist()
	def create_order_from_mandate(self, title, fsm_template=None):
		"""Crée un FSO unique pré-rempli depuis les données du mandat (ajout manuel)."""
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
		self.append("orders", {"field_service_order": order.name})
		self.db_update()
		frappe.db.commit()
		return order.name

	@frappe.whitelist()
	def sync_shared_data_to_orders(self):
		"""Propage les données communes (client, lieu, date, techs) à tous les FSO non clôturés."""
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

		if updated:
			frappe.msgprint(
				_("{0} bon(s) de travail mis à jour.").format(len(updated)),
				alert=True,
			)
		return updated
