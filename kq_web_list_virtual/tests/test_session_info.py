from unittest.mock import MagicMock

from odoo.addons.web.models import ir_http as web_ir_http
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSessionInfo(TransactionCase):
    """Pruebas para la inyección del umbral en session_info."""

    def _session_info(self):
        """Ejecuta session_info con un request mínimo mockeado.

        El método base de ``web.models.ir_http`` usa el ``request`` importado
        en ese módulo. En un ``TransactionCase`` no hay request activo, así que
        reemplazamos directamente ``web_ir_http.request`` por un mock que
        exponga la sesión mínima que necesita.
        """
        mock_session = MagicMock()
        mock_session.uid = self.env.user.id
        mock_session.context = {}
        mock_request = type("Request", (), {"session": mock_session})()
        original = web_ir_http.request
        web_ir_http.request = mock_request
        try:
            return self.env["ir.http"].session_info()
        finally:
            web_ir_http.request = original

    def test_default_threshold_in_session_info(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "kq_web_list_virtual.threshold", ""
        )
        info = self._session_info()
        self.assertEqual(info.get("kq_list_virtual_threshold"), 200)

    def test_custom_threshold_in_session_info(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "kq_web_list_virtual.threshold", "500"
        )
        info = self._session_info()
        self.assertEqual(info.get("kq_list_virtual_threshold"), 500)

    def test_invalid_threshold_falls_back_to_default(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "kq_web_list_virtual.threshold", "not-a-number"
        )
        info = self._session_info()
        self.assertEqual(info.get("kq_list_virtual_threshold"), 200)

    def test_zero_threshold_is_exposed(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "kq_web_list_virtual.threshold", "0"
        )
        info = self._session_info()
        self.assertEqual(info.get("kq_list_virtual_threshold"), 0)
