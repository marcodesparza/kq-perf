# Part of kq_export_stream. See LICENSE file for full copyright and licensing details.
"""Exportación con memoria constante.

El controlador estándar (``odoo.addons.web.controllers.export``) acumula la
matriz completa de filas en RAM (``all_rows``), la serializa entera en un
``StringIO``/``BytesIO`` y responde sin streaming: en el peor instante hay
3-4 copias del dataset en memoria a la vez.

Este módulo conserva el mismo pipeline ORM (``export_data`` por lotes con
``invalidate_recordset``) pero escribe cada lote incrementalmente a un
archivo temporal en disco y responde con ``odoo.http.Stream`` desde ese
archivo. Memoria pico ≈ 1 lote, independiente del tamaño del export.

La semántica del archivo generado es idéntica a la del core (quoting,
escape anti-fórmula, filas extra de o2m, XML-IDs, estilos xlsx); los tests
de fidelidad comparan la salida byte a byte / valor a valor contra el core.
"""
import contextlib
import csv
import json
import logging
import operator
import os
import tempfile

from odoo import http
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools import osutil
from odoo.tools.constants import PREFETCH_MAX
from odoo.tools.misc import split_every

from odoo.addons.web.controllers.export import CSVExport, ExcelExport, ExportXlsxWriter

_logger = logging.getLogger(__name__)


def _unlink_quietly(path):
    with contextlib.suppress(OSError):
        os.unlink(path)


class CsvSpoolWriter:
    """Escribe filas de export a un archivo CSV en disco, lote a lote.

    La transformación de celdas replica exactamente ``CSVExport.from_data``
    del core (None/False → '', bytes → decode, prefijo ' anti-fórmula,
    QUOTE_ALL, terminador \\r\\n, UTF-8 sin BOM).
    """

    def __init__(self, path, columns_headers):
        self._path = path
        self._columns_headers = columns_headers
        self._file = None
        self._writer = None

    def __enter__(self):
        self._file = open(self._path, 'w', encoding='utf-8', newline='')
        self._writer = csv.writer(self._file, quoting=1)
        self._writer.writerow(self._columns_headers)
        return self

    def write_rows(self, rows):
        for data in rows:
            row = []
            for d in data:
                if d is None or d is False:
                    d = ''
                elif isinstance(d, bytes):
                    d = d.decode()
                # Spreadsheet apps tend to detect formulas on leading =, + and -
                if isinstance(d, str) and d.startswith(('=', '-', '+')):
                    d = "'" + d

                row.append(d)
            self._writer.writerow(row)

    def __exit__(self, exc_type, exc_value, exc_traceback):
        if self._file is not None:
            self._file.close()


class FileExportXlsxWriter(ExportXlsxWriter):
    """``ExportXlsxWriter`` respaldado en archivo con ``constant_memory``.

    Replica el ``__init__`` del core cambiando únicamente el destino del
    workbook: en lugar de ``BytesIO`` + ``in_memory: True`` (todo el paquete
    xlsx en RAM), escribe a ``path`` con ``constant_memory: True`` (las filas
    se vuelcan a temp files de xlsxwriter a medida que se escriben). Requiere
    escribir las filas en orden ascendente, que es lo que el flujo de export
    ya hace. Mantener sincronizado con el core en upgrades.

    Acepta un ``env`` explícito para poder usarse fuera de un request HTTP
    (p. ej. desde un cron, como hace kq_export_async).
    """

    def __init__(self, fields, columns_headers, row_count, path, env=None):
        import xlsxwriter  # noqa: PLC0415
        env = env if env is not None else request.env
        self.fields = fields
        self.columns_headers = columns_headers
        self.output = None
        self.workbook = xlsxwriter.Workbook(path, {'constant_memory': True})
        self.header_style = self.workbook.add_format({'bold': True})
        self.date_style = self.workbook.add_format({'text_wrap': True, 'num_format': 'yyyy-mm-dd'})
        self.datetime_style = self.workbook.add_format({'text_wrap': True, 'num_format': 'yyyy-mm-dd hh:mm:ss'})
        self.base_style = self.workbook.add_format({'text_wrap': True})
        self.float_style = self.workbook.add_format({'text_wrap': True, 'num_format': '#,##0.00'})

        decimal_places = env['res.currency']._read_group([], aggregates=['decimal_places:max'])[0][0]
        self.monetary_style = self.workbook.add_format({'text_wrap': True, 'num_format': f'#,##0.{(decimal_places or 2) * "0"}'})

        header_bold_props = {'text_wrap': True, 'bold': True, 'bg_color': '#e9ecef'}
        self.header_bold_style = self.workbook.add_format(header_bold_props)
        self.header_bold_style_float = self.workbook.add_format(dict(**header_bold_props, num_format='#,##0.00'))
        self.header_bold_style_monetary = self.workbook.add_format(dict(**header_bold_props, num_format=f'#,##0.{(decimal_places or 2) * "0"}'))

        self.worksheet = self.workbook.add_worksheet()
        self.value = False

        if row_count > self.worksheet.xls_rowmax:
            raise UserError(env._('There are too many rows (%(count)s rows, limit: %(limit)s) to export as Excel 2007-2013 (.xlsx) format. Consider splitting the export.', count=row_count, limit=self.worksheet.xls_rowmax))

    def close(self):
        self.workbook.close()


