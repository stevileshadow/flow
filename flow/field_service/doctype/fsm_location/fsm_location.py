# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_link_to_form


class FSMLocation(Document):

	# ------------------------------------------------------------------ #
	#  Validation                                                          #
	# ------------------------------------------------------------------ #

	def validate(self):
		self.validate_no_circular_parent()
		self.set_complete_name()

	def validate_no_circular_parent(self):
		"""Empêche qu'un lieu soit son propre ancêtre."""
		if not self.parent_location:
			return
		parent = self.parent_location
		visited = set()
		while parent:
			if parent == self.name:
				frappe.throw(
					_("Référence circulaire détectée : {0} ne peut pas être son propre parent.").format(
						self.name
					)
				)
			if parent in visited:
				break
			visited.add(parent)
			parent = frappe.db.get_value("FSM Location", parent, "parent_location")

	def set_complete_name(self):
		"""Calcule le chemin hiérarchique : Grand-parent > Parent > Ce lieu."""
		parts = [self.location_name]
		parent = self.parent_location
		depth = 0
		while parent and depth < 10:
			parent_name = frappe.db.get_value("FSM Location", parent, "location_name")
			if parent_name:
				parts.insert(0, parent_name)
			parent = frappe.db.get_value("FSM Location", parent, "parent_location")
			depth += 1
		self.complete_name = " > ".join(parts)

	# ------------------------------------------------------------------ #
	#  Événements                                                          #
	# ------------------------------------------------------------------ #

	def on_update(self):
		"""Propage la mise à jour du complete_name aux enfants directs."""
		self._update_children_complete_name()

	def _update_children_complete_name(self):
		children = frappe.get_all(
			"FSM Location",
			filters={"parent_location": self.name},
			fields=["name"],
		)
		for child in children:
			child_doc = frappe.get_doc("FSM Location", child.name)
			child_doc.set_complete_name()
			child_doc.db_set("complete_name", child_doc.complete_name)
			child_doc._update_children_complete_name()

	# ------------------------------------------------------------------ #
	#  Méthodes utilitaires                                                #
	# ------------------------------------------------------------------ #

	def get_primary_worker(self):
		"""Retourne le technicien principal de ce lieu."""
		for w in self.assigned_workers:
			if w.is_primary:
				return w.employee
		return self.assigned_workers[0].employee if self.assigned_workers else None

	def get_google_maps_url(self):
		"""Génère un lien Google Maps si les coordonnées sont renseignées."""
		if self.latitude and self.longitude:
			return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"
		if self.address_display:
			import urllib.parse
			return f"https://www.google.com/maps/search/{urllib.parse.quote(self.address_display)}"
		return None

	def get_sublocation_count(self):
		return frappe.db.count("FSM Location", {"parent_location": self.name})

	def get_open_order_count(self):
		return frappe.db.count(
			"Field Service Order",
			{
				"fsm_location": self.name,
				"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
			},
		)


# ------------------------------------------------------------------ #
#  API publique                                                        #
# ------------------------------------------------------------------ #

@frappe.whitelist()
def get_location_tree(parent=None):
	"""Retourne l'arbre des lieux pour un composant tree-select."""
	filters = {"parent_location": parent or ["is", "not set"]}
	locations = frappe.get_all(
		"FSM Location",
		filters=filters,
		fields=["name", "location_name", "complete_name", "customer_name",
		        "latitude", "longitude", "fsm_team"],
		order_by="location_name asc",
	)
	for loc in locations:
		loc["has_children"] = bool(
			frappe.db.count("FSM Location", {"parent_location": loc["name"]})
		)
	return locations


@frappe.whitelist()
def get_location_for_order(location_name):
	"""Retourne les données d'un lieu pour pré-remplir un ordre d'intervention."""
	loc = frappe.get_doc("FSM Location", location_name)
	return {
		"customer": loc.customer,
		"customer_name": loc.customer_name,
		"contact_person": loc.contact_person,
		"customer_address": loc.address,
		"address_display": loc.address_display,
		"fsm_team": loc.fsm_team,
		"assigned_to": loc.get_primary_worker(),
		"directions": loc.directions,
		"maps_url": loc.get_google_maps_url(),
	}
