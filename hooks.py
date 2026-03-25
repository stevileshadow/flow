app_name = "flow"
app_title = "Flow"
app_publisher = "stevileshadow"
app_description = "ERP open source concurrent d'Odoo, bâti sur Frappe"
app_icon = "octicon octicon-zap"
app_color = "blue"
app_email = "ton-courriel@exemple.com"
app_license = "mit"

# Pages web servies depuis flow/www/
# Frappe détecte automatiquement le dossier www de chaque app installée

# ------------------------------------------------------------------ #
#  Portail client                                                      #
# ------------------------------------------------------------------ #

portal_menu_items = [
	{
		"title": "Mes interventions",
		"route": "/my/interventions",
		"reference_doctype": "Field Service Order",
		"role": "Customer",
	}
]

website_route_rules = [
	{"from_route": "/my/interventions/<name>", "to_route": "my/interventions/detail"},
]

# ------------------------------------------------------------------ #
#  Rôles                                                              #
# ------------------------------------------------------------------ #

has_permission = {
	"Field Service Order": "flow.field_service.doctype.field_service_order.field_service_order.has_permission",
}

# ------------------------------------------------------------------ #
#  Scheduler — rappels + vérification SLA                             #
# ------------------------------------------------------------------ #

scheduler_events = {
	"cron": {
		# Chaque matin à 7h : rappel des interventions planifiées du jour
		"0 7 * * *": [
			"flow.field_service.tasks.send_daily_reminders",
		],
		# Chaque heure : détection des retards + mise à jour statuts SLA
		"0 * * * *": [
			"flow.field_service.tasks.flag_overdue_orders",
			"flow.field_service.tasks.update_sla_statuses",
		],
	}
}

# ------------------------------------------------------------------ #
#  Doc Events                                                         #
# ------------------------------------------------------------------ #

doc_events = {
	"Field Service Order": {
		"on_submit": "flow.field_service.tasks.notify_technician_on_assignment",
		"on_cancel": "flow.field_service.tasks.notify_cancellation",
		"on_update": "flow.field_service.tasks.notify_customer_on_status_change",
	}
}

# ------------------------------------------------------------------ #
#  Fixtures — données de référence installées avec l'app              #
# ------------------------------------------------------------------ #

fixtures = [
	{
		"doctype": "Field Service Activity Type",
		"filters": []
	},
	{
		"doctype": "FSM Stage",
		"filters": []
	},
	{
		"doctype": "Role",
		"filters": [["role_name", "in", ["Field Service Manager", "Field Service User"]]]
	},
	{
		"doctype": "Print Format",
		"filters": [["doc_type", "=", "Field Service Order"]]
	},
]
