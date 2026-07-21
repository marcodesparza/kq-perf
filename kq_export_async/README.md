# KQ Export Async

Exportaciones masivas **en segundo plano** para Odoo 19. Fase 2 del plan de
`custom-addons/reporte_exportacion_csv_memoria.md`; se apoya en
[`kq_export_stream`](../kq_export_stream/README.md) (fase 1).

## Problema que resuelve

`kq_export_stream` ya elimina el problema de memoria, pero un export enorme
sigue ejecutándose dentro del request HTTP: compite con el tráfico
interactivo, está sujeto a `limit_time_real` (acá 1200 s) y si el usuario
cierra la pestaña el trabajo se pierde.

## Qué hace

Cuando un export (no agrupado) alcanza `kq_export_async.threshold` registros:

1. El controller encola un `kq.export.job` (en cursor propio, commiteado) y
   responde al usuario con un mensaje: *"se procesará en segundo plano…"*.
2. Un cron (cada 2 minutos, con `_trigger()` inmediato si queda backlog)
   procesa los jobs pendientes **con el usuario que pidió el export** (sus
   permisos y `base.group_allow_export` aplican) usando los writers de spool
   de `kq_export_stream`: memoria ≈ un lote.
3. El archivo se adjunta **moviéndolo al filestore** (sha1 por chunks +
   `_mark_for_gc`, dedup incluido) — nunca se carga entero en RAM. Los campos
   `store_fname/checksum/file_size` se fijan por SQL porque `create()`/`write()`
   de `ir.attachment` los descartan de los vals.
4. El usuario recibe una notificación en su inbox (mail.thread
   `message_notify`) con el enlace de descarga (`/web/content/...`), servido
   streamed desde el filestore por el mecanismo estándar.

Los jobs se ven en **Ajustes → Técnico → Exports asíncronos** (cada usuario ve
los suyos; los administradores, todos), con botones *Descargar* y *Reintentar*.

## Instalación

1. Requiere `kq_export_stream` en el mismo `addons_path` (se instala solo por
   dependencia) y el módulo `mail` (estándar, presente en cualquier base
   real).
2. Instalar:
   - **UI**: modo desarrollador → Apps → *Actualizar lista de aplicaciones* →
     buscar "KQ Export Async" → Instalar.
   - **CLI** (este proyecto):
     ```bash
     docker compose restart web
     docker compose exec web odoo -c /etc/odoo/odoo.conf -d odoo_stress \
         -i kq_export_async --stop-after-init
     docker compose restart web
     ```
3. **Importante:** recién instalado, el módulo NO intercepta nada. El umbral
   `kq_export_async.threshold` vale `0` (deshabilitado) por diseño — la
   activación es una decisión operativa explícita (ver Configuración).

## Configuración

Parámetros del sistema (`ir.config_parameter`): **Ajustes → Técnico →
Parámetros → Parámetros del sistema** (modo desarrollador). Crear la clave
con *Nuevo* si no existe. Aplican al siguiente export, sin reiniciar.

| Clave | Default | Descripción |
|---|---|---|
| `kq_export_async.threshold` | `0` | **El interruptor principal.** Cantidad de registros a partir de la cual el export se encola en vez de generarse en el request. `0` = deshabilitado (el módulo instalado no hace nada). El conteo es de registros seleccionados/del dominio, no de filas del archivo (los o2m expanden filas después). |
| `kq_export_async.jobs_per_run` | `2` | Jobs procesados por corrida del cron. Si tras procesarlos queda backlog, el cron se re-dispara solo (no espera los 2 minutos). |
| `kq_export_async.retention_days` | `7` | Días tras los cuales se eliminan los jobs terminados (done/error) **y sus adjuntos**. `0` = no limpiar nunca (el filestore crece sin límite; no recomendado). |
| `kq_export_stream.batch_size` | `1000` | (heredado del módulo base) registros por lote también durante el procesamiento del job. |

### Cómo elegir el umbral

Regla práctica: el umbral debe quedar por debajo del punto donde el export
inline se vuelve incómodo para el usuario (tiempo de espera con la pestaña
bloqueada), no donde revienta la memoria — de la memoria ya se ocupa
`kq_export_stream`.

- Este despliegue (512 MB, 1 CPU, ~81k productos en `odoo_stress`): un valor
  razonable es **20000–50000**. Con `threshold = 20000`, exportar toda la
  tabla de productos se encola; exportar una página filtrada sale inline al
  instante.
