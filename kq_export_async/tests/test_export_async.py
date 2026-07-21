# Part of kq_export_async. See LICENSE file for full copyright and licensing details.
import io
import json
from unittest.mock import patch

from odoo import Command, http
from odoo.tests import common, tagged

from odoo.addons.web.controllers.export import CSVExport


class ExportJobCase(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partners = cls.env['res.partner'].create([
            {'name': 'KQ Async Uno', 'phone': '+54 11 1111-1111'},
            {'name': 'KQ Async Dos', 'child_ids': [Command.create({'name': 'KQ Async Hijo'})]},
            {'name': 'KQ Async Tres'},
        ])

    def _make_job(self, export_format='csv', fields=None, **params):
        fields = fields or [{'name': 'name', 'label': 'Name'}]
        payload = {
            'model': 'res.partner',
            'fields': fields,
            'ids': self.partners.ids,
            'domain': [],
            'groupby': [],
            'context': {},
            'import_compat': False,
        }
        payload.update(params)
        return self.env['kq.export.job'].create({
            'name': 'test job',
            'model_name': 'res.partner',
            'export_format': export_format,
            'payload': json.dumps(payload),
            'record_count': len(self.partners),
        })


@tagged('post_install', '-at_install')
class TestExportJobProcessing(ExportJobCase):

    def _expected_csv(self, fields):
        field_names = [f['name'] for f in fields]
        headers = [f['label'].strip() for f in fields]
        rows = self.partners.with_context(import_compat=False).export_data(field_names)['datas']
        return CSVExport().from_data(fields, headers, rows).encode('utf-8')

    def test_process_csv_job(self):
        fields = [
            {'name': 'name', 'label': 'Name'},
            {'name': 'phone', 'label': 'Phone'},
            {'name': 'child_ids/name', 'label': 'Contact/Name'},
        ]
        expected = self._expected_csv(fields)
        job = self._make_job(fields=fields)

        job._process_one()

        self.assertEqual(job.state, 'done')
        self.assertTrue(job.done_at)
        self.assertTrue(job.attachment_id)
        self.assertEqual(job.attachment_id.raw, expected)
        self.assertEqual(job.attachment_id.file_size, len(expected))
        self.assertIn('.csv', job.attachment_id.name)

    def test_process_xlsx_job(self):
        import openpyxl
        fields = [
            {'name': 'name', 'label': 'Name', 'type': 'char'},
            {'name': 'phone', 'label': 'Phone', 'type': 'char'},
        ]
        job = self._make_job(export_format='xlsx', fields=fields)

        job._process_one()

        self.assertEqual(job.state, 'done')
        workbook = openpyxl.load_workbook(io.BytesIO(job.attachment_id.raw), read_only=True)
        values = [list(row) for row in workbook.active.iter_rows(values_only=True)]
        self.assertEqual(values[0], ['Name', 'Phone'])
        self.assertEqual([row[0] for row in values[1:]], self.partners.mapped('name'))

    def test_process_error_marks_job(self):
        job = self._make_job(fields=[{'name': 'no_existe', 'label': 'Nope'}])

        job._process_one()

        self.assertEqual(job.state, 'error')
        self.assertTrue(job.error_message)
        self.assertFalse(job.attachment_id)

    def test_notifications(self):
        job = self._make_job()
        job._process_one()
        messages = self.env['mail.message'].search([
            ('model', '=', 'kq.export.job'),
            ('res_id', '=', job.id),
        ])
        self.assertTrue(messages)
        body = ' '.join(messages.mapped(lambda m: str(m.body)))
        self.assertIn(f'/web/content/{job.attachment_id.id}', body)

    def test_retry(self):
        job = self._make_job(fields=[{'name': 'no_existe', 'label': 'Nope'}])
        job._process_one()
        self.assertEqual(job.state, 'error')

        job.action_retry()
        self.assertEqual(job.state, 'pending')
        self.assertFalse(job.error_message)

    def test_gc_expired(self):
        job = self._make_job()
        job._process_one()
        attachment = job.attachment_id
        # flush antes del UPDATE crudo: si queda un write_date pendiente en
        # caché, el flush del search posterior pisaría el valor envejecido.
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE kq_export_job SET write_date = now() - interval '30 days' WHERE id = %s",
            [job.id],
        )
        job.invalidate_recordset()

        self.env['kq.export.job']._gc_expired()

        self.assertFalse(job.exists())
        self.assertFalse(attachment.exists())

    def test_cron_processes_pending(self):
        job = self._make_job()
        self.env['kq.export.job']._cron_process()
        self.assertEqual(job.state, 'done')


