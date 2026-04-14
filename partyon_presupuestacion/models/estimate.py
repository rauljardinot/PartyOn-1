from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PartyonEstimate(models.Model):
    _name = "partyon.estimate"
    _description = "Internal Estimate"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, copy=False, default="New", tracking=True)
    active = fields.Boolean(default=True)
    partner_id = fields.Many2one(
        "res.partner", required=True, tracking=True, check_company=True
    )
    opportunity_id = fields.Many2one("crm.lead", tracking=True, check_company=True)
    user_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user, tracking=True
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", store=True
    )
    date = fields.Date(default=fields.Date.context_today, tracking=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("review", "In Review"),
            ("approved", "Approved"),
            ("quoted", "Quoted"),
            ("customer_approved", "Customer Approved"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )
    version = fields.Integer(default=1, tracking=True)
    parent_estimate_id = fields.Many2one(
        "partyon.estimate", string="Previous Version", copy=False
    )
    child_estimate_ids = fields.One2many(
        "partyon.estimate", "parent_estimate_id", string="Versions"
    )
    line_ids = fields.One2many("partyon.estimate.line", "estimate_id", string="Lines")
    description = fields.Text()
    notes_internal = fields.Text()
    notes_customer = fields.Text()
    sale_order_id = fields.Many2one("sale.order", copy=False, tracking=True)
    sale_order_count = fields.Integer(compute="_compute_sale_order_count")
    total_material_cost = fields.Monetary(compute="_compute_totals", store=True)
    total_operation_cost = fields.Monetary(compute="_compute_totals", store=True)
    total_labor_cost = fields.Monetary(compute="_compute_totals", store=True)
    total_overhead_cost = fields.Monetary(compute="_compute_totals", store=True)
    total_shipping_cost = fields.Monetary(compute="_compute_totals", store=True)
    total_extra_cost = fields.Monetary(compute="_compute_totals", store=True)
    subtotal_cost = fields.Monetary(compute="_compute_totals", store=True)
    margin_type = fields.Selection(
        [
            ("percent", "Percentage"),
            ("amount", "Fixed Amount"),
            ("manual", "Manual Final Price"),
        ],
        default="percent",
        tracking=True,
    )
    margin_value = fields.Float(tracking=True)
    manual_sale_price = fields.Monetary(tracking=True)
    suggested_sale_price = fields.Monetary(compute="_compute_sale_price", store=True)
    sale_price = fields.Monetary(compute="_compute_sale_price", store=True, tracking=True)
    approved_by = fields.Many2one("res.users", copy=False, tracking=True)
    approved_date = fields.Datetime(copy=False, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("partyon.estimate") or "New"
                )
        return super().create(vals_list)

    @api.depends("sale_order_id")
    def _compute_sale_order_count(self):
        for estimate in self:
            estimate.sale_order_count = 1 if estimate.sale_order_id else 0

    @api.depends(
        "line_ids.material_cost",
        "line_ids.operation_cost",
        "line_ids.labor_cost",
        "line_ids.overhead_cost",
        "line_ids.shipping_cost",
        "line_ids.extra_cost",
        "line_ids.subtotal_cost",
    )
    def _compute_totals(self):
        for estimate in self:
            estimate.total_material_cost = sum(estimate.line_ids.mapped("material_cost"))
            estimate.total_operation_cost = sum(
                estimate.line_ids.mapped("operation_cost")
            )
            estimate.total_labor_cost = sum(estimate.line_ids.mapped("labor_cost"))
            estimate.total_overhead_cost = sum(estimate.line_ids.mapped("overhead_cost"))
            estimate.total_shipping_cost = sum(
                estimate.line_ids.mapped("shipping_cost")
            )
            estimate.total_extra_cost = sum(estimate.line_ids.mapped("extra_cost"))
            estimate.subtotal_cost = sum(estimate.line_ids.mapped("subtotal_cost"))

    @api.depends("subtotal_cost", "margin_type", "margin_value", "manual_sale_price")
    def _compute_sale_price(self):
        for estimate in self:
            if estimate.margin_type == "percent":
                margin_amount = estimate.subtotal_cost * (estimate.margin_value / 100.0)
                estimate.suggested_sale_price = estimate.subtotal_cost + margin_amount
            elif estimate.margin_type == "amount":
                estimate.suggested_sale_price = estimate.subtotal_cost + estimate.margin_value
            else:
                estimate.suggested_sale_price = estimate.subtotal_cost

            estimate.sale_price = (
                estimate.manual_sale_price
                if estimate.margin_type == "manual"
                else estimate.suggested_sale_price
            )

    def action_submit_review(self):
        self.write({"state": "review"})

    def action_approve(self):
        self.write(
            {
                "state": "approved",
                "approved_by": self.env.user.id,
                "approved_date": fields.Datetime.now(),
            }
        )

    def action_mark_customer_approved(self):
        self.write({"state": "customer_approved"})

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_reset_to_draft(self):
        self.write({"state": "draft"})

    def action_create_new_version(self):
        self.ensure_one()
        new_estimate = self.copy(
            {
                "state": "draft",
                "sale_order_id": False,
                "approved_by": False,
                "approved_date": False,
                "parent_estimate_id": self.id,
                "version": self.version + 1,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "partyon.estimate",
            "res_id": new_estimate.id,
            "view_mode": "form",
        }

    def action_create_sale_order(self):
        self.ensure_one()
        if self.sale_order_id:
            return self.action_view_sale_order()
        if self.state not in ("approved", "quoted", "customer_approved"):
            raise UserError(
                _("Only approved estimates can be converted into a quotation.")
            )
        if not self.line_ids:
            raise UserError(_("Add at least one line before creating a quotation."))

        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner_id.id,
                "opportunity_id": self.opportunity_id.id,
                "company_id": self.company_id.id,
                "partyon_estimate_id": self.id,
                "note": self.notes_customer or False,
            }
        )

        fallback_product = self.env.ref(
            "partyon_presupuestacion.product_estimate_service"
        )
        line_commands = []
        total_subtotal = self.subtotal_cost
        total_lines = len(self.line_ids)
        for line in self.line_ids:
            product = (
                line.product_id
                if line.product_id and line.product_id.sale_ok
                else fallback_product.product_variant_id
            )
            if self.margin_type == "manual":
                if total_subtotal:
                    line_total = self.sale_price * (line.subtotal_cost / total_subtotal)
                else:
                    line_total = self.sale_price / total_lines
            else:
                line_total = line.sale_price
            line_commands.append(
                (
                    0,
                    0,
                    {
                        "order_id": order.id,
                        "product_id": product.id,
                        "name": line.display_name_for_sale,
                        "product_uom_qty": line.quantity or 1.0,
                        "price_unit": line_total / line.quantity if line.quantity else line_total,
                    },
                )
            )
        order.write({"order_line": line_commands})

        self.write({"sale_order_id": order.id, "state": "quoted"})
        message = _("Sale quotation created: %s") % order.name
        self.message_post(body=message)
        order.message_post(body=_("Created from estimate %s") % self.name)
        return self.action_view_sale_order()

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("There is no quotation linked to this estimate yet."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Quotation"),
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
        }