- El export agrupado (XLSX con groupby) **no** pasa por el umbral: sigue el
  camino de `kq_export_stream` (que delega al core en ese caso).

### Configuración inicial recomendada (este despliegue)

```
kq_export_async.threshold      = 20000
kq_export_async.jobs_per_run   = 1      (1 CPU: procesar de a uno)
kq_export_async.retention_days = 7      (default, no hace falta crearla)
```

Por CLI:

```bash
docker compose exec web odoo shell -c /etc/odoo/odoo.conf -d odoo_stress <<'EOF'
icp = env['ir.config_parameter'].sudo()
icp.set_param('kq_export_async.threshold', '20000')
icp.set_param('kq_export_async.jobs_per_run', '1')
env.cr.commit()
EOF
```

### El cron

Se crea al instalar: **Ajustes → Técnico → Automatización → Acciones
planificadas → "KQ Export Async: procesar jobs de exportación"** (cada 2
minutos). Ahí se puede pausar (campo *Activo*), cambiar la frecuencia o
ejecutarlo a mano con *Ejecutar manualmente* para procesar la cola ya mismo.

Con `workers = 0` verificar que `max_cron_threads >= 1` en `odoo.conf` (este
proyecto ya lo tiene), si no, ningún cron corre.

## Flujo desde el punto de vista del usuario

1. Exporta normalmente (lista → ⚙ → Exportar → botón Exportar).
2. Si supera el umbral, ve un diálogo: *"La exportación de N registros supera
   el umbral… se procesará en segundo plano"*. (Se muestra con el diálogo
   estándar de error del cliente web — sin tocar JS; mejorable a futuro.)
3. Cuando el job termina le llega una notificación al inbox (campana de
   Discuss) con el enlace **Descargar**.
4. Alternativa: Ajustes → Técnico → Exports asíncronos → su job → botón
   *Descargar*. Si falló, el error completo está en el registro y el botón
   *Reintentar* lo re-encola.

## Verificar la instalación

```bash
# 1. Con threshold=2 (temporal), exportar 3 registros debe encolar:
#    el diálogo del cliente muestra "se procesará en segundo plano".
# 2. Forzar el procesamiento sin esperar al cron:
docker compose exec web odoo shell -c /etc/odoo/odoo.conf -d odoo_stress <<'EOF'
env['kq.export.job']._cron_process()
env.cr.commit()
print(env['kq.export.job'].search([]).mapped(lambda j: (j.name, j.state)))
EOF
# 3. Revisar la notificación en el inbox del usuario y descargar.
# 4. Restaurar el threshold real.
```

## Seguridad

- ACL: los usuarios internos crean/ven/editan **solo sus propios jobs**
  (regla de registro); `base.group_system` ve todo y puede borrar.
- El procesamiento corre `with_user(job.user_id)`: el archivo contiene solo
  lo que ese usuario puede leer, y si no tiene `base.group_allow_export` el
  job falla igual que el export inline.
- El adjunto queda vinculado al job (`res_model`/`res_id`), así que
  `/web/content` aplica el control de acceso estándar: solo el dueño (o un
  admin) puede descargarlo.

## Límites conocidos

- La generación de un job debe caber en una corrida de cron
  (`limit_time_real_cron`); no hay resume parcial. Si falla, queda en estado
  `error` con el traceback y se puede reintentar.
- Los exports **agrupados** (XLSX con groupby) no se encolan.
- Si `kq_export_stream.enabled = False`, el encolado asíncrono también queda
  deshabilitado (la intercepción vive en `_stream_base`).
- El conteo del umbral usa `search_count`/`len(ids)`: registros, no filas
  finales del archivo (un export con o2m puede producir bastantes más filas
  que registros).

## Tests

```
--test-tags /kq_export_async
```

Procesamiento CSV (fidelidad byte a byte contra el core) y XLSX (valores vía
openpyxl), manejo de errores + reintento, notificaciones con enlace,
retención/GC de jobs vencidos, cron, y end-to-end HTTP: umbral deshabilitado,
por debajo del umbral (stream normal), por encima (encola + procesa +
descarga vía `/web/content`).

Corrida completa con la imagen OCA CI (skill `odoo_test_kq` del proyecto):

```bash
python3 ~/.claude/skills/odoo_test_kq/scripts/odoo_ci.py custom-addons/kq_export_async \
    --include kq_export_stream,kq_export_async
```
