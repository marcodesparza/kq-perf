# Part of kq_export_stream. See LICENSE file for full copyright and licensing details.
import io
import json
import os
import tempfile
import tracemalloc
from unittest.mock import patch

from odoo import Command, http
from odoo.tests import common, tagged

from odoo.addons.web.controllers.export import CSVExport
from odoo.addons.kq_export_stream.controllers.export import CsvSpoolWriter, StreamedExportMixin


class TestCsvSpoolWriter(common.TransactionCase):
    """Fidelidad del writer CSV contra ``CSVExport.from_data`` del core,
    byte a byte, sobre una matriz de valores borde."""

    HEADERS = ['Name', 'Notes', 'Amount', 'Flag']
    EDGE_ROWS = [
        ['=SUM(A1:A3)', '-negativo', '+positivo', "'ya con comilla"],
        [None, False, '', 0],
        [0.0, 123.45, -7, True],
        ['comilla "adentro"', 'multi\nlínea', 'salto\r\nrn', 'áé 漢字'],
        [b'Ynl0ZXM=', 'espacio final ', ' espacio inicial', 'a,b;c'],
    ]

    def _spool(self, headers, rows):
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        try:
            with CsvSpoolWriter(path, headers) as writer:
                writer.write_rows(rows)
            with open(path, 'rb') as f:
                return f.read()
        finally:
            os.unlink(path)

    def test_byte_identical_to_core(self):
        expected = CSVExport().from_data([], self.HEADERS, self.EDGE_ROWS).encode('utf-8')
        actual = self._spool(self.HEADERS, self.EDGE_ROWS)
        self.assertEqual(actual, expected)

    def test_empty_export_matches_core(self):
        expected = CSVExport().from_data([], self.HEADERS, []).encode('utf-8')
        actual = self._spool(self.HEADERS, [])
        self.assertEqual(actual, expected)

    def test_constant_memory(self):
        """Escribir 50k filas en lotes no debe acumular memoria: el pico debe
        quedar en el orden de un solo lote."""
        headers = [f'col{i}' for i in range(10)]
        fd, path = tempfile.mkstemp(suffix='.csv')
        os.close(fd)
        try:
            tracemalloc.start()
            with CsvSpoolWriter(path, headers) as writer:
                for batch_index in range(50):
                    batch = [
                        [f'value {batch_index}-{row}-{col}' for col in range(10)]
                        for row in range(1000)
                    ]
                    writer.write_rows(batch)
                    del batch
            _current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            self.assertGreater(os.path.getsize(path), 1024 * 1024)
            self.assertLess(peak, 20 * 1024 * 1024)
        finally:
            os.unlink(path)


