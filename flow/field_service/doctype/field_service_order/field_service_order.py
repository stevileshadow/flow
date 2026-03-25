# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, date_diff, flt, get_datetime, getdate, now_datetime, time_diff_in_hours, today


class FieldServiceOrder(Document):

	# ------------------------------------------------------------------ #
	#  Validation                                                          #
	# ------------------------------------------------------------------ #

	def validate(self):
		self.apply_template_on_new()
		self.prefill_from_equipment()
		self.prefill_from_location()
		self.sync_stage_and_status()
		self._handle_status_transitions()
		self.apply_sla()
		self.update_sla_status()
		self.set_actual_duration()
		self.calculate_parts_total()
		self.calculate_timesheet_total()
		self.calculate_total_amount()
		self.sync_timesheet_hours()
		self._warn_technician_conflict()
		self._warn_low_stock()

	def apply_template_on_new(self):
		"""Applique le modèle uniquement lors de la création (doc is new)."""
		if not self.fsm_template or not self.is_new():
			return
		tpl = frappe.get_cached_doc("FSM Template", self.fsm_template)
		if tpl.activity_type and not self.activity_type:
			self.activity_type = tpl.activity_type
		if tpl.billing_type and not self.billing_type:
			self.billing_type = tpl.billing_type
		if tpl.fsm_team and not self.fsm_team:
			self.fsm_team = tpl.fsm_team
		if tpl.priority and not self.priority:
			self.priority = tpl.priority
		if tpl.scheduled_duration and not self.scheduled_duration:
			self.scheduled_duration = tpl.scheduled_duration
		if tpl.description and not self.description:
			self.description = tpl.description
		if tpl.instructions and not self.internal_notes:
			self.internal_notes = tpl.instructions
		for part in tpl.default_parts:
			self.append("parts", {
				"item_code": part.item_code,
				"item_name": part.item_name,
				"qty": part.qty,
				"uom": part.uom,
				"part_type": getattr(part, "part_type", "Consommée") or "Consommée",
				"rental_rate": getattr(part, "rental_rate", None),
				"rental_unit": getattr(part, "rental_unit", None) or "Jour",
			})

	def prefill_from_equipment(self):
		"""Auto-remplit lieu et client depuis l'équipement sélectionné."""
		if not self.fsm_equipment:
			return
		eq = frappe.get_cached_doc("FSM Equipment", self.fsm_equipment)
		if eq.fsm_location and not self.fsm_location:
			self.fsm_location = eq.fsm_location
		if eq.customer and not self.customer:
			self.customer = eq.customer
			self.customer_name = eq.customer_name

	def prefill_from_location(self):
		"""Auto-remplit client, contact, adresse et équipe depuis FSM Location."""
		if not self.fsm_location:
			return
		# Ne pas écraser les champs déjà renseignés manuellement
		loc = frappe.get_cached_doc("FSM Location", self.fsm_location)
		if not self.customer and loc.customer:
			self.customer = loc.customer
			self.customer_name = loc.customer_name
		if not self.contact_person and loc.contact_person:
			self.contact_person = loc.contact_person
		if not self.customer_address and loc.address:
			self.customer_address = loc.address
		if not self.fsm_team and loc.fsm_team:
			self.fsm_team = loc.fsm_team
		if not self.assigned_to and loc.assigned_workers:
			primary = next((w.employee for w in loc.assigned_workers if w.is_primary), None)
			if primary:
				self.assigned_to = primary
		if not self.directions and loc.directions:
			self.directions = loc.directions

	def sync_stage_and_status(self):
		"""Synchronise fsm_stage ↔ status bidirectionnellement.
		- Si fsm_stage change → met à jour status
		- Si status change sans fsm_stage → cherche l'étape correspondante
		"""
		if self.fsm_stage:
			stage = frappe.get_cached_doc("FSM Stage", self.fsm_stage)
			# Synchronise le champ status lisible depuis le stage
			self.status = stage.stage_name
		elif self.status:
			# Cherche l'étape dont le nom correspond au status
			stage_name = frappe.db.get_value(
				"FSM Stage", {"stage_name": self.status, "stage_type": "Ordre"}, "name"
			)
			if stage_name:
				self.fsm_stage = stage_name
		else:
			# Cherche l'étape par défaut
			default = frappe.db.get_value(
				"FSM Stage", {"stage_type": "Ordre", "is_default": 1}, "name"
			)
			if default:
				self.fsm_stage = default
				self.status = default

	def set_actual_duration(self):
		"""Calcule la durée réelle entre actual_start et actual_end."""
		if self.actual_start and self.actual_end:
			self.actual_duration = flt(
				time_diff_in_hours(self.actual_end, self.actual_start), 2
			)

	def calculate_parts_total(self):
		"""Calcule le montant de chaque ligne pièce (consommée ou location) et les totaux."""
		total_consumed = 0.0
		total_rental = 0.0
		for row in self.parts:
			if getattr(row, "part_type", "Consommée") == "En location":
				row.amount = _rental_amount(row)
				total_rental += row.amount
			else:
				row.amount = flt(row.qty) * flt(row.rate)
				total_consumed += row.amount
		self.total_parts_amount = total_consumed
		self.total_rental_amount = total_rental

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
			self.total_amount = (
				flt(self.total_parts_amount)
				+ flt(self.total_timesheet_amount)
				+ flt(self.total_rental_amount)
			)

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

	def _warn_technician_conflict(self):
		"""E — Avertit (sans bloquer) si le technicien a un autre ordre le même jour."""
		if not self.assigned_to or not self.scheduled_date:
			return
		filters = {
			"assigned_to": self.assigned_to,
			"scheduled_date": self.scheduled_date,
			"status": ["not in", ["Terminé", "Facturé", "Annulé"]],
		}
		if not self.is_new():
			filters["name"] = ["!=", self.name]
		conflicts = frappe.get_all(
			"Field Service Order",
			filters=filters,
			fields=["name", "title", "scheduled_time"],
			limit=5,
		)
		if not conflicts:
			return
		lines = ", ".join(
			f"{c.name} ({c.scheduled_time or _('heure n/d')})" for c in conflicts
		)
		frappe.msgprint(
			_("{0} a déjà {1} autre(s) intervention(s) le {2} : {3}").format(
				self.assigned_to_name or self.assigned_to,
				len(conflicts),
				self.scheduled_date,
				lines,
			),
			title=_("Conflit d'agenda"),
			indicator="orange",
		)

	def _warn_low_stock(self):
		"""G — Avertit (sans bloquer) si une pièce manque de stock dans l'entrepôt."""
		warnings = []
		for row in self.parts:
			if not row.item_code or not row.warehouse or not flt(row.qty):
				continue
			available = flt(
				frappe.db.get_value(
					"Bin",
					{"item_code": row.item_code, "warehouse": row.warehouse},
					"actual_qty",
				) or 0
			)
			if available < flt(row.qty):
				warnings.append(
					_("{0} : {1} disponible(s), {2} requis(e)(s) — {3}").format(
						row.item_name or row.item_code,
						available,
						row.qty,
						row.warehouse,
					)
				)
		if warnings:
			frappe.msgprint(
				"<br>".join(warnings),
				title=_("Stock insuffisant"),
				indicator="orange",
			)

	def _handle_status_transitions(self):
		"""Gère en un seul passage (B) la pause SLA et (C partiel) le statut équipement
		lors des changements de statut sauvegardés via validate."""
		if self.is_new():
			return
		old_status = frappe.db.get_value("Field Service Order", self.name, "status")
		if old_status == self.status:
			return

		HOLD = "En attente de pièces"

		# B — Pause / Reprise SLA
		if self.sla_policy:
			pause_on_hold = frappe.db.get_value("FSM SLA Policy", self.sla_policy, "pause_on_hold")
			if pause_on_hold:
				if self.status == HOLD and old_status != HOLD:
					# Entrée en attente : enregistre l'heure de suspension
					self.sla_paused_since = now_datetime()
				elif old_status == HOLD and self.status != HOLD and self.sla_paused_since:
					# Sortie d'attente : prolonge les deadlines du temps de suspension
					paused_hours = time_diff_in_hours(now_datetime(), get_datetime(self.sla_paused_since))
					if self.sla_response_due:
						self.sla_response_due = add_to_date(
							get_datetime(self.sla_response_due), hours=paused_hours
						)
					if self.sla_resolution_due:
						self.sla_resolution_due = add_to_date(
							get_datetime(self.sla_resolution_due), hours=paused_hours
						)
					self.sla_paused_since = None

		# C — Sync statut équipement (transitions passant par validate)
		if self.fsm_equipment:
			if self.status == HOLD:
				frappe.db.set_value("FSM Equipment", self.fsm_equipment, "status", HOLD)
			elif old_status == HOLD and self.status == "En cours":
				# Retour en cours après attente de pièces
				frappe.db.set_value("FSM Equipment", self.fsm_equipment, "status", "En maintenance")

	# ------------------------------------------------------------------ #
	#  Actions métier                                                      #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def start_intervention(self):
		"""Démarre l'intervention : passe au stage 'En cours'."""
		closed_stages = _get_closed_stage_names()
		if self.status in closed_stages:
			frappe.throw(_("Impossible de démarrer une intervention au statut '{0}'").format(self.status))
		stage = frappe.db.get_value(
			"FSM Stage", {"stage_name": "En cours", "stage_type": "Ordre"}, "name"
		)
		self.actual_start = now_datetime()
		if stage:
			self.fsm_stage = stage
		self.status = "En cours"
		# Auto-remplir date_sortie pour les pièces en location sans date encore définie
		today_date = today()
		for part in self.parts:
			if getattr(part, "part_type", "Consommée") == "En location" and not part.date_sortie:
				part.date_sortie = today_date
		self.save()
		# C — Équipement → En maintenance dès le démarrage
		if self.fsm_equipment:
			frappe.db.set_value("FSM Equipment", self.fsm_equipment, "status", "En maintenance")
		# Stock Entry de sortie pour les pièces en location (si pas déjà créé)
		if not self.rental_stock_entry:
			rental_se = self._create_rental_departure_entry()
			if rental_se:
				self.db_set("rental_stock_entry", rental_se)
		frappe.msgprint(_("Intervention démarrée le {0}").format(self.actual_start), alert=True)

	@frappe.whitelist()
	def end_intervention(self):
		"""Termine l'intervention : passe au stage 'Terminé'."""
		if self.status != "En cours":
			frappe.throw(_("L'intervention n'est pas en cours (statut actuel : {0})").format(self.status))
		if not self.actual_start:
			frappe.throw(_("La date de début réelle est manquante."))
		self.actual_end = now_datetime()
		self.set_actual_duration()
		stage = frappe.db.get_value(
			"FSM Stage", {"stage_name": "Terminé", "stage_type": "Ordre"}, "name"
		)
		if stage:
			self.fsm_stage = stage
		self.status = "Terminé"
		self.save()
		# C — Équipement → Actif à la clôture + mise à jour dates maintenance
		if self.fsm_equipment:
			eq_status = frappe.db.get_value("FSM Equipment", self.fsm_equipment, "status")
			if eq_status in ("En maintenance", "En attente de pièces"):
				frappe.db.set_value("FSM Equipment", self.fsm_equipment, "status", "Actif")
			eq = frappe.get_doc("FSM Equipment", self.fsm_equipment)
			eq.update_after_service(str(self.actual_end)[:10])
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

		# Lignes pièces consommées
		for part in self.parts:
			if getattr(part, "part_type", "Consommée") == "En location":
				continue
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

		# Lignes pièces en location (tarif × durée)
		for part in self.parts:
			if getattr(part, "part_type", "Consommée") != "En location":
				continue
			duration = _rental_duration(part)
			unit_label = getattr(part, "rental_unit", None) or "Jour"
			date_fin = part.date_retour or today()
			invoice.append("items", {
				"item_code": part.item_code,
				"item_name": _("Location — {0}").format(part.item_name),
				"description": _("Location du {0} au {1} ({2} {3})").format(
					part.date_sortie or _("?"), date_fin, duration, unit_label
				),
				"qty": duration,
				"uom": unit_label,
				"rate": flt(part.rental_rate),
				"amount": flt(part.amount),
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

		# Stock Entry — consommation des pièces Consommées
		se_name = self._create_stock_entry_for_parts()
		# Stock Entry — sortie des pièces En location (si pas déjà créé au démarrage)
		rental_se = self.rental_stock_entry or self._create_rental_departure_entry()

		invoiced_stage = frappe.db.get_value(
			"FSM Stage", {"stage_name": "Facturé", "stage_type": "Ordre"}, "name"
		)
		self.db_set("invoice", invoice.name)
		if se_name:
			self.db_set("stock_entry", se_name)
		if rental_se and not self.rental_stock_entry:
			self.db_set("rental_stock_entry", rental_se)
		self.db_set("status", "Facturé")
		if invoiced_stage:
			self.db_set("fsm_stage", invoiced_stage)

		msg = _("Facture {0} créée avec succès.").format(
			frappe.utils.get_link_to_form("Sales Invoice", invoice.name)
		)
		if se_name:
			msg += "<br>" + _("Sortie stock (consommation) : {0}").format(
				frappe.utils.get_link_to_form("Stock Entry", se_name)
			)
		if rental_se:
			msg += "<br>" + _("Sortie stock (location) : {0}").format(
				frappe.utils.get_link_to_form("Stock Entry", rental_se)
			)
		frappe.msgprint(msg, title=_("Facturation terminée"))
		return invoice.name

	def _create_stock_entry_for_parts(self):
		"""Crée un Material Issue pour les pièces Consommées (pas les locations).
		Retourne le nom du Stock Entry créé, ou None si aucune pièce éligible.
		"""
		lines = [
			p for p in self.parts
			if p.item_code and p.warehouse and flt(p.qty) > 0
			and getattr(p, "part_type", "Consommée") != "En location"
		]
		if not lines:
			return None

		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Issue"
		se.company = self.company
		se.remarks = _("Consommation pièces — Intervention {0}").format(self.name)

		for part in lines:
			se.append("items", {
				"item_code": part.item_code,
				"qty": part.qty,
				"uom": part.uom,
				"s_warehouse": part.warehouse,
			})

		se.insert(ignore_permissions=True)
		se.submit()
		return se.name

	def _create_rental_departure_entry(self):
		"""Crée un Material Issue pour la sortie des pièces En location avec entrepôt défini.
		Retourne le nom du Stock Entry, ou None si aucune pièce éligible.
		"""
		lines = [
			p for p in self.parts
			if getattr(p, "part_type", "Consommée") == "En location"
			and p.item_code and p.warehouse and flt(p.qty) > 0
		]
		if not lines:
			return None

		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Issue"
		se.company = self.company
		se.remarks = _("Sortie location — Intervention {0}").format(self.name)
		for part in lines:
			se.append("items", {
				"item_code": part.item_code,
				"qty": part.qty,
				"uom": part.uom,
				"s_warehouse": part.warehouse,
			})
		se.insert(ignore_permissions=True)
		se.submit()
		return se.name

	@frappe.whitelist()
	def return_rental_parts(self):
		"""Enregistre le retour des pièces en location : Material Receipt + mise à jour lignes."""
		if self.return_stock_entry:
			frappe.throw(
				_("Les pièces ont déjà été retournées (écriture {0}).").format(self.return_stock_entry)
			)
		lines = [
			p for p in self.parts
			if getattr(p, "part_type", "Consommée") == "En location"
			and not p.is_returned
			and p.item_code and p.warehouse and flt(p.qty) > 0
		]
		if not lines:
			frappe.throw(_("Aucune pièce en location à retourner pour cette intervention."))

		today_date = today()

		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.company = self.company
		se.remarks = _("Retour location — Intervention {0}").format(self.name)
		for part in lines:
			se.append("items", {
				"item_code": part.item_code,
				"qty": part.qty,
				"uom": part.uom,
				"t_warehouse": part.warehouse,
			})
		se.insert(ignore_permissions=True)
		se.submit()

		# Mise à jour des lignes et recalcul du montant location
		for part in lines:
			part.is_returned = 1
			if not part.date_retour:
				part.date_retour = today_date

		self.db_set("return_stock_entry", se.name)
		self.save()  # déclenche calculate_parts_total → recalcul total_rental_amount avec date_retour

		frappe.msgprint(
			_("Retour enregistré. Écriture de stock {0} créée.").format(
				frappe.utils.get_link_to_form("Stock Entry", se.name)
			),
			title=_("Retour pièces en location"),
		)
		return se.name

	# ------------------------------------------------------------------ #
	#  Événements Frappe                                                   #
	# ------------------------------------------------------------------ #

	def on_submit(self):
		if self.status == "Nouveau":
			self.db_set("status", "Planifié")

	def after_submit_completed(self):
		"""Appelé manuellement après end_intervention pour mettre à jour l'équipement."""
		if self.fsm_equipment and self.status == "Terminé":
			eq = frappe.get_doc("FSM Equipment", self.fsm_equipment)
			eq.update_after_service(self.actual_end or today())

	def on_cancel(self):
		if self.invoice:
			frappe.throw(
				_("Impossible d'annuler : la facture {0} est déjà émise. Annulez d'abord la facture.").format(
					self.invoice
				)
			)
		cancelled_stage = frappe.db.get_value(
			"FSM Stage", {"stage_name": "Annulé", "stage_type": "Ordre"}, "name"
		)
		self.db_set("status", "Annulé")
		if cancelled_stage:
			self.db_set("fsm_stage", cancelled_stage)

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

	def apply_sla(self):
		"""Calcule et assigne les deadlines SLA si pas encore définis."""
		if self.sla_response_due and self.sla_resolution_due:
			return  # Déjà calculé
		from flow.field_service.doctype.fsm_sla_policy.fsm_sla_policy import get_applicable_policy
		policy = get_applicable_policy(
			fsm_team=self.fsm_team,
			activity_type=self.activity_type,
			company=self.company,
		)
		if not policy:
			return
		deadlines = policy.get_deadlines_for_priority(self.priority or "Normal")
		self.sla_policy = policy.name
		self.sla_response_due = deadlines.get("response_due")
		self.sla_resolution_due = deadlines.get("resolution_due")

	def update_sla_status(self):
		"""Met à jour les indicateurs de respect du SLA."""
		from frappe.utils import now_datetime, get_datetime
		now = now_datetime()
		closed_statuses = {"Terminé", "Facturé", "Annulé"}

		# Statut prise en charge : Respecté si l'intervention a démarré avant sla_response_due
		if self.sla_response_due:
			if self.actual_start:
				actual = get_datetime(self.actual_start)
				due = get_datetime(self.sla_response_due)
				self.sla_response_status = "Respecté" if actual <= due else "Dépassé"
			elif self.status not in closed_statuses and now > get_datetime(self.sla_response_due):
				self.sla_response_status = "Dépassé"

		# Statut résolution : Respecté si terminé avant sla_resolution_due
		if self.sla_resolution_due:
			if self.status in {"Terminé", "Facturé"}:
				end = get_datetime(self.actual_end) if self.actual_end else now
				due = get_datetime(self.sla_resolution_due)
				self.sla_resolution_status = "Respecté" if end <= due else "Dépassé"
			elif self.status not in closed_statuses and now > get_datetime(self.sla_resolution_due):
				self.sla_resolution_status = "Dépassé"

	# ------------------------------------------------------------------ #
	#  Actions métier                                                      #
	# ------------------------------------------------------------------ #

def _rental_duration(row):
	"""Calcule la durée de location en unités (jours, semaines ou mois)."""
	if not row.date_sortie:
		return 1
	end = getdate(row.date_retour) if row.date_retour else getdate(today())
	days = max(1, date_diff(end, getdate(row.date_sortie)))
	unit = getattr(row, "rental_unit", None) or "Jour"
	if unit == "Semaine":
		return max(1, -(-days // 7))   # division plafond
	if unit == "Mois":
		return max(1, -(-days // 30))
	return days  # Jour


def _rental_amount(row):
	"""Calcule le montant d'une ligne location = tarif × durée."""
	return flt(row.rental_rate) * _rental_duration(row)


def _get_closed_stage_names():
	"""Retourne les noms des stages marqués is_closed=1."""
	return frappe.db.get_all(
		"FSM Stage",
		filters={"is_closed": 1, "stage_type": "Ordre"},
		pluck="stage_name",
	)


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
