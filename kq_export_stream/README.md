# KQ Export Stream

Exportaciones CSV/XLSX con **memoria constante** para Odoo 19.

## Problema

El controlador estándar (`/web/export/csv`, `/web/export/xlsx`) materializa
todo el export en RAM varias veces a la vez: la matriz completa de filas
(`all_rows`), el archivo serializado entero (`StringIO` / workbook
`xlsxwriter` con `in_memory: True`) y la copia final en la respuesta HTTP
(sin streaming). En despliegues con poca memoria (este proyecto: contenedor
de 512 MB, `limit_memory_hard` de 512 MB y `workers = 0`), un export grande
puede tirar `MemoryError` o reiniciar el servidor para todos los usuarios.

Análisis completo: `custom-addons/reporte_exportacion_csv_memoria.md`.

## Qué hace

Hereda los controllers de export del core y reemplaza `ExportFormat.base()`:

- Mantiene el mismo pipeline ORM del core: `export_data()` por lotes
  (`split_every`) + `invalidate_recordset()` por lote.
- Cada lote se escribe **incrementalmente a un archivo temporal** en disco:
  - CSV: `csv.writer` directo al archivo (misma transformación de celdas que
    el core: `None`/`False` → `''`, `bytes` → `decode()`, prefijo `'`
    anti-fórmula, `QUOTE_ALL`).
  - XLSX: `xlsxwriter` en modo `constant_memory` (vuelca filas a disco a
    medida que se escriben, mismos estilos que el core).
- La respuesta se sirve con `odoo.http.Stream` desde el archivo (streamed,
  con `Content-Length`), y el temporal se elimina de forma segura (unlink
  inmediato con el descriptor abierto: no puede quedar huérfano ni aunque el
  proceso muera a mitad de descarga).

Memoria pico ≈ un lote (1000 registros por defecto), independiente del
tamaño del export.

## Qué NO cambia

- La salida es idéntica a la del core (los tests comparan byte a byte el CSV
  y valor a valor el XLSX; el XLSX no es byte-idéntico porque
  `constant_memory` usa inline strings en lugar de shared strings — Excel y
  cualquier lector lo abren igual).
- Los exports agrupados (XLSX con groupby) usan el código estándar del core
  en esta fase (era el camino P6 del reporte; solo afecta a XLSX agrupado).
- El frontend no se toca: el usuario exporta exactamente igual que siempre.
- La creación de XML-IDs faltantes al exportar la columna `id`
  (import-compatible) se mantiene: es parte del contrato de `export_data`.

## Instalación

1. Dejar el módulo en un directorio del `addons_path` (en este proyecto ya
   está: `custom-addons` se monta como `/mnt/custom-addons` en el contenedor).
2. Reiniciar Odoo y actualizar la lista de aplicaciones:
   - **UI**: activar modo desarrollador (Ajustes → General → Herramientas de
     desarrollador → *Activar el modo desarrollador*), luego Apps →
     *Actualizar lista de aplicaciones* → buscar "KQ Export Stream" →
     Instalar.
   - **CLI** (este proyecto):
     ```bash
     docker compose restart web
     docker compose exec web odoo -c /etc/odoo/odoo.conf -d odoo_stress \
         -i kq_export_stream --stop-after-init
     docker compose restart web
     ```
3. No requiere configuración adicional: **funciona desde la instalación**
   con valores por defecto seguros. Los parámetros de abajo son opcionales.

## Configuración

Todos los parámetros son *Parámetros del sistema* (`ir.config_parameter`).
Se crean/editan en **Ajustes → Técnico → Parámetros → Parámetros del
sistema** (requiere modo desarrollador). Si la clave no existe, crearla con
el botón *Nuevo*. Los cambios aplican al siguiente export, sin reiniciar.

| Clave | Default | Descripción |
|---|---|---|
| `kq_export_stream.enabled` | `True` | Kill-switch. Con `False` (también `0` o `no`) todos los exports vuelven al comportamiento estándar del core sin desinstalar el módulo. Útil para descartar al módulo ante cualquier sospecha de regresión. |
| `kq_export_stream.batch_size` | `1000` | Registros leídos del ORM por lote (clamp interno 1–100000). Subirlo reduce round-trips a costa de más RAM por lote; bajarlo al revés. Con 512 MB de RAM no conviene pasar de 2000–5000. |
| `kq_export_stream.max_records` | `0` | Si es > 0, los exports de más de N registros se rechazan con un error claro **antes** de empezar. `0` = sin límite. Útil como fusible si no se instala `kq_export_async`. |

### Valores recomendados para este despliegue (512 MB, workers=0)

```
kq_export_stream.enabled     = True    (default, no hace falta crearla)
kq_export_stream.batch_size  = 1000    (default, no hace falta crearla)
kq_export_stream.max_records = 200000  (fusible; con kq_export_async instalado
                                        puede quedar en 0 y que el umbral
                                        async haga de límite blando)
```

### Crear un parámetro por CLI (alternativa a la UI)

```bash
docker compose exec web odoo shell -c /etc/odoo/odoo.conf -d odoo_stress <<'EOF'
env['ir.config_parameter'].sudo().set_param('kq_export_stream.max_records', '200000')
env.cr.commit()
EOF
```

## Verificar que está activo

- Exportar cualquier lista a CSV: en el log del servidor la línea
  `User N exported ...` ahora la emite el logger
  `odoo.addons.kq_export_stream.controllers.export` (el core la emite como
  `odoo.addons.web.controllers.export`).
- La respuesta del export llega con `Content-Length` y sin que la RSS del
  proceso crezca con el tamaño del export (`docker stats odoo19-web`).

## Requisitos y supuestos

- Odoo 19.0 (los writers replican código del core 19; revisar en upgrades —
  los puntos copiados están marcados con "Mantener sincronizado" en el
  código).
- Espacio en disco temporal (`/tmp` del contenedor) del tamaño del export.
- Linux/POSIX (la limpieza del temporal usa unlink con fd abierto).

## Tests

```
--test-tags /kq_export_stream
```

- `TestCsvSpoolWriter`: fidelidad byte a byte del writer CSV contra
  `CSVExport.from_data` del core sobre valores borde (fórmulas, saltos de
  línea, comillas, UTF-8, bytes, None/False/0), export vacío, y test de
  memoria constante con `tracemalloc` (50k filas en lotes, pico < 20 MB).
- `TestExportStreamRoutes` (HttpCase, post_install): fidelidad end-to-end de
  la ruta CSV (incluye o2m multilínea, m2m, import-compatible con creación
  de XML-IDs, batch_size=1, dominio vacío), equivalencia de valores XLSX
  contra el core, delegación del agrupado, kill-switch, límite
  `max_records`, y ausencia de temp files huérfanos (éxito y error).


## Módulos relacionados

- [`kq_export_async`](../kq_export_async/README.md) (fase 2): procesa en
  segundo plano los exports que superan un umbral de registros. Depende de
  este módulo.

## Fases siguientes (ver reporte)

- Camino agrupado XLSX (groupby) con batching propio.
- Endurecimiento operativo (multi-worker si se amplía la RAM).