@tagged('post_install', '-at_install')
class TestExportStreamRoutes(common.HttpCase):

    def setUp(self):
        super().setUp()
        self.authenticate('admin', 'admin')
        categories = self.env['res.partner.category'].create([
            {'name': 'KQ Cat =A'},
            {'name': 'KQ Cat B'},
        ])
        self.partners = self.env['res.partner'].create([
            {
                'name': '=SUM(A1:A3)',
                'phone': '+54 11 4444-5555',
                'category_id': [Command.set(categories.ids)],
                'child_ids': [
                    Command.create({'name': 'KQ Child -1'}),
                    Command.create({'name': 'KQ Child "two"'}),
                ],
            },
            {'name': '-KQ Ácentos 漢字', 'phone': False},
            {'name': 'KQ Plain'},
        ])

    def _payload(self, **kwargs):
        payload = {
            'model': 'res.partner',
            'fields': [{'name': 'name', 'label': 'Name'}],
            'ids': self.partners.ids,
            'domain': [],
            'groupby': [],
            'context': {},
            'import_compat': False,
        }
        payload.update(kwargs)
        return payload

    def _export(self, fmt, payload):
        return self.url_open(
            f'/web/export/{fmt}',
            data={
                'data': json.dumps(payload),
                'csrf_token': http.Request.csrf_token(self),
            },
        )

    def _expected_csv(self, fields, import_compat):
        field_names = [f['name'] for f in fields]
        headers = field_names if import_compat else [f['label'].strip() for f in fields]
        rows = self.partners.with_context(import_compat=import_compat).export_data(field_names)['datas']
        return CSVExport().from_data(fields, headers, rows).encode('utf-8')

    def test_csv_matches_core(self):
        fields = [
            {'name': 'name', 'label': 'Name'},
            {'name': 'phone', 'label': 'Phone'},
            {'name': 'category_id', 'label': 'Tags'},
            {'name': 'child_ids/name', 'label': 'Contact/Name'},
        ]
        expected = self._expected_csv(fields, import_compat=False)

        res = self._export('csv', self._payload(fields=fields))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, expected)
        self.assertIn('text/csv', res.headers.get('Content-Type', ''))
        self.assertIn('.csv', res.headers.get('Content-Disposition', ''))

    def test_csv_import_compat_creates_xids(self):
        fields = [
            {'name': 'id', 'label': 'External ID'},
            {'name': 'name', 'label': 'Name'},
        ]
        # Calculado primero: crea los xids faltantes, la ruta los reutiliza y
        # la salida debe ser idéntica.
        expected = self._expected_csv(fields, import_compat=True)

        res = self._export('csv', self._payload(fields=fields, import_compat=True))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, expected)
        xids = self.env['ir.model.data'].search([
            ('model', '=', 'res.partner'),
            ('res_id', 'in', self.partners.ids),
        ])
        self.assertEqual(len(xids), len(self.partners))

    def test_csv_small_batches(self):
        """Con batch_size=1 (peor caso de lotes) la salida no cambia."""
        self.env['ir.config_parameter'].sudo().set_param('kq_export_stream.batch_size', '1')
        fields = [
            {'name': 'name', 'label': 'Name'},
            {'name': 'child_ids/name', 'label': 'Contact/Name'},
        ]
        expected = self._expected_csv(fields, import_compat=False)

        res = self._export('csv', self._payload(fields=fields))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, expected)

    def test_csv_empty_domain(self):
        fields = [{'name': 'name', 'label': 'Name'}]
        res = self._export('csv', self._payload(
            fields=fields, ids=False, domain=[('id', '=', 0)],
        ))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, CSVExport().from_data(fields, ['Name'], []).encode('utf-8'))

    def test_kill_switch_falls_back_to_core(self):
        self.env['ir.config_parameter'].sudo().set_param('kq_export_stream.enabled', 'False')
        fields = [{'name': 'name', 'label': 'Name'}]
        expected = self._expected_csv(fields, import_compat=False)

        with patch.object(StreamedExportMixin, '_stream_base', side_effect=AssertionError('should not stream')):
            res = self._export('csv', self._payload(fields=fields))

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, expected)

    def test_max_records_limit(self):
        self.env['ir.config_parameter'].sudo().set_param('kq_export_stream.max_records', '2')
        res = self._export('csv', self._payload())
        self.assertEqual(res.status_code, 500)
        self.assertIn('kq_export_stream.max_records', res.text)

    def _xlsx_values(self, content):
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        worksheet = workbook.active
        return [list(row) for row in worksheet.iter_rows(values_only=True)]

    def test_xlsx_matches_core(self):
        """Misma grilla de valores que el core (los bytes difieren porque
        constant_memory usa inline strings en lugar de shared strings)."""
        fields = [
            {'name': 'name', 'label': 'Name', 'type': 'char'},
            {'name': 'phone', 'label': 'Phone', 'type': 'char'},
            {'name': 'category_id', 'label': 'Tags', 'type': 'many2many'},
            {'name': 'child_ids/name', 'label': 'Contact/Name', 'type': 'char'},
        ]
        payload = self._payload(fields=fields)
        icp = self.env['ir.config_parameter'].sudo()

        icp.set_param('kq_export_stream.enabled', 'False')
        core_res = self._export('xlsx', payload)
        self.assertEqual(core_res.status_code, 200)

        icp.set_param('kq_export_stream.enabled', 'True')
        stream_res = self._export('xlsx', payload)
        self.assertEqual(stream_res.status_code, 200)

        self.assertEqual(self._xlsx_values(stream_res.content), self._xlsx_values(core_res.content))

    def test_xlsx_grouped_delegates_to_core(self):
        fields = [
            {'name': 'name', 'label': 'Name', 'type': 'char'},
            {'name': 'active', 'label': 'Active', 'type': 'boolean'},
        ]
        res = self._export('xlsx', self._payload(fields=fields, groupby=['active']))
        self.assertEqual(res.status_code, 200)
        values = self._xlsx_values(res.content)
        self.assertTrue(values)

    def test_no_orphan_temp_files(self):
        tmpdir = tempfile.gettempdir()

        def kq_temp_files():
            return {f for f in os.listdir(tmpdir) if f.startswith('kq_export_')}

        before = kq_temp_files()

        res = self._export('csv', self._payload())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.content)

        # Error a mitad de spool: el temp file también debe limpiarse.
        self.env['ir.config_parameter'].sudo().set_param('kq_export_stream.max_records', '1')
        res = self._export('csv', self._payload())
        self.assertEqual(res.status_code, 500)

        self.assertEqual(kq_temp_files(), before)
