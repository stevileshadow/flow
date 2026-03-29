# Copyright (c) 2026, stevileshadow and contributors
# License: MIT
"""
flow.field_service.automation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Moteur d'automatisation cross-modules Field Service → ERPNext.

Réduit les saisies manuelles en câblant automatiquement les transitions
FSO/Mandat vers les modules ERPNext (Timesheets, Projets, Tâches, Factures,
Stock, Comptabilité).

Points d'entrée :
  - on_fso_update(doc, method)      ← hooks.py doc_events FSO on_update
  - on_mandate_update(doc, method)  ← hooks.py doc_events FSM Mandate on_update
  - sync_pending_erp_timesheets()   ← tasks.py scheduler nuit
  - auto_close_stale_orders()       ← tasks.py scheduler nuit
  - auto_generate_mandate_invoices()← tasks.py scheduler matin

Architecture :
  Événement → garde (settings flag) → action ERPNext → log silencieux
              Toutes les actions sont idempotentes (no-op si déjà fait).
"""
import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime, today


# ────────────────────────────────────────────────────────────────────────────
#  Helper
# ────────────────────────────────────────────────────────────────────────────

def _s():
    """Retourne FSM Settings (cached)."""
    return frappe.get_cached_doc("FSM Settings")


# ════════════════════════════════════════════════════════════════════════════
#  Entrées publiques doc_events
# ════════════════════════════════════════════════════════════════════════════

def on_fso_update(doc, method=None):
    """
    Appelé par hooks.py à chaque on_update du Field Service Order.
    Utilise _pre_save_status (stocké dans validate) pour détecter
    la vraie transition avant sauvegarde DB.
    """
    old_status = getattr(doc, "_pre_save_status", None)
    if not old_status or old_status == doc.status:
        return

    # ── Toujours lors d'un changement de statut ───────────────────────────
    _auto_sync_task(doc)
    _auto_update_project_progress(doc)

    # ── À la clôture uniquement ───────────────────────────────────────────
    if doc.status == "Terminé":
        _auto_post_timesheets(doc)
        _auto_create_labor_je(doc)
        _auto_create_invoice(doc)
        _auto_update_equipment_after_close(doc)
        _auto_create_overtime_salary(doc)

    # ── À la planification : vérification stock ───────────────────────────
    if doc.status == "Planifié" and old_status in ("Nouveau", "Brouillon"):
        _check_parts_availability(doc)


def on_mandate_update(doc, method=None):
    """
    Appelé par hooks.py à chaque on_update du FSM Mandate.
    Crée automatiquement le projet ERPNext dès que le mandat devient actif.
    Idempotent : no-op si le projet existe déjà.
    """
    _auto_create_erp_project(doc)


# ════════════════════════════════════════════════════════════════════════════
#  Sync ERPNext Task
# ════════════════════════════════════════════════════════════════════════════

