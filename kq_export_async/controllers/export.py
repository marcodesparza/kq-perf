# Part of kq_export_async. See LICENSE file for full copyright and licensing details.
"""Intercepción del export por umbral.

Si el export (no agrupado) supera ``kq_export_async.threshold`` registros,
en lugar de generarse dentro del request se encola un ``kq.export.job`` y
se informa al usuario con un UserError amigable. El job se crea en un
cursor propio y se commitea antes de lanzar la excepción, porque el
UserError revierte la transacción del request.

Nota: la intercepción vive en ``_stream_base``, así que si se desactiva
``kq_export_stream.enabled`` también se desactiva el encolado asíncrono.
"""
import json

from odoo import api
from odoo.exceptions import UserError
from odoo.http import request
from odoo.modules.registry import Registry

from odoo.addons.kq_export_stream.controllers.export import CSVExportStream, ExcelExportStream


class AsyncExportMixin:
    _kq_export_format = None

    def _async_threshold(self):
        try:
            value = int(request.env['ir.config_parameter'].sudo().get_param('kq_export_async.threshold', 0))
        except (TypeError, ValueError):
            value = 0
        return max(0, value)

    def _async_record_count(self, params):
        Model = request.env[params['model']].with_context(**(params.get('context') or {}))
        ids = params.get('ids')
        return len(ids) if ids else Model.search_count(params.get('domain') or [])

    def _stream_base(self, params):
        threshold = self._async_threshold()
        if threshold:
            count = self._async_record_count(params)
            if count >= threshold:
                self._async_enqueue(params, count, threshold)  # lanza UserError, no retorna
        return super()._stream_base(params)

    def _async_enqueue(self, params, count, threshold):
        vals = {
            'name': f"{params['model']} → {self._kq_export_format} ({count} registros)",
            'user_id': request.env.uid,
            'model_name': params['model'],
            'export_format': self._kq_export_format,
            'payload': json.dumps(params),
            'record_count': count,
        }
        registry = Registry(request.env.cr.dbname)
        with registry.cursor() as cr:
            env = api.Environment(cr, request.env.uid, dict(request.env.context))
            env['kq.export.job'].create(vals)

        raise UserError(request.env._(
            'La exportación de %(count)s registros supera el umbral de %(threshold)s y se procesará '
            'en segundo plano. Vas a recibir una notificación con el enlace de descarga cuando esté lista.',
            count=count, threshold=threshold,
        ))


class CSVExportAsync(AsyncExportMixin, CSVExportStream):
    _kq_export_format = 'csv'


class ExcelExportAsync(AsyncExportMixin, ExcelExportStream):
    _kq_export_format = 'xlsx'
