// Copyright (c) 2026, stevileshadow and contributors
// License: MIT

frappe.ui.form.on("Field Service Order", {
	refresh(frm) {
		frm.set_query("project", function () {
			return {
				filters: frm.doc.customer
					? { customer: frm.doc.customer, status: ["!=", "Cancelled"] }
					: { status: ["!=", "Cancelled"] },
			};
		});
	},

	customer(frm) {
		// Réinitialise le projet si le client change
		if (frm.doc.project) {
			frappe.db
				.get_value("Project", frm.doc.project, "customer")
				.then(({ message }) => {
					if (message && message.customer !== frm.doc.customer) {
						frm.set_value("project", null);
					}
				});
		}
	},
});
