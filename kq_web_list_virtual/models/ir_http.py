from odoo import models

DEFAULT_THRESHOLD = 200


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        info = super().session_info()
        threshold = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("kq_web_list_virtual.threshold", DEFAULT_THRESHOLD)
        )
        try:
            info["kq_list_virtual_threshold"] = int(threshold)
        except (TypeError, ValueError):
            info["kq_list_virtual_threshold"] = DEFAULT_THRESHOLD
        return info
