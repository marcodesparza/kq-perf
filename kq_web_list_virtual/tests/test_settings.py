from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestListVirtualThreshold(TransactionCase):
    def test_threshold_is_stored_as_config_parameter(self):
        settings = self.env["res.config.settings"].create(
            {"list_virtual_threshold": 500}
        )
        settings.set_values()
        self.assertEqual(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("kq_web_list_virtual.threshold"),
            "500",
        )

    def test_threshold_zero_disables_virtualization(self):
        settings = self.env["res.config.settings"].create(
            {"list_virtual_threshold": 0}
        )
        settings.set_values()
        self.assertEqual(
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("kq_web_list_virtual.threshold"),
            "0",
        )

    def test_threshold_cannot_be_negative(self):
        with self.assertRaises(ValidationError):
            self.env["res.config.settings"].create(
                {"list_virtual_threshold": -1}
            )

    def test_threshold_default_value(self):
        settings = self.env["res.config.settings"].create({})
        self.assertEqual(settings.list_virtual_threshold, 200)

    def test_threshold_write_negative_is_rejected(self):
        settings = self.env["res.config.settings"].create(
            {"list_virtual_threshold": 100}
        )
        with self.assertRaises(ValidationError):
            settings.write({"list_virtual_threshold": -5})
