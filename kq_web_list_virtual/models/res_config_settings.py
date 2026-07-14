from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    list_virtual_threshold = fields.Integer(
        string="Umbral de virtualización de listas",
        help=(
            "Cantidad de registros cargados a partir de la cual las vistas de "
            "lista solo renderizan las filas visibles en pantalla (scroll "
            "virtual). Usa 0 para desactivar la virtualización."
        ),
        config_parameter="kq_web_list_virtual.threshold",
        default=200,
    )

    def set_values(self):
        super().set_values()
        self.env["ir.config_parameter"].sudo().set_param(
            "kq_web_list_virtual.threshold", str(self.list_virtual_threshold)
        )

    @api.constrains("list_virtual_threshold")
    def _check_list_virtual_threshold(self):
        for setting in self:
            if setting.list_virtual_threshold < 0:
                raise ValidationError(
                    _("El umbral de virtualización no puede ser negativo.")
                )
