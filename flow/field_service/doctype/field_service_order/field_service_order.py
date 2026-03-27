# Copyright (c) 2026, stevileshadow and contributors
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, flt, get_datetime, now_datetime, time_diff_in_hours, today


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
		self.validate_technicians()
		self.validate_required_fields_on_close()
		self._warn_technician_conflict()
		self._warn_low_stock()

	def apply_template_on_new(self):
		"""Applique le modèle et le mandat uniquement lors de la création (doc is new)."""
		if not self.is_new():
			return
		self._apply_mandate()
		if not self.fsm_template:
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

	def _apply_mandate(self):
		"""Pré-remplit depuis le mandat (client, lieu, date, techs, projet)."""
		if not self.fsm_mandate:
			return
		m = frappe.get_cached_doc("FSM Mandate", self.fsm_mandate)
		if m.customer and not self.customer:
			self.customer = m.customer
			self.customer_name = m.customer_name
		if m.project_name and not self.project_name:
			self.project_name = m.project_name
		if m.company and not self.company:
			self.company = m.company
		if m.fsm_location and not self.fsm_location:
			self.fsm_location = m.fsm_location
		if m.scheduled_date and not self.scheduled_date:
			self.scheduled_date = m.scheduled_date
		if m.technicians and not self.technicians:
			for t in m.technicians:
				self.append("technicians", {
					"employee": t.employee,
					"employee_name": t.employee_name,
					"is_lead": t.is_lead,
					"user_id": t.user_id,
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

	def validate_technicians(self):
		"""Vérifie les règles d'assignation multi-techniciens définies dans le gabarit."""
		if not self.technicians:
			return
		tpl = None
		if self.fsm_template:
			tpl = frappe.get_cached_doc("FSM Template", self.fsm_template)

		# Limite du nombre de techniciens
		max_tech = int(tpl.max_technicians) if tpl and tpl.max_technicians else 0
		if max_tech and len(self.technicians) > max_tech:
			frappe.throw(
				_("Ce gabarit autorise au maximum {0} technicien(s) ({1} assigné(s)).").format(
					max_tech, len(self.technicians)
				)
			)

		# Unicité du lead
		leads = [t for t in self.technicians if t.is_lead]
		require_lead = tpl.require_lead if tpl else True
		if len(leads) > 1:
			frappe.throw(_("Un seul technicien peut être défini comme responsable (lead)."))
		if require_lead and not leads:
			frappe.throw(_("Ce gabarit exige qu'un technicien responsable (lead) soit désigné."))

		# Synchronise assigned_to avec le lead
		if leads:
			self.assigned_to = leads[0].employee
			self.assigned_to_name = leads[0].employee_name

	def validate_required_fields_on_close(self):
		"""Vérifie les champs obligatoires à la clôture (statut Terminé) selon le gabarit."""
		if self.status != "Terminé":
			return
		if not self.fsm_template:
			return
		tpl = frappe.get_cached_doc("FSM Template", self.fsm_template)
		if tpl.require_signature and not self.customer_signature:
			frappe.throw(_("La signature du client est obligatoire pour clôturer cette intervention."))
		if tpl.require_description_on_close and not self.description:
			frappe.throw(_("Une description est obligatoire pour clôturer cette intervention."))
		if tpl.require_parts and not self.parts:
			frappe.throw(_("Au moins une pièce doit être renseignée pour clôturer cette intervention."))

	def _handle_status_transitions(self):
		"""Gère en un seul passage (B) la pause SLA et (C partiel) le statut équipement
		lors des changements de statut sauvegardés via validate."""
		if self.is_new():
			return
		old_status = frappe.db.get_value("Field Service Order", self.name, "status")
		# Mémorise pour on_update (qui s'exécute après la sauvegarde en DB)
		self._pre_save_status = old_status
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
		# Auto-remplir date_sortie (Datetime) pour les pièces en location sans date encore définie
		departure_dt = now_datetime()
		for part in self.parts:
			if getattr(part, "part_type", "Consommée") == "En location" and not part.date_sortie:
				part.date_sortie = departure_dt
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
		self.consolidate_timesheets()
		self.save()
		# C — Équipement → Actif à la clôture + mise à jour dates maintenance
		if self.fsm_equipment:
			eq_status = frappe.db.get_value("FSM Equipment", self.fsm_equipment, "status")
			if eq_status in ("En maintenance", "En attente de pièces"):
				frappe.db.set_value("FSM Equipment", self.fsm_equipment, "status", "Actif")
			eq = frappe.get_doc("FSM Equipment", self.fsm_equipment)
			eq.update_after_service(str(self.actual_end)[:10])
		# Intégrations ERPNext (silencieuses — erreurs loggées)
		self._run_erp_integrations()
		frappe.msgprint(
			_("Intervention terminée. Durée : {0} h").format(self.actual_duration),
			alert=True,
		)

	def _run_erp_integrations(self):
		"""Lance les intégrations ERPNext activées dans FSM Settings."""
		try:
			settings = frappe.get_single("FSM Settings")
		except Exception:
			return
		try:
			from flow.field_service.api import sync_fso_task_status
			if getattr(settings, "auto_sync_task_on_status", 1):
				sync_fso_task_status(self.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"FSM: sync task — {self.name}")
		try:
			from flow.field_service.api import create_erp_timesheets
			if getattr(settings, "auto_create_erp_timesheet", 0) and not self.erp_timesheet:
				create_erp_timesheets(self.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"FSM: create timesheets — {self.name}")

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

	@frappe.whitelist()
	def generate_customer_signature_link(self):
		"""Génère un lien de signature unique et l'envoie au client par email."""
		import uuid
		if not self.customer:
			frappe.throw(_("Aucun client associé à cet ordre."))
		token = uuid.uuid4().hex
		self.db_set("signature_token", token, update_modified=False)
		link = f"{frappe.utils.get_url()}/my/signature?token={token}"
		email = self.contact_email or None
		if not email and self.contact_person:
			email = frappe.db.get_value("Contact", self.contact_person, "email_id")
		if email:
			frappe.sendmail(
				recipients=[email],
				subject=_("Signature requise — Intervention {0}").format(self.name),
				message=_(
					"Bonjour {0},\n\n"
					"Veuillez signer le rapport d'intervention en cliquant sur le lien ci-dessous :\n\n"
					"  {1}\n\n"
					"Ce lien est valable 7 jours.\n\nMerci."
				).format(self.customer_name or _("Client"), link),
			)
		return link

	@frappe.whitelist()
	def consolidate_timesheets(self):
		"""Importe les feuilles de temps des techniciens secondaires dans l'ordre."""
		pending = frappe.get_all(
			"FSM Technician Timesheet",
			filters={"field_service_order": self.name, "is_consolidated": 0},
			fields=["name", "employee", "employee_name"],
		)
		if not pending:
			return
		consolidated_at = now_datetime()
		for ts_ref in pending:
			ts = frappe.get_doc("FSM Technician Timesheet", ts_ref.name)
			for line in ts.timesheets:
				self.append("timesheets", {
					"employee": ts.employee,
					"employee_name": ts.employee_name,
					"from_time": line.from_time,
					"to_time": line.to_time,
					"hours": line.hours,
					"activity_type": line.activity_type,
					"is_break": line.is_break,
					"is_billable": line.is_billable,
					"billing_hours": line.billing_hours,
					"hourly_rate": line.hourly_rate,
					"billing_amount": line.billing_amount,
					"description": line.description,
				})
			frappe.db.set_value(
				"FSM Technician Timesheet", ts.name,
				{"is_consolidated": 1, "consolidated_at": consolidated_at},
				update_modified=False,
			)

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

	def after_insert(self):
		"""Envoie la confirmation de création au client (silencieux)."""
		try:
			from flow.field_service.email_engine import trigger_on_new
			trigger_on_new(self.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"FSM Email: after_insert — {self.name}")

	def on_update(self):
		"""Détecte les transitions de statut et déclenche l'email approprié."""
		if self.is_new():
			return
		# _pre_save_status est défini dans _handle_status_transitions (validate), avant la sauvegarde
		old_status = getattr(self, "_pre_save_status", None)
		if old_status and old_status != self.status:
			try:
				from flow.field_service.email_engine import trigger_on_status_change
				trigger_on_status_change(self.name, old_status, self.status)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"FSM Email: on_update ({old_status}→{self.status}) — {self.name}",
				)

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
	"""Calcule la durée de location en unités (Minute/Heure/Jour/Semaine/Mois/Année)."""
	if not row.date_sortie:
		return 1
	start = get_datetime(row.date_sortie)
	end = get_datetime(row.date_retour) if row.date_retour else now_datetime()
	total_seconds = max(0.0, (end - start).total_seconds())
	unit = getattr(row, "rental_unit", None) or "Jour"
	if unit == "Minute":
		return max(1, int(total_seconds / 60))
	if unit == "Heure":
		return max(1, int(total_seconds / 3600))
	if unit == "Semaine":
		days = total_seconds / 86400
		return max(1, -(-int(days) // 7))   # division plafond
	if unit == "Mois":
		days = total_seconds / 86400
		return max(1, -(-int(days) // 30))
	if unit == "Année":
		days = total_seconds / 86400
		return max(1, -(-int(days) // 365))
	# Jour (défaut)
	return max(1, int(total_seconds / 86400))


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


def has_permission(doc, ptype, user):
	"""Contrôle d'accès FSO pour les techniciens.
	- Technicien lead (is_lead=1) : accès complet (write/read/…)
	- Technicien secondaire        : lecture seule
	- Autres utilisateurs          : None → règles normales Frappe
	"""
	if not user:
		user = frappe.session.user
	# Recherche dans la table enfant FSM Order Technician
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not employee:
		return None
	assignment = frappe.db.get_value(
		"FSM Order Technician",
		{"parent": doc.name, "employee": employee},
		["is_lead"],
		as_dict=True,
	)
	if assignment is None:
		return None
	if assignment.is_lead:
		return True
	# Technicien secondaire : lecture autorisée, écriture refusée
	return ptype == "read"


@frappe.whitelist(allow_guest=True)
def save_customer_signature(token, signature_data):
	"""Enregistre la signature client à partir d'un lien signé (token).
	Accessible sans session (allow_guest) — le token fait office d'authentification.
	"""
	if not token or not signature_data:
		frappe.throw(_("Données manquantes."))
	order_name = frappe.db.get_value(
		"Field Service Order", {"signature_token": token}, "name"
	)
	if not order_name:
		frappe.throw(_("Lien de signature invalide ou expiré."))
	frappe.db.set_value(
		"Field Service Order", order_name,
		{"customer_signature": signature_data, "signature_token": ""},
		update_modified=True,
	)
	return {"order": order_name, "success": True}


def has_permission_timesheet(doc, ptype, user):
	"""Contrôle d'accès sur FSM Technician Timesheet.
	Un technicien ne peut lire/écrire que sa propre feuille.
	"""
	if not user:
		user = frappe.session.user
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not employee:
		return None
	if doc.employee == employee:
		return True
	# Les managers FSM voient tout (None → règles normales)
	return None


@frappe.whitelist()
def submit_technician_timesheet(order_name, lines):
	"""Crée ou met à jour la feuille de temps du technicien connecté pour un FSO.
	`lines` est une liste de dicts (from_time, to_time, activity_type, …).
	"""
	import json
	if isinstance(lines, str):
		lines = json.loads(lines)

	user = frappe.session.user
	employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
	if not employee:
		frappe.throw(_("Aucun employé associé à votre compte."))

	# Vérifie que l'employé est bien secondaire sur l'ordre
	assignment = frappe.db.get_value(
		"FSM Order Technician",
		{"parent": order_name, "employee": employee},
		["is_lead"],
		as_dict=True,
	)
	if not assignment:
		frappe.throw(_("Vous n'êtes pas assigné à l'ordre {0}.").format(order_name))

	# Vérifie le droit de saisie selon le gabarit
	tpl_name = frappe.db.get_value("Field Service Order", order_name, "fsm_template")
	if tpl_name:
		secondary_can = frappe.db.get_value(
			"FSM Template", tpl_name, "secondary_can_edit_timesheets"
		)
		if not assignment.is_lead and not secondary_can:
			frappe.throw(_("La saisie d'heures n'est pas autorisée pour les techniciens secondaires sur ce gabarit."))

	# Cherche ou crée le FSM Technician Timesheet
	existing = frappe.db.get_value(
		"FSM Technician Timesheet",
		{"field_service_order": order_name, "employee": employee, "is_consolidated": 0},
		"name",
	)
	if existing:
		ts = frappe.get_doc("FSM Technician Timesheet", existing)
		ts.timesheets = []  # remplace toutes les lignes
	else:
		ts = frappe.new_doc("FSM Technician Timesheet")
		ts.field_service_order = order_name
		ts.employee = employee

	for line in lines:
		ts.append("timesheets", line)

	ts.save(ignore_permissions=True)
	return ts.name


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
