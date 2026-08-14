#!/usr/bin/env bash
# Ejecuta los tests de los módulos kq-perf en un entorno Odoo 19.
#
# Uso:
#   ODOO_BIN=/ruta/a/odoo-bin DATABASE=kq_perf ADDONS_PATH=/ruta/a/addons ./scripts/run_tests.sh
#   ./scripts/run_tests.sh --tags :TestsPointA,

set -euo pipefail

ODOO_BIN="${ODOO_BIN:-./odoo-bin}"
DATABASE="${DATABASE:-${1:-}}"
ADDONS_PATH="${ADDONS_PATH:-}"
MODULES="${MODULES:-kq_export_async,kq_web_list_virtual}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

if [[ -z "$DATABASE" ]]; then
    echo "Error: define DATABASE o pasa el nombre de la base de datos como primer argumento." >&2
    echo "Ejemplo: DATABASE=kq_perf ./scripts/run_tests.sh" >&2
    exit 1
fi

if ! command -v "$ODOO_BIN" >/dev/null 2>&1 && [[ ! -x "$ODOO_BIN" ]]; then
    echo "Error: no se encontró odoo-bin en '$ODOO_BIN'. Define ODOO_BIN." >&2
    exit 1
fi

ARGS=(
    "$ODOO_BIN"
    -d "$DATABASE"
    -u "$MODULES"
    --test-enable
    --stop-after-init
    --log-level=test
)

if [[ -n "$ADDONS_PATH" ]]; then
    ARGS+=("--addons-path=$ADDONS_PATH")
fi

if [[ -n "$EXTRA_ARGS" ]]; then
    # shellcheck disable=SC2206
    ARGS+=($EXTRA_ARGS)
fi

echo "Ejecutando tests para: $MODULES"
echo "Comando: ${ARGS[*]}"
"${ARGS[@]}"