def _auto_sync_task(fso):
    """Synchronise la tâche ERPNext liée au FSO si le flag est actif."""
    if not getattr(_s(), "auto_sync_task_on_status", 1):
        return
    try:
        from flow.field_service.api import sync_fso_task_status
        sync_fso_task_status(fso.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Automation: sync_task — {fso.name}")


# ════════════════════════════════════════════════════════════════════════════
#  Timesheets ERPNext
# ════════════════════════════════════════════════════════════════════════════

def _auto_post_timesheets(fso):
    """
    Crée les Timesheets ERPNext depuis les lignes consolidées du FSO
    si auto_create_erp_timesheet=1 et qu'aucune n'existe déjà.
    """
    if not getattr(_s(), "auto_create_erp_timesheet", 1):
        return
    if fso.erp_timesheet:
        return
    if not fso.timesheets:
        return
    try:
        from flow.field_service.api import create_erp_timesheets
        create_erp_timesheets(fso.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Automation: timesheets — {fso.name}")


# ════════════════════════════════════════════════════════════════════════════
#  Journal Entry main-d'œuvre
# ════════════════════════════════════════════════════════════════════════════

def _auto_create_labor_je(fso):
    """
    Crée l'écriture comptable MO à la clôture si auto_create_labor_je=1.
    Dépend des comptes configurés dans FSM Settings.
    """
    if not getattr(_s(), "auto_create_labor_je", 0):
        return
    if fso.erp_journal_entry:
        return
    if not fso.total_hours and not fso.total_timesheet_amount:
        return
    try:
        from flow.field_service.api import create_labor_journal_entry
        create_labor_journal_entry(fso.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Automation: labor_je — {fso.name}")


# ════════════════════════════════════════════════════════════════════════════
#  Facture client (Sales Invoice)
# ════════════════════════════════════════════════════════════════════════════

def _auto_create_invoice(fso):
    """
    Crée une Sales Invoice ERPNext à la clôture du FSO si :
      - auto_create_invoice=1 dans FSM Settings
      - billing_type n'est pas "Pas de facturation"
      - aucune facture n'est déjà liée

    Lignes créées :
      • Main-d'œuvre  : total_timesheet_amount / total_hours heures
      • Pièces        : si billing_type inclut les pièces
    """
    s = _s()
    if not getattr(s, "auto_create_invoice", 0):
        return
    if fso.invoice:
        return
    if not fso.customer:
        return
    billing_type = fso.billing_type or ""
    if billing_type in ("", "Pas de facturation"):
        return

    item_code = getattr(s, "invoice_item_code", None)
    if not item_code:
        frappe.log_error(
            "invoice_item_code non configuré dans FSM Settings — facture non créée",
            f"Automation: invoice — {fso.name}",
        )
        return

    income_account = getattr(s, "invoice_income_account", None)
    company = fso.company or frappe.defaults.get_global_default("company")
    posting_date = getdate(fso.actual_end) if fso.actual_end else getdate(today())

    try:
        si = frappe.new_doc("Sales Invoice")
        si.customer = fso.customer
        si.company = company
        si.posting_date = posting_date
        si.set_posting_time = 1
        si.due_date = posting_date
        si.po_no = fso.name
        si.remarks = _("Intervention {0} — {1}").format(fso.name, fso.title or "")

        # ── Ligne main-d'œuvre ────────────────────────────────────────────
        labor_amount = flt(fso.total_timesheet_amount) or (
            flt(fso.total_hours) * flt(getattr(s, "default_hourly_rate", 0))
        )
        if labor_amount:
            mo_qty = flt(fso.total_hours) or 1.0
            mo_row = si.append("items", {
                "item_code": item_code,
                "qty": mo_qty,
                "rate": labor_amount / mo_qty,
                "description": _("Main-d'œuvre — {0}").format(fso.name),
            })
            if income_account:
                mo_row.income_account = income_account

        # ── Lignes pièces consommées ──────────────────────────────────────
        if billing_type in ("Forfait + Pièces", "Pièces uniquement", "Régie"):
            for part in (fso.parts or []):
                if not getattr(part, "item_code", None):
                    continue
                if getattr(part, "part_type", "Consommée") == "En location":
                    continue
                part_row = si.append("items", {
                    "item_code": part.item_code,
                    "qty": flt(part.qty) or 1.0,
                    "rate": flt(getattr(part, "rate", 0)),
                    "description": getattr(part, "item_name", None) or part.item_code,
                })
                if income_account:
                    part_row.income_account = income_account

        if not si.items:
            return  # rien à facturer

        si.insert(ignore_permissions=True)
        fso.db_set("invoice", si.name, update_modified=False)
        frappe.logger().info(
            f"[Automation] Facture {si.name} créée automatiquement pour FSO {fso.name}"
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Automation: invoice — {fso.name}")


# ════════════════════════════════════════════════════════════════════════════
#  Mise à jour équipement après clôture
# ════════════════════════════════════════════════════════════════════════════

def _auto_update_equipment_after_close(fso):
    """
    À la clôture du FSO :
      - Remet l'équipement en statut "Opérationnel"
      - Met à jour last_service_date
      - Si ordre de maintenance préventive : recalcule next_service_date
        selon maintenance_interval_days de l'équipement
    """
    if not fso.fsm_equipment:
        return
    try:
        updates = {
            "status": "Opérationnel",
            "last_service_date": getdate(fso.actual_end) if fso.actual_end else getdate(today()),
        }
        if fso.is_preventive_maintenance:
            interval = int(
                frappe.db.get_value(
                    "FSM Equipment", fso.fsm_equipment, "maintenance_interval_days"
                ) or 0
            )
            if interval > 0:
                updates["next_service_date"] = add_days(today(), interval)
        frappe.db.set_value("FSM Equipment", fso.fsm_equipment, updates)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Automation: equipment_close — {fso.name}")


# ════════════════════════════════════════════════════════════════════════════
#  Progression du projet ERPNext
# ════════════════════════════════════════════════════════════════════════════

def _auto_update_project_progress(fso):
    """
    Met à jour le % d'avancement du projet ERPNext associé au mandat
    en comptant les FSO terminés / total dans le mandat.
    """
    if not fso.fsm_mandate:
        return
    try:
        project_name = frappe.db.get_value("FSM Mandate", fso.fsm_mandate, "erp_project")
        if not project_name or not frappe.db.exists("Project", project_name):
            return

        rows = frappe.get_all(
            "FSM Mandate Order",
            filters={"parent": fso.fsm_mandate},
            fields=["field_service_order"],
        )
        total = len(rows)
        if not total:
            return

        fso_names = [r.field_service_order for r in rows if r.field_service_order]
        if not fso_names:
            return

        done = frappe.db.count(
            "Field Service Order",
            filters={
                "name": ["in", fso_names],
                "status": ["in", ["Terminé", "Facturé"]],
            },
        )
        pct = round((done / total) * 100)
        frappe.db.set_value(
            "Project", project_name, "percent_complete", pct, update_modified=False
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Automation: project_progress — {fso.name}")


# ════════════════════════════════════════════════════════════════════════════
#  Vérification disponibilité stock à la planification
# ════════════════════════════════════════════════════════════════════════════

def _check_parts_availability(fso):
    """
    Lors du passage à "Planifié" : vérifie que les pièces consommées sont
    disponibles dans l'entrepôt par défaut.
    Si auto_create_material_request=1 : crée automatiquement une Material
    Request pour les manquants.
    """
    s = _s()
    if not getattr(s, "auto_check_stock_on_schedule", 0):
        return
    if not fso.parts:
        return

    warehouse = getattr(s, "default_warehouse", None)
    if not warehouse:
        return

    shortages = []
    for part in fso.parts:
        item_code = getattr(part, "item_code", None)
        if not item_code:
            continue
        if getattr(part, "part_type", "Consommée") == "En location":
            continue
        qty_needed = flt(part.qty)
        actual_qty = flt(
            frappe.db.get_value(
                "Bin",
                {"item_code": item_code, "warehouse": warehouse},
                "actual_qty",
            ) or 0
        )
        if actual_qty < qty_needed:
            shortages.append({
                "item_code": item_code,
                "item_name": getattr(part, "item_name", None) or item_code,
                "needed": qty_needed,
                "available": actual_qty,
                "shortfall": qty_needed - actual_qty,
            })

    if not shortages:
        return

    if getattr(s, "auto_create_material_request", 0):
        _create_material_request(fso, shortages, warehouse)
    else:
        lines = "\n".join(
            f"  • {sh['item_name']} : besoin {sh['needed']}, disponible {sh['available']}"
            for sh in shortages
        )
        frappe.log_error(
            _("Stock insuffisant pour {0} :\n{1}").format(fso.name, lines),
            f"Stock insuffisant — {fso.name}",
        )


def _create_material_request(fso, shortages, warehouse):
    """Crée une Material Request pour les pièces manquantes."""
    try:
        mr = frappe.new_doc("Material Request")
        mr.material_request_type = "Purchase"
        mr.company = fso.company or frappe.defaults.get_global_default("company")
        mr.schedule_date = fso.scheduled_date or today()
        mr.title = _("Réquisition pièces — {0}").format(fso.name)
        for sh in shortages:
            mr.append("items", {
                "item_code": sh["item_code"],
                "qty": sh["shortfall"],
                "schedule_date": mr.schedule_date,
                "warehouse": warehouse,
            })
        mr.insert(ignore_permissions=True)
        fso.db_set("erp_material_request", mr.name, update_modified=False)
        frappe.logger().info(
            f"[Automation] Réquisition {mr.name} créée pour {fso.name}"
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Automation: material_request — {fso.name}")


# ════════════════════════════════════════════════════════════════════════════
#  Projet ERPNext depuis Mandat
# ════════════════════════════════════════════════════════════════════════════

def _auto_create_erp_project(mandate):
    """
    Crée silencieusement un projet ERPNext pour le mandat si :
      - auto_create_erp_project=1
      - aucun projet lié n'existe encore
    """
    s = _s()
    if not getattr(s, "auto_create_erp_project", 1):
        return
    if mandate.erp_project and frappe.db.exists("Project", mandate.erp_project):
        return
    try:
        company = mandate.company or frappe.defaults.get_global_default("company")
        project = frappe.new_doc("Project")
        project.project_name = _("FSM — {0}").format(mandate.mandate_name)
        project.customer = mandate.customer
        project.company = company
        project.expected_start_date = mandate.scheduled_date
        project.status = "Open"
        template = getattr(s, "default_project_template", None)
        if template:
            project.project_template = template
        project.insert(ignore_permissions=True)
        mandate.db_set("erp_project", project.name, update_modified=False)
        frappe.logger().info(
            f"[Automation] Projet {project.name} créé pour mandat {mandate.name}"
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Automation: erp_project — {mandate.name}"
        )


# ════════════════════════════════════════════════════════════════════════════
#  Jobs planifiés (appelés depuis tasks.py)
# ════════════════════════════════════════════════════════════════════════════

def sync_pending_erp_timesheets():
    """
    Batch nocturne — crée les Timesheets ERPNext manquantes pour tous les
    FSO clôturés sans timesheet ERPNext.
    Délai configurable : FSM Settings → timesheet_sync_days (défaut 30).
    Rattrape les échecs éventuels du déclenchement temps-réel.
    """
    if not getattr(_s(), "auto_create_erp_timesheet", 1):
        return

    sync_days = int(getattr(_s(), "timesheet_sync_days", 30) or 30)
    cutoff = add_days(today(), -sync_days)
    pending = frappe.get_all(
        "Field Service Order",
        filters={
            "status": ["in", ["Terminé", "Facturé"]],
            "erp_timesheet": ["is", "not set"],
            "actual_end": [">=", cutoff],
        },
        fields=["name"],
    )
    for row in pending:
        fso = frappe.get_doc("Field Service Order", row.name)
        if fso.timesheets:
            _auto_post_timesheets(fso)


def auto_close_stale_orders():
    """
    Ferme automatiquement les interventions bloquées en 'En cours' depuis
    trop longtemps sans activité.
    Délai configurable : FSM Settings → auto_close_stale_days (0 = désactivé).
    """
    days = int(getattr(_s(), "auto_close_stale_days", 0) or 0)
    if days <= 0:
        return

    cutoff = add_days(today(), -days)
    stale = frappe.get_all(
        "Field Service Order",
        filters={
            "status": "En cours",
            "actual_start": ["<", cutoff],
        },
        fields=["name"],
    )
    closed = 0
    for row in stale:
        try:
            frappe.db.set_value(
                "Field Service Order",
                row.name,
                {
                    "status": "Terminé",
                    "actual_end": now_datetime(),
                },
            )
            closed += 1
        except Exception:
            frappe.log_error(
                frappe.get_traceback(), f"Automation: auto_close — {row.name}"
            )
    if closed:
        frappe.db.commit()
        frappe.logger().info(f"[Automation] {closed} FSO(s) fermé(s) automatiquement (inactivité)")


# ════════════════════════════════════════════════════════════════════════════
#  Bridge FSM → Paie : Additional Salary pour heures supplémentaires
# ════════════════════════════════════════════════════════════════════════════

def _auto_create_overtime_salary(fso):
    """
    Après clôture du FSO : si les heures réelles dépassent les heures
    contractuelles de la journée (configurable dans FSM Settings →
    standard_daily_hours, défaut 8h), crée un Additional Salary ERPNext
    pour les heures supplémentaires du technicien.

    Architecture inspirée Odoo/D365 : FSM hours → payroll pipeline.
    Idempotent : vérifie l'absence d'un Additional Salary pour ce FSO.
    """
    s = _s()
    if not getattr(s, "auto_create_overtime_salary", 0):
        return
    if not fso.assigned_to or not fso.total_hours:
        return
    if flt(fso.total_hours) <= 0:
        return

    standard_daily_h = flt(getattr(s, "standard_daily_hours", 8) or 8)
    overtime_h = flt(fso.total_hours) - standard_daily_h
    if overtime_h <= 0:
        return

    # Vérifier si un Additional Salary existe déjà pour ce FSO
    existing = frappe.db.exists(
        "Additional Salary",
        {"ref_docname": fso.name, "employee": fso.assigned_to, "docstatus": ["!=", 2]},
    )
    if existing:
        return

    # Taux des heures supplémentaires (1.5× le taux horaire par défaut)
    base_rate = flt(frappe.db.get_value("Employee", fso.assigned_to, "hour_rate") or 0)
    overtime_rate = base_rate * flt(getattr(s, "overtime_multiplier", 1.5) or 1.5)
    overtime_amount = flt(overtime_h * overtime_rate, 2)

    if overtime_amount <= 0:
        return

    salary_component = getattr(s, "overtime_salary_component", None) or "Overtime"
    company = fso.company or frappe.defaults.get_global_default("company")
    payroll_date = getdate(fso.actual_end) if fso.actual_end else getdate(today())

    try:
        add_sal = frappe.new_doc("Additional Salary")
        add_sal.employee = fso.assigned_to
        add_sal.salary_component = salary_component
        add_sal.amount = overtime_amount
        add_sal.payroll_date = payroll_date
        add_sal.company = company
        add_sal.ref_doctype = "Field Service Order"
        add_sal.ref_docname = fso.name
        add_sal.notes = _(
            "Heures supplémentaires FSO {0} — {1}h au-delà de {2}h standard"
        ).format(fso.name, round(overtime_h, 2), int(standard_daily_h))
        add_sal.insert(ignore_permissions=True)
        frappe.logger().info(
            f"[Automation] Additional Salary {add_sal.name} créé pour {fso.assigned_to} "
            f"({round(overtime_h, 2)}h sup — {fso.name})"
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(), f"Automation: overtime_salary — {fso.name}"
        )


def auto_generate_mandate_invoices():
    """
    Batch matin — génère automatiquement les Sales Invoices pour les FSO
    clôturés (Terminé) appartenant à des mandats dont tous les ordres
    sont terminés, si auto_create_invoice=1.
    Évite la double facturation : ne traite que les FSO sans invoice liée.
    """
    if not getattr(_s(), "auto_create_invoice", 0):
        return

    mandates = frappe.get_all(
        "FSM Mandate",
        filters={"status": "En cours"},
        fields=["name"],
    )

    for m in mandates:
        try:
            mandate = frappe.get_doc("FSM Mandate", m.name)
            fso_names = [r.field_service_order for r in (mandate.orders or []) if r.field_service_order]
            if not fso_names:
                continue

            statuses = frappe.get_all(
                "Field Service Order",
                filters={"name": ["in", fso_names]},
                fields=["name", "status", "invoice"],
            )
            all_closed = all(o.status in ("Terminé", "Facturé") for o in statuses)
            if not all_closed:
                continue

            for fso_row in statuses:
                if fso_row.status == "Terminé" and not fso_row.invoice:
                    fso = frappe.get_doc("Field Service Order", fso_row.name)
                    _auto_create_invoice(fso)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Automation: mandate_invoices — {m.name}",
            )
