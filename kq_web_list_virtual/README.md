# Web List Virtual Rows

[![Odoo](https://img.shields.io/badge/Odoo-19.0-875A7B?logo=odoo)](https://www.odoo.com)
[![License](https://img.shields.io/badge/License-LGPL--3-00A4DE?logo=gnu)](LICENSE)
[![Version](https://img.shields.io/badge/version-19.0.1.0.0-blue)](#)

Parte de [kq-perf](../README.md), un proyecto de **marcodesparza** para mejorar el rendimiento general de Odoo.

Virtualiza las filas de las vistas de lista del backend de Odoo 19. Cuando una lista carga más registros que el umbral configurado, solo se renderizan en el DOM las filas visibles en pantalla (más un margen de seguridad). Dos filas espaciadoras mantienen la altura total de la tabla, por lo que la barra de scroll se comporta con normalidad.

> [!IMPORTANT]
> Con esto, mostrar **10 000/10 000 registros** en el paginador deja de congelar el navegador: el DOM contiene ~30-60 filas en todo momento, independientemente de cuántos registros estén cargados en memoria.

## Características

- **Render virtual**: solo las filas del viewport + overscan se pintan en el DOM.
- **Carga por lotes**: a partir de 30 000 registros, los datapoints reactivos se materializan en lotes para no bloquear el hilo principal.
- **Agregados y selección cacheados**: durante el scroll se reutiliza el último valor calculado en lugar de recorrer todos los registros por frame.
- **Sin cambios en el servidor**: Odoo sigue enviando los registros en una sola petición `web_search_read`; el módulo resuelve el cuello de botella del renderizado en el navegador.
- **Configurable por UI**: el umbral se ajusta desde **Ajustes generales** sin reiniciar el servidor.

## Instalación

1. Copia o clona este directorio dentro de tu `addons_path` de Odoo 19.
2. Actualiza la lista de aplicaciones en Odoo.
3. Busca **Web List Virtual Rows** en Aplicaciones e instálalo.

```bash
./odoo-bin -u kq_web_list_virtual -d <tu_base_de_datos>
```

## Configuración

Ve a **Ajustes → Web List Virtual Rows → Umbral de virtualización de listas**.

| Parámetro | Descripción |
|-----------|-------------|
| `kq_web_list_virtual.threshold` | Registros cargados a partir de los cuales se activa la virtualización. Valor por defecto: `200`. |
| `0` | Desactiva la virtualización por completo. |

> [!NOTE]
> El valor se guarda como parámetro de configuración (`ir.config_parameter`) y llega al navegador a través de `session_info`.

## Cómo funciona

```text
┌─────────────────────────────────────┐
│  Vista lista (ListRenderer)         │
│  ┌─────────────────────────────┐    │
│  │  Espaciador superior        │    │
│  │  ┌─────────────────────┐    │    │
│  │  │ Filas visibles      │    │    │
│  │  │ (~viewport + 15)    │    │    │
│  │  └─────────────────────┘    │    │
│  │  Espaciador inferior        │    │
│  └─────────────────────────────┘    │
└─────────────────────────────────────┘
```

### Componentes principales

- `static/src/list_renderer_virtual.js` — parchea `ListRenderer` para calcular la ventana visible a partir del scroll, medir la altura media de las filas y exponer `getVirtualRecords()`.
- `static/src/list_renderer_virtual.xml` — hereda `web.ListRenderer.Rows` para iterar solo sobre la ventana visible e insertar las filas espaciadoras.
- `static/src/relational_model_chunked.js` — parchea `DynamicRecordList` y `ListController` para materializar los registros en lotes cuando el volumen supera el umbral.
- `static/src/list_renderer_virtual.scss` — aplica `overflow-anchor: none` al contenedor activo, evitando que el navegador "devuelva" el scroll al cambiar la altura de los espaciadores.
- `models/ir_http.py` — inyecta el umbral en `session_info`.
- `models/res_config_settings.py` — expone el ajuste en **Ajustes generales** con validación de valores negativos.

## Alcance

La virtualización se aplica **solo** a la lista raíz de una vista lista cuando:

- no está agrupada,
- no es editable en línea,
- no es un campo `x2many` embebido,
- el número de registros cargados supera el umbral configurado.

En cualquier otro caso se conserva el renderizado estándar de Odoo, que es seguro para la edición, las agrupaciones y los campos relacionales.

> [!WARNING]
> El servidor sigue enviando todos los registros solicitados en una sola petición. Este módulo elimina el bloqueo del navegador causado por el DOM masivo y la creación síncrona de datapoints, pero no reduce el tamaño de la respuesta del servidor ni la memoria total ocupada por los registros reactivos.

## Tests

El módulo incluye tests de Python para validar la configuración del umbral:

```bash
./odoo-bin -u kq_web_list_virtual -d <tu_base_de_datos> --test-enable --stop-after-init
```

Casos cubiertos:

- El umbral se almacena correctamente como parámetro de configuración.
- El valor `0` persiste y desactiva la virtualización.
- No se permiten valores negativos.

## Notas

- La paridad del rayado (`table-striped`) se conserva manteniendo impar el índice inicial de la ventana cuando hay espaciador superior.
- Si llega una nueva carga de datos mientras aún hay lotes pendientes, el proceso de lotes se abandona para evitar inconsistencias.
