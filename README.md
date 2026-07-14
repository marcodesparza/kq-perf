# kq-perf

[![Odoo](https://img.shields.io/badge/Odoo-19.0-875A7B?logo=odoo)](https://www.odoo.com)
[![License](https://img.shields.io/badge/License-LGPL--3-00A4DE?logo=gnu)](LICENSE)

Colección de módulos de Odoo creada por **marcodesparza** para mejorar el rendimiento general de Odoo en escenarios reales de alto volumen.

> [!IMPORTANT]
> El objetivo de este proyecto es atacar los cuellos de botella de performance del cliente y del servidor de Odoo de forma práctica, medible y mantenible.

## Módulos

| Módulo | Descripción |
|--------|-------------|
| [`kq_web_list_virtual`](kq_web_list_virtual/) | Virtualiza las filas de las vistas lista del backend para que cargar decenas de miles de registros no congele el navegador. |

## Filosofía

- Cambios mínimos y enfocados en el problema real.
- Sin alterar el comportamiento estándar de Odoo donde no es necesario.
- Código limpio, documentado y con tests.

## Autor

**marcodesparza**

## Licencia

Este proyecto se distribuye bajo la licencia LGPL-3.
