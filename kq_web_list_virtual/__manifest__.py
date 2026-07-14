{
    "name": "Web List Virtual Rows",
    "summary": (
        "Virtualiza las filas de las vistas lista: solo se renderizan las filas "
        "visibles, por lo que cargar miles de registros no congela el navegador."
    ),
    "version": "19.0.1.0.0",
    "category": "Web",
    "license": "LGPL-3",
    "author": "Kimi Code",
    "depends": ["web"],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "kq_web_list_virtual/static/src/**/*",
        ],
    },
    "installable": True,
    "application": False,
    "auto_install": False,
}