class PartyonEstimateLine(models.Model):
    _name = "partyon.estimate.line"
    _description = "Internal Estimate Line"
    _order = "sequence, id"
    _check_company_auto = True

    sequence = fields.Integer(default=10)
    estimate_id = fields.Many2one(
        "partyon.estimate", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(
        related="estimate_id.company_id", store=True, readonly=True
    )
    currency_id = fields.Many2one(
        related="estimate_id.currency_id", store=True, readonly=True
    )
    product_id = fields.Many2one("product.product", check_company=True)
    name = fields.Char(required=True)
    quantity = fields.Float(default=1.0, digits="Product Unit of Measure")
    width_cm = fields.Float(string="Width (cm)")
    height_cm = fields.Float(string="Height (cm)")
    area_m2 = fields.Float(compute="_compute_area", store=True, string="Area (m2)")
    material_factor = fields.Float(
        default=1.0, help="Multiplier for proportional material usage."
    )
    material_unit_cost = fields.Monetary(
        compute="_compute_material_cost", store=True, string="Material Unit Cost"
    )
    material_cost = fields.Monetary(compute="_compute_material_cost", store=True)
    waste_percent = fields.Float(default=0.0)
    waste_cost = fields.Monetary(compute="_compute_costs", store=True)
    electricity_cost = fields.Monetary(default=0.0)
    machine_cost = fields.Monetary(default=0.0)
    paint_cost = fields.Monetary(default=0.0)
    operation_cost = fields.Monetary(compute="_compute_costs", store=True)
    labor_hours = fields.Float(default=0.0)
    labor_rate = fields.Monetary(default=0.0)
    design_hours = fields.Float(default=0.0)
    design_rate = fields.Monetary(default=0.0)
    handling_hours = fields.Float(default=0.0)
    handling_rate = fields.Monetary(default=0.0)
    labor_cost = fields.Monetary(compute="_compute_costs", store=True)
    shipping_cost = fields.Monetary(default=0.0)
    extra_cost = fields.Monetary(default=0.0)
    overhead_cost = fields.Monetary(compute="_compute_costs", store=True)
    subtotal_cost = fields.Monetary(compute="_compute_costs", store=True)
    use_estimate_margin = fields.Boolean(default=True)
    margin_type = fields.Selection(
        [
            ("percent", "Percentage"),
            ("amount", "Fixed Amount"),
            ("manual", "Manual Final Price"),
        ],
        default="percent",
    )
    margin_value = fields.Float(default=0.0)
    manual_sale_price = fields.Monetary(default=0.0)
    suggested_sale_price = fields.Monetary(compute="_compute_sale_price", store=True)
    sale_price = fields.Monetary(compute="_compute_sale_price", store=True)
    display_name_for_sale = fields.Char(
        compute="_compute_display_name_for_sale", store=False
    )

    @api.depends("width_cm", "height_cm", "quantity")
    def _compute_area(self):
        for line in self:
            area_unit = (line.width_cm * line.height_cm) / 10000.0
            line.area_m2 = area_unit * (line.quantity or 0.0)

    @api.depends("product_id", "area_m2", "material_factor")
    def _compute_material_cost(self):
        for line in self:
            line.material_unit_cost = line.product_id.standard_price if line.product_id else 0.0
            line.material_cost = line.area_m2 * line.material_factor * line.material_unit_cost

    @api.depends(
        "material_cost",
        "waste_percent",
        "electricity_cost",
        "machine_cost",
        "paint_cost",
        "labor_hours",
        "labor_rate",
        "design_hours",
        "design_rate",
        "handling_hours",
        "handling_rate",
        "shipping_cost",
        "extra_cost",
    )
    def _compute_costs(self):
        for line in self:
            base_labor = line.labor_hours * line.labor_rate
            design_cost = line.design_hours * line.design_rate
            handling_cost = line.handling_hours * line.handling_rate
            waste_cost = line.material_cost * (line.waste_percent / 100.0)
            operation_cost = line.electricity_cost + line.machine_cost + line.paint_cost
            labor_cost = base_labor + design_cost + handling_cost
            overhead_cost = waste_cost
            subtotal = (
                line.material_cost
                + operation_cost
                + labor_cost
                + overhead_cost
                + line.shipping_cost
                + line.extra_cost
            )
            line.waste_cost = waste_cost
            line.operation_cost = operation_cost
            line.labor_cost = labor_cost
            line.overhead_cost = overhead_cost
            line.subtotal_cost = subtotal

    @api.depends(
        "subtotal_cost",
        "margin_type",
        "margin_value",
        "manual_sale_price",
    )
    def _compute_sale_price(self):
        for line in self:
            margin_type = (
                line.estimate_id.margin_type if line.use_estimate_margin else line.margin_type
            )
            margin_value = (
                line.estimate_id.margin_value
                if line.use_estimate_margin
                else line.margin_value
            )
            if margin_type == "percent":
                line.suggested_sale_price = line.subtotal_cost * (
                    1.0 + (margin_value / 100.0)
                )
                line.sale_price = line.suggested_sale_price
            elif margin_type == "amount":
                line.suggested_sale_price = line.subtotal_cost + margin_value
                line.sale_price = line.suggested_sale_price
            else:
                line.suggested_sale_price = line.subtotal_cost
                line.sale_price = (
                    line.subtotal_cost
                    if line.use_estimate_margin
                    else (line.manual_sale_price or line.subtotal_cost)
                )

    def _compute_display_name_for_sale(self):
        for line in self:
            description = line.name
            if line.width_cm and line.height_cm:
                description = _("%s (%scm x %scm)") % (
                    description,
                    line.width_cm,
                    line.height_cm,
                )
            line.display_name_for_sale = description