class XlsxSpoolWriter:
    """Adaptador con la interfaz de spool (``write_rows``) sobre
    ``FileExportXlsxWriter``, con chequeo incremental del límite de filas
    (el total real no se conoce por adelantado: los o2m expanden filas)."""

    def __init__(self, path, fields, columns_headers, row_count_hint, env=None):
        self._env = env if env is not None else request.env
        self._writer = FileExportXlsxWriter(fields, columns_headers, row_count_hint, path, env=self._env)
        self._row = 0

    def __enter__(self):
        self._writer.__enter__()
        return self

    def write_rows(self, rows):
        xls_rowmax = self._writer.worksheet.xls_rowmax
        for row_data in rows:
            self._row += 1
            if self._row > xls_rowmax:
                raise UserError(self._env._('There are too many rows (%(count)s rows, limit: %(limit)s) to export as Excel 2007-2013 (.xlsx) format. Consider splitting the export.', count=self._row, limit=xls_rowmax))
            for cell_index, cell_value in enumerate(row_data):
                self._writer.write_cell(self._row, cell_index, cell_value)

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self._writer.__exit__(exc_type, exc_value, exc_traceback)


class StreamedExportMixin:
    """Reemplaza ``ExportFormat.base()`` por la variante con spool a disco.

    Se aplica heredando los controllers del core; los métodos de ruta
    (``web_export_csv``/``web_export_xlsx``) no se tocan, siguen llamando a
    ``self.base(data)`` y el MRO resuelve esta implementación.
    """

    def _spool_writer(self, path, fields, columns_headers, row_count_hint):
        raise NotImplementedError()

    def _export_stream_param(self, name, default):
        return request.env['ir.config_parameter'].sudo().get_param(f'kq_export_stream.{name}', default)

    def _export_stream_enabled(self):
        return str(self._export_stream_param('enabled', 'True')).strip().lower() not in ('0', 'false', 'no')

    def _export_stream_batch_size(self):
        try:
            size = int(self._export_stream_param('batch_size', PREFETCH_MAX))
        except (TypeError, ValueError):
            size = PREFETCH_MAX
        return max(1, min(size, 100000))

    def _export_stream_max_records(self):
        try:
            return max(0, int(self._export_stream_param('max_records', 0)))
        except (TypeError, ValueError):
            return 0

    def base(self, data):
        params = json.loads(data)
        # Los exports agrupados (solo xlsx: import_compat ignora groupby, y
        # CSV agrupado es UserError en el core) mantienen el camino estándar
        # en esta fase.
        if not self._export_stream_enabled() or (params.get('groupby') and not params.get('import_compat')):
            return super().base(data)
        return self._stream_base(params)

    def _stream_base(self, params):
        model, fields, ids, domain, import_compat = \
            operator.itemgetter('model', 'fields', 'ids', 'domain', 'import_compat')(params)

        Model = request.env[model].with_context(import_compat=import_compat, **params.get('context', {}))
        if not Model._is_an_ordinary_table():
            fields = [field for field in fields if field['name'] != 'id']

        field_names = [f['name'] for f in fields]
        if import_compat:
            columns_headers = field_names
        else:
            columns_headers = [val['label'].strip() for val in fields]

        records = Model.browse(ids) if ids else Model.search(domain)

        max_records = self._export_stream_max_records()
        if max_records and len(records) > max_records:
            raise UserError(request.env._(
                'This export exceeds the limit of %(limit)s records set by kq_export_stream.max_records (%(count)s records requested). Narrow the selection or raise the limit.',
                limit=max_records, count=len(records),
            ))

        fd, tmp_path = tempfile.mkstemp(prefix='kq_export_', suffix=self.extension)
        os.close(fd)
        try:
            batch_size = self._export_stream_batch_size()
            with self._spool_writer(tmp_path, fields, columns_headers, len(records)) as writer:
                for batch in split_every(batch_size, records.ids, Model.browse):
                    rows = batch.export_data(field_names).get('datas', [])
                    writer.write_rows(rows)
                    batch.invalidate_recordset()
        except Exception:
            _unlink_quietly(tmp_path)
            raise

        _logger.info(
            "User %d exported %d %r records from %s. Fields: %s. %s: %s",
            request.env.user.id, len(records.ids), records._name, request.httprequest.environ['REMOTE_ADDR'],
            ','.join(field_names),
            'IDs sample' if ids else 'Domain',
            records.ids[:10] if ids else domain,
        )

        return self._stream_response(tmp_path, model)

    def _stream_response(self, tmp_path, model):
        # Stream.from_path() valida contra los addons paths, no sirve para un
        # temp file: se instancia Stream directamente con los mismos campos.
        filename = osutil.clean_filename(self.filename(model) + self.extension)
        stream = http.Stream(
            type='path',
            path=tmp_path,
            mimetype=self.content_type,
            download_name=filename,
            as_attachment=True,
            conditional=False,
            etag=False,
            last_modified=None,
            size=os.path.getsize(tmp_path),
            public=False,
        )
        response = stream.get_response()
        # send_file ya abrió el archivo: con el fd abierto se puede desvincular
        # el path de inmediato (POSIX) y el contenido sigue disponible hasta
        # que la response se cierre. Con direct_passthrough los callbacks de
        # call_on_close no se ejecutan, así que este es el único mecanismo de
        # limpieza que no deja huérfanos (incluso si el proceso muere a mitad
        # de descarga).
        _unlink_quietly(tmp_path)
        return response


class CSVExportStream(StreamedExportMixin, CSVExport):

    def _spool_writer(self, path, fields, columns_headers, row_count_hint):
        return CsvSpoolWriter(path, columns_headers)


class ExcelExportStream(StreamedExportMixin, ExcelExport):

    def _spool_writer(self, path, fields, columns_headers, row_count_hint):
        return XlsxSpoolWriter(path, fields, columns_headers, row_count_hint)
