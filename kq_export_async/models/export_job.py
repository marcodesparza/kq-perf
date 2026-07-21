# Part of kq_export_async. See LICENSE file for full copyright and licensing details.
import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import timedelta

from markupsafe import Markup

from odoo import api, fields, models, modules
from odoo.exceptions import UserError
from odoo.tools import osutil
from odoo.tools.constants import PREFETCH_MAX
from odoo.tools.misc import split_every

from odoo.addons.kq_export_stream.controllers.export import (
    CsvSpoolWriter,
    XlsxSpoolWriter,
    _unlink_quietly,
)

_logger = logging.getLogger(__name__)

FORMAT_MIMETYPES = {
    'csv': 'text/csv;charset=utf8',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}


class KqExportJob(models.Model):
    _name = 'kq.export.job'
    _description = 'Export asíncrono'
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(required=True)
    user_id = fields.Many2one(
        'res.users', string='Usuario', required=True, index=True,
        default=lambda self: self.env.user, ondelete='cascade',
    )
    model_name = fields.Char(string='Modelo', required=True)
    export_format = fields.Selection(
        [('csv', 'CSV'), ('xlsx', 'XLSX')], string='Formato', required=True,
    )
    payload = fields.Text(
        required=True,
        help='Parámetros del export tal como los envía el cliente web '
             '(fields, ids/domain, context, import_compat), en JSON.',
    )
    state = fields.Selection(
        [
            ('pending', 'Pendiente'),
            ('running', 'En proceso'),
            ('done', 'Hecho'),
            ('error', 'Error'),
        ],
        default='pending', required=True, index=True, tracking=True,
    )
    record_count = fields.Integer(string='Registros')
    attachment_id = fields.Many2one('ir.attachment', string='Archivo', ondelete='set null')
    error_message = fields.Text()
    done_at = fields.Datetime(string='Terminado el')

    # ------------------------------------------------------------------
    # Acciones de usuario
    # ------------------------------------------------------------------

    def action_retry(self):
        for job in self:
            if job.state == 'error':
                job.write({'state': 'pending', 'error_message': False})

    def action_download(self):
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(self.env._('Este export no tiene archivo generado.'))
        return {
            'type': 'ir.actions.act_url',
            'url': f'/web/content/{self.attachment_id.id}?download=true',
            'target': 'self',
        }

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    def _param_int(self, name, default):
        try:
            return int(self.env['ir.config_parameter'].sudo().get_param(f'kq_export_async.{name}', default))
        except (TypeError, ValueError):
            return default

    @api.model
    def _cron_process(self):
        self._gc_expired()
        self._requeue_stale_running()
        limit = max(1, self._param_int('jobs_per_run', 2))
        jobs = self.search([('state', '=', 'pending')], order='id', limit=limit)
        for job in jobs:
            job._process_one()
        if len(jobs) == limit and self.search_count([('state', '=', 'pending')], limit=1):
            self.env.ref('kq_export_async.ir_cron_process_export_jobs')._trigger()

    def _requeue_stale_running(self):
        """Reencola jobs huérfanos en ``running``.

        ``_process_one`` marca ``running`` y hace commit antes de generar el
        adjunto, así que si el worker muere a mitad (reinicio por
        ``limit_memory``/``limit_time_real``, crash, deploy) el job queda en
        ``running`` para siempre: el cron solo levanta ``pending``. Pasado el
        umbral (por defecto mayor que ``limit_time_real``, así ningún job vivo
        puede seguir corriendo) lo devolvemos a ``pending`` para reintentarlo."""
        minutes = self._param_int('stale_running_minutes', 30)
        if minutes <= 0:
            return
        cutoff = fields.Datetime.now() - timedelta(minutes=minutes)
        stale = self.search([('state', '=', 'running'), ('write_date', '<', cutoff)])
        if stale:
            _logger.warning(
                "kq_export_async: reencolando %d job(s) huérfano(s) en running: %s",
                len(stale), stale.ids,
            )
            stale.write({
                'state': 'pending',
                'error_message': self.env._(
                    'El proceso se reinició mientras se generaba el export '
                    '(probablemente por límite de memoria o tiempo). Se reintenta automáticamente.'
                ),
            })

    def _gc_expired(self):
        days = self._param_int('retention_days', 7)
        if days <= 0:
            return
        cutoff = fields.Datetime.now() - timedelta(days=days)
        jobs = self.sudo().search([
            ('state', 'in', ('done', 'error')),
            ('write_date', '<', cutoff),
        ])
        if jobs:
            # BaseModel.unlink ya elimina los ir.attachment con
            # res_model/res_id apuntando a estos registros.
            _logger.info("kq_export_async: limpiando %d jobs vencidos", len(jobs))
            jobs.unlink()

    # ------------------------------------------------------------------
    # Procesamiento
    # ------------------------------------------------------------------

    def _auto_commit(self):
        # commit/rollback están prohibidos sobre el cursor de test
        # (mismo patrón que mail.mail: auto-commit salvo en tests).
        if not modules.module.current_test:
            self.env.cr.commit()

    def _auto_rollback(self):
        if not modules.module.current_test:
            self.env.cr.rollback()

    def _process_one(self):
        """Procesa el job con commits intermedios (patrón estándar de colas en
        cron: cada job termina en done o error sin afectar a los demás)."""
        self.ensure_one()
        self.write({'state': 'running'})
        self._auto_commit()
        try:
            attachment = self._generate_attachment()
        except Exception as exc:  # noqa: BLE001 - el job absorbe cualquier fallo
            self._auto_rollback()
            _logger.exception("kq.export.job %d: fallo generando el export", self.id)
            self.write({'state': 'error', 'error_message': str(exc)})
            self._notify_failed()
            self._auto_commit()
            return
        self.write({
            'state': 'done',
            'attachment_id': attachment.id,
            'done_at': fields.Datetime.now(),
            'error_message': False,
        })
        self._notify_done()
        self._auto_commit()

    def _generate_attachment(self):
        """Genera el archivo con el pipeline de spool de kq_export_stream
        (memoria ≈ un lote) y lo adjunta sin cargarlo en RAM."""
        self.ensure_one()
        params = json.loads(self.payload)
        import_compat = params.get('import_compat')
        context = params.get('context') or {}

        # El export corre con el usuario que lo pidió: respeta sus permisos
        # de acceso y el grupo base.group_allow_export (lo valida export_data).
        env = self.env(user=self.user_id.id)
        Model = env[self.model_name].with_context(import_compat=import_compat, **context)

        export_fields = params['fields']
        if not Model._is_an_ordinary_table():
            export_fields = [field for field in export_fields if field['name'] != 'id']

        field_names = [f['name'] for f in export_fields]
        if import_compat:
            columns_headers = field_names
        else:
            columns_headers = [val['label'].strip() for val in export_fields]

        ids = params.get('ids')
        domain = params.get('domain') or []
        records = Model.browse(ids) if ids else Model.search(domain)

        try:
            batch_size = int(env['ir.config_parameter'].sudo().get_param(
                'kq_export_stream.batch_size', PREFETCH_MAX))
        except (TypeError, ValueError):
            batch_size = PREFETCH_MAX
        batch_size = max(1, min(batch_size, 100000))

        fd, tmp_path = tempfile.mkstemp(prefix='kq_export_job_', suffix=f'.{self.export_format}')
        os.close(fd)
        try:
            if self.export_format == 'csv':
                writer = CsvSpoolWriter(tmp_path, columns_headers)
            else:
                writer = XlsxSpoolWriter(tmp_path, export_fields, columns_headers, len(records), env=Model.env)
            with writer:
                for batch in split_every(batch_size, records.ids, Model.browse):
                    rows = batch.export_data(field_names).get('datas', [])
                    writer.write_rows(rows)
                    batch.invalidate_recordset()

            _logger.info(
                "kq.export.job %d: user %d exported %d %r records. Fields: %s.",
                self.id, self.user_id.id, len(records.ids), self.model_name, ','.join(field_names),
            )
            return self._attachment_from_file(tmp_path)
        finally:
            # Si _attachment_from_file movió el archivo al filestore, el path
            # ya no existe y el unlink se suprime.
            _unlink_quietly(tmp_path)

    def _attachment_filename(self):
        model_record = self.env['ir.model']._get(self.model_name)
        base = f"{model_record.name} ({self.model_name})" if model_record else self.model_name
        return osutil.clean_filename(f"{base}.{self.export_format}")

    def _attachment_from_file(self, tmp_path):
        """Crea el ir.attachment moviendo el archivo al filestore, sin pasar
        el contenido por RAM.

        create()/write() de ir.attachment eliminan store_fname/checksum/
        file_size de los vals (solo se setean vía raw/datas, que requieren los
        bytes completos en memoria), así que esos campos se fijan por SQL.
        Replica la semántica de _file_write: sha1 como nombre en el filestore
        (dedup incluido) y _mark_for_gc antes de mover, para que un rollback
        no deje archivos huérfanos.
        """
        self.ensure_one()
        Attachment = self.env['ir.attachment'].sudo()
        filename = self._attachment_filename()
        mimetype = FORMAT_MIMETYPES[self.export_format]

        if Attachment._storage() != 'file':
            # Almacenamiento en DB (configuración poco común): no hay
            # filestore al que mover el archivo, único camino es raw.
            with open(tmp_path, 'rb') as handle:
                raw = handle.read()
            return Attachment.create({
                'name': filename,
                'type': 'binary',
                'raw': raw,
                'mimetype': mimetype,
                'res_model': self._name,
                'res_id': self.id,
            })

        size = os.path.getsize(tmp_path)
        sha = hashlib.sha1()
        with open(tmp_path, 'rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                sha.update(chunk)
        checksum = sha.hexdigest()

        fname = checksum[:2] + '/' + checksum
        full_path = Attachment._full_path(fname)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        Attachment._mark_for_gc(fname)
        if os.path.exists(full_path):
            # Mismo sha1 ya presente: dedup, igual que _file_write.
            os.unlink(tmp_path)
        else:
            shutil.move(tmp_path, full_path)

        attachment = Attachment.create({
            'name': filename,
            'type': 'binary',
            'mimetype': mimetype,
            'res_model': self._name,
            'res_id': self.id,
        })
        self.env.cr.execute(
            "UPDATE ir_attachment SET store_fname=%s, checksum=%s, file_size=%s, db_datas=NULL WHERE id=%s",
            [fname, checksum, size, attachment.id],
        )
        attachment.invalidate_recordset()
        return attachment

    # ------------------------------------------------------------------
    # Notificaciones
    # ------------------------------------------------------------------

    def _notify_done(self):
        self.ensure_one()
        url = f'/web/content/{self.attachment_id.id}?download=true'
        body = Markup('<p>{} <a href="{}">{}</a></p>').format(
            self.env._('Tu exportación "%(name)s" está lista.', name=self.name),
            url,
            self.env._('Descargar'),
        )
        self.message_notify(
            partner_ids=self.user_id.partner_id.ids,
            subject=self.env._('Exportación lista: %(name)s', name=self.name),
            body=body,
        )

    def _notify_failed(self):
        self.ensure_one()
        body = Markup('<p>{}</p><pre>{}</pre>').format(
            self.env._('Tu exportación "%(name)s" falló.', name=self.name),
            self.error_message or '',
        )
        self.message_notify(
            partner_ids=self.user_id.partner_id.ids,
            subject=self.env._('Exportación fallida: %(name)s', name=self.name),
            body=body,
        )
