# Part of kq_export_async. See LICENSE file for full copyright and licensing details.
{
    'name': 'KQ Export Async',
    'summary': 'Exportaciones masivas en segundo plano: job + cron + adjunto + notificación',
    'description': """
Fase 2 de la mejora de exportaciones (ver kq_export_stream): cuando un
export supera un umbral configurable de registros, en lugar de generarse
dentro del request HTTP (sujeto a limit_time_real y compitiendo con el
tráfico interactivo), se encola un job que un cron procesa por lotes con
memoria constante. El resultado se guarda como adjunto en el filestore
(sin cargarlo en RAM) y el usuario recibe una notificación en su inbox
con el enlace de descarga.

Parámetros de sistema (ir.config_parameter):

- ``kq_export_async.threshold`` (default 0 = deshabilitado): cantidad de
  registros a partir de la cual el export se procesa en segundo plano.
- ``kq_export_async.jobs_per_run`` (default 2): jobs procesados por
  corrida del cron.
- ``kq_export_async.retention_days`` (default 7): días tras los cuales se
  eliminan los jobs terminados y sus adjuntos (0 = no limpiar).
- ``kq_export_async.stale_running_minutes`` (default 30): minutos tras los
  cuales un job que sigue en ``running`` se considera huérfano (el worker
  murió a mitad) y se reencola a ``pending`` (0 = no reencolar). Debe ser
  mayor que ``limit_time_real`` para no pisar jobs vivos.

También reutiliza ``kq_export_stream.batch_size`` para el tamaño de lote.
""",
    'version': '19.0.1.1.0',
    'category': 'Technical',
    'author': 'Origami Soft',
    'license': 'LGPL-3',
    'depends': ['kq_export_stream', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/ir_config_parameter_data.xml',
        'data/ir_cron.xml',
        'views/export_job_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
