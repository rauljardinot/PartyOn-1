from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    partyon_estimate_id = fields.Many2one(
        "partyon.estimate", string="Internal Estimate", copy=False
    )

    def action_view_partyon_estimate(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Estimate",
            "res_model": "partyon.estimate",
            "res_id": self.partyon_estimate_id.id,
            "view_mode": "form",
        }
