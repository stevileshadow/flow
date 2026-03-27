"""
flow.field_service.integration_check
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Valide à chaud que tous les DocTypes et champs ERPNext utilisés par
Flow existent sur l'instance en cours.

Usage (bench console) :
    from flow.field_service.integration_check import run_checks
    run_checks()

Usage (bench execute) :
    bench execute flow.field_service.integration_check.run_checks
"""

import frappe


# ------------------------------------------------------------------ #
#  Carte des dépendances : {DocType: [champs requis]}                 #
# ------------------------------------------------------------------ #
REQUIRED_DOCTYPES: dict[str, list[str]] = {
    # ERPNext Projects
    "Project": ["project_name", "status", "expected_start_date",
                "expected_end_date", "description", "customer"],
    "Task":    ["subject", "project", "status", "description",
                "exp_start_date", "exp_end_date"],
    # ERPNext HR / Payroll
    "Timesheet":          ["employee", "company", "start_date", "end_date",
                           "time_logs"],
    "Timesheet Detail":   ["activity_type", "from_time", "to_time",
                           "hours", "is_billable"],
    "Additional Salary":  ["employee", "company", "salary_component",
                           "amount", "payroll_date"],
    # ERPNext Accounting
    "Journal Entry":      ["voucher_type", "company", "posting_date",
                           "accounts"],
    "Journal Entry Account": ["account", "debit_in_account_currency",
                              "credit_in_account_currency", "party_type",
                              "party", "cost_center"],
    # ERPNext Buying
    "Purchase Order":   ["supplier", "transaction_date", "schedule_date",
                         "status", "grand_total", "currency"],
    "Purchase Invoice": ["supplier", "posting_date", "due_date",
                         "status", "grand_total", "outstanding_amount"],
    "Payment Entry":    ["party_type", "party", "posting_date",
                         "paid_amount", "currency", "mode_of_payment"],
    # ERPNext CRM
    "Contact":       ["first_name", "email_ids"],
    "Contact Email": ["email_id", "parent", "parenttype"],
    "Dynamic Link":  ["link_doctype", "link_name", "parent", "parenttype"],
    # ERPNext Selling
    "Sales Invoice": ["customer", "posting_date", "status",
                      "grand_total", "outstanding_amount"],
    # ERPNext HR
    "Employee": ["employee_name", "user_id", "company", "department"],
}


def run_checks(raise_on_error: bool = False) -> dict:
    """
    Vérifie l'existence de chaque DocType et champ requis.

    Retourne un dict :
    {
        "ok": bool,
        "missing_doctypes": [...],
        "missing_fields": {DocType: [field, ...]},
        "warnings": [...],
    }
    """
    result = {
        "ok": True,
        "missing_doctypes": [],
        "missing_fields": {},
        "warnings": [],
    }

    for doctype, fields in REQUIRED_DOCTYPES.items():
        # 1. Le DocType existe-t-il ?
        if not frappe.db.table_exists(f"tab{doctype}"):
            result["missing_doctypes"].append(doctype)
            result["ok"] = False
            continue

        # 2. Les champs existent-ils ?
        meta = frappe.get_meta(doctype)
        existing_fields = {f.fieldname for f in meta.fields}
        missing = [f for f in fields if f not in existing_fields]
        if missing:
            result["missing_fields"][doctype] = missing
            result["ok"] = False

    # Affichage console
    if result["ok"]:
        frappe.logger().info("[Flow Integration Check] ✅ Toutes les dépendances ERPNext sont présentes.")
        print("✅  Toutes les dépendances ERPNext sont présentes.")
    else:
        msg_lines = ["❌  Dépendances manquantes :"]
        if result["missing_doctypes"]:
            msg_lines.append("  DocTypes absents : " + ", ".join(result["missing_doctypes"]))
        for dt, fields in result["missing_fields"].items():
            msg_lines.append(f"  {dt} — champs manquants : {', '.join(fields)}")
        msg = "\n".join(msg_lines)
        frappe.logger().warning("[Flow Integration Check] " + msg)
        print(msg)
        if raise_on_error:
            raise RuntimeError(msg)

    return result
