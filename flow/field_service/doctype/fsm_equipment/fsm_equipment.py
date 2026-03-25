# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, today


class FSMEquipment(Document):

	# ------------------------------------------------------------------ #
	#  Validation                                                          #
	# ------------------------------------------------------------------ #

	def validate(self):
		self.validate_no_circular_parent()
		self.set_complete_name()
		self.check_warranty_expiry()

	def validate_no_circular_parent(self):
		if not self.parent_equipment:
			return
		parent = self.parent_equipment
		visited = set()
		while parent:
			if parent == self.name:
				frappe.throw(
					_("Référence circulaire : {0} ne peut pas être son propre parent.").format(self.name)
				)
			if parent in visited:
				break
			visited.add(parent)
			parent = frappe.db.get_value("FSM Equipment", parent, "parent_equipment")

	def set_complete_name(self):
		"""Calcule le chemin : Groupe > Sous-groupe > Équipement."""
		parts = [self.equipment_name]
		parent = self.parent_equipment
		depth = 0
		while parent and depth < 10:
			parent_name = frappe.db.get_value("FSM Equipment", parent, "equipment_name")
			if parent_name:
				parts.insert(0, parent_name)
			parent = frappe.db.get_value("FSM Equipment", parent, "parent_equipment")
			depth += 1
		self.complete_name = " > ".join(parts)

	def check_warranty_expiry(self):
		"""Alerte si la garantie est expirée ou expire dans 30 jours."""
		if not self.warranty_expiry:
			return
		from frappe.utils import getdate
		expiry = getdate(self.warranty_expiry)
		today_date = getdate(today())
		if expiry < today_date:
			frappe.msgprint(
				_("La garantie de {0} est expirée depuis le {1}.").format(
					self.equipment_name, self.warranty_expiry
				),
				indicator="red",
				alert=True,
			)
		elif (expiry - today_date).days <= 30:
			frappe.msgprint(
				_("La garantie de {0} expire dans {1} jours ({2}).").format(
					self.equipment_name,
					(expiry - today_date).days,
					self.warranty_expiry,
				),
				indicator="orange",
				alert=True,
			)

	# ------------------------------------------------------------------ #
	#  Événements                                                          #
	# ------------------------------------------------------------------ #

	def on_update(self):
		self._update_children_complete_name()

	def _update_children_complete_name(self):
		children = frappe.get_all(
			"FSM Equipment",
			filters={"parent_equipment": self.name},
			fields=["name"],
		)
		for child in children:
			child_doc = frappe.get_doc("FSM Equipment", child.name)
			child_doc.set_complete_name()
			child_doc.db_set("complete_name", child_doc.complete_name)
			child_doc._update_children_complete_name()

	# ------------------------------------------------------------------ #
	#  Méthodes utilitaires                                                #
	# ------------------------------------------------------------------ #

	def update_after_service(self, service_date=None):
		"""Appelé depuis Field Service Order à la complétion : met à jour les dates."""
		service_date = service_date or today()
		self.db_set("last_service_date", service_date)
		if self.maintenance_interval_days:
			self.db_set(
				"next_service_date",
				add_days(service_date, self.maintenance_interval_days),
			)

	def get_intervention_history(self, limit=10):
		"""Retourne les dernières interventions sur cet équipement."""
		return frappe.get_all(
			"Field Service Order",
			filters={"fsm_equipment": self.name},
			fields=["name", "title", "status", "scheduled_date", "assigned_to_name"],
			order_by="scheduled_date desc",
			limit=limit,
		)

	def get_children_count(self):
		return frappe.db.count("FSM Equipment", {"parent_equipment": self.name})


# ------------------------------------------------------------------ #
#  API publique                                                        #
# ------------------------------------------------------------------ #

@frappe.whitelist()
def get_equipment_tree(parent=None, location=None):
	"""Retourne l'arbre des équipements pour un tree-select."""
	filters = {"parent_equipment": parent or ["is", "not set"]}
	if location:
		filters["fsm_location"] = location
	equipments = frappe.get_all(
		"FSM Equipment",
		filters=filters,
		fields=[
			"name", "equipment_name", "complete_name", "status",
			"serial_no", "model", "manufacturer", "fsm_location",
			"customer_name", "warranty_expiry", "next_service_date",
		],
		order_by="equipment_name asc",
	)
	for eq in equipments:
		eq["has_children"] = bool(
			frappe.db.count("FSM Equipment", {"parent_equipment": eq["name"]})
		)
	return equipments


@frappe.whitelist()
def get_equipment_for_order(equipment_name):
	"""Pré-remplit les données d'un ordre depuis l'équipement sélectionné."""
	eq = frappe.get_doc("FSM Equipment", equipment_name)
	data = {
		"fsm_location": eq.fsm_location,
		"customer": eq.customer,
		"customer_name": eq.customer_name,
	}
	# Si le lieu est renseigné, récupère aussi ses données
	if eq.fsm_location:
		loc_data = frappe.get_cached_doc("FSM Location", eq.fsm_location)
		data.update({
			"contact_person": loc_data.contact_person,
			"customer_address": loc_data.address,
			"fsm_team": loc_data.fsm_team,
			"directions": loc_data.directions,
		})
	return data
