# Part of kq_export_stream. See LICENSE file for full copyright and licensing details.
{
    'name': 'KQ Export Stream',
    'summary': 'Exportaciones CSV/XLSX con memoria constante: spool a disco y respuesta streamed',
    'description': """
Reemplaza el pipeline de exportación estándar (que materializa todo el
dataset en RAM varias veces) por escritura incremental a un archivo
temporal, lote a lote, y una respuesta HTTP streamed desde disco.

Parámetros de sistema (ir.config_parameter):

- ``kq_export_stream.enabled`` (default True): kill-switch para volver al
  comportamiento estándar sin desinstalar.
- ``kq_export_stream.batch_size`` (default 1000): registros por lote.
- ``kq_export_stream.max_records`` (default 0 = sin límite): si es > 0,
  rechaza exports que superen esa cantidad de registros.

Los exports agrupados (XLSX con groupby) mantienen el código estándar en
esta fase.
""",
    'version': '19.0.1.1.0',
    'category': 'Technical',
    'author': 'Origami Soft',
    'license': 'LGPL-3',
    'depends': ['web'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