@tagged('post_install', '-at_install')
class TestExportAsyncRoute(common.HttpCase):

    def setUp(self):
        super().setUp()
        self.authenticate('admin', 'admin')
        self.partners = self.env['res.partner'].create([
            {'name': 'KQ Route Uno'},
            {'name': 'KQ Route Dos'},
            {'name': 'KQ Route Tres'},
        ])
        self.fields = [{'name': 'name', 'label': 'Name'}]
        self.payload = {
            'model': 'res.partner',
            'fields': self.fields,
            'ids': self.partners.ids,
            'domain': [],
            'groupby': [],
            'context': {},
            'import_compat': False,
        }

    def _export(self, fmt='csv'):
        return self.url_open(
            f'/web/export/{fmt}',
            data={
                'data': json.dumps(self.payload),
                'csrf_token': http.Request.csrf_token(self),
            },
        )

    def _expected_csv(self):
        rows = self.partners.with_context(import_compat=False).export_data(['name'])['datas']
        return CSVExport().from_data(self.fields, ['Name'], rows).encode('utf-8')

    def test_below_threshold_streams_normally(self):
        self.env['ir.config_parameter'].sudo().set_param('kq_export_async.threshold', '100')
        res = self._export()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, self._expected_csv())
        self.assertFalse(self.env['kq.export.job'].search([('model_name', '=', 'res.partner')]))

    def test_threshold_disabled_by_default(self):
        res = self._export()
        self.assertEqual(res.status_code, 200)
        self.assertFalse(self.env['kq.export.job'].search([('model_name', '=', 'res.partner')]))

    def test_above_threshold_enqueues_job(self):
        # Nota de modo test: el controller crea el job en un cursor propio y
        # commitea antes de lanzar el UserError. En producción el job persiste
        # (conexión independiente); en modo test ese "commit" es un release de
        # savepoint que el rollback posterior del request descarta, así que la
        # creación se verifica espiando create() y el job se re-crea con los
        # mismos vals para probar el resto del flujo.
        self.env['ir.config_parameter'].sudo().set_param('kq_export_async.threshold', '2')

        JobModel = self.registry['kq.export.job']
        original_create = JobModel.create
        captured = {}

        def spy_create(model_self, vals):
            captured['vals'] = vals
            return original_create(model_self, vals)

        with patch.object(JobModel, 'create', spy_create):
            res = self._export()

        self.assertEqual(res.status_code, 500)
        self.assertIn('segundo plano', res.text)

        vals = captured.get('vals')
        self.assertTrue(vals, 'el controller no encoló ningún job')
        self.assertEqual(vals['model_name'], 'res.partner')
        self.assertEqual(vals['export_format'], 'csv')
        self.assertEqual(vals['record_count'], 3)
        self.assertEqual(json.loads(vals['payload'])['ids'], self.partners.ids)

        # Resto del flujo: procesar el job y descargar el adjunto.
        job = self.env['kq.export.job'].create(vals)
        job._process_one()
        self.assertEqual(job.state, 'done')
        self.assertEqual(job.attachment_id.raw, self._expected_csv())

        download = self.url_open(f'/web/content/{job.attachment_id.id}?download=true')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, self._expected_csv())
