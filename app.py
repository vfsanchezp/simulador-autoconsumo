import io
import math
import os
import uuid

import yaml
from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

from simulador.carga_datos import cargar_datos
from simulador.kpis import calcular_kpis
from simulador.modelo import simular
from simulador.plots import get_plot_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)

# Almacenamiento en memoria para descargas (uso único)
_excel_store: dict[str, bytes] = {}
_html_store: dict[str, str] = {}


def _load_config():
    with open(os.path.join(BASE_DIR, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sanitize_kpis(kpis: dict) -> dict:
    """Convierte inf y nan a None para serialización JSON segura."""
    result = {}
    for k, v in kpis.items():
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            result[k] = None
        else:
            result[k] = v
    return result


@app.route("/referencias/<path:filename>")
def serve_referencias(filename):
    return send_from_directory(os.path.join(BASE_DIR, "referencias"), filename)


@app.route("/")
def index():
    config = _load_config()
    return render_template("index.html", config=config)


@app.route("/api/simulate", methods=["POST"])
def simulate():
    try:
        config = _load_config()

        # Sobreescribir con parámetros del usuario
        user = request.get_json(force=True)

        float_keys = [
            "pv_mw", "pv_reference_mw", "capex_fv_eur_per_kwp",
            "battery_mwh", "battery_mw", "eta_ch", "eta_dis",
            "soc_min", "soc_max", "soc_ini", "capex_bat_eur_per_kwh",
            "tasa_descuento",
        ]
        int_keys = ["vida_util_fv_anos", "ciclos_garantizados_fabricante"]
        bool_keys = ["allow_export", "allow_charge_from_grid"]

        for k in float_keys:
            if k in user:
                config[k] = float(user[k])
        for k in int_keys:
            if k in user:
                config[k] = int(user[k])
        for k in bool_keys:
            if k in user:
                config[k] = bool(user[k])

        # Paths de datos absolutos (independiente del CWD)
        config["file_precios"] = os.path.join(BASE_DIR, "data", "precios.xlsx")
        config["file_solar"] = os.path.join(BASE_DIR, "data", "solar_49mw.xlsx")
        config["file_consumo"] = os.path.join(BASE_DIR, "data", "consumo.xlsx")

        # Pipeline de simulación
        df = cargar_datos(
            config["file_precios"],
            config["file_solar"],
            config["file_consumo"],
            config,
        )
        df_res = simular(df, config)

        # KPIs (inyecta LCOE en config)
        kpis = calcular_kpis(df_res, config)

        # Excel en memoria (misma lógica que main.py)
        price_col = config["price_column"]
        pv_cost = float(config["pv_energy_cost_eur_per_mwh"])
        batt_cost = float(config["battery_energy_cost_eur_per_mwh"])

        df_export = df_res.copy()
        df_export["coste_horario_€"] = (
            df_export["grid_import"] * df_export[price_col]
            + df_export["pv_to_load"] * pv_cost
            + df_export["batt_discharge"] * batt_cost
        )
        df_export["energia_bateria_MWh"] = df_export["soc"] * float(config["battery_mwh"])

        columnas_a_eliminar = [
            "Día", "Periodo", "Precio Electricidad (€/MWh)",
            "Cargos y peajes 6.1TD (€/MWh)", "datetime",
            "Producción solar", "soc",
        ]
        df_export.drop(columns=columnas_a_eliminar, inplace=True, errors="ignore")

        excel_buf = io.BytesIO()
        df_export.to_excel(excel_buf, index=False)
        excel_token = uuid.uuid4().hex
        _excel_store[excel_token] = excel_buf.getvalue()

        # Gráfica JSON
        plot_json = get_plot_json(df_res, config)

        # HTML descargable (envuelto en página mínima con CDN)
        html_content = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script>"
            "</head><body style='margin:0'>"
            "<div id='chart' style='width:100%;height:100vh'></div>"
            "<script>"
            f"var fig={plot_json};"
            "Plotly.newPlot('chart',fig.data,fig.layout,{responsive:true});"
            "</script></body></html>"
        )
        html_token = uuid.uuid4().hex
        _html_store[html_token] = html_content

        return jsonify(
            {
                "status": "ok",
                "kpis": _sanitize_kpis(kpis),
                "plot_json": plot_json,
                "excel_token": excel_token,
                "html_token": html_token,
            }
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/download/<token>")
def download_excel(token):
    data = _excel_store.pop(token, None)
    if data is None:
        return jsonify({"error": "Token inválido o expirado"}), 404
    return send_file(
        io.BytesIO(data),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name="resultados_simulacion.xlsx",
    )


@app.route("/api/download-html/<token>")
def download_html(token):
    content = _html_store.pop(token, None)
    if content is None:
        return jsonify({"error": "Token inválido o expirado"}), 404
    return send_file(
        io.BytesIO(content.encode("utf-8")),
        mimetype="text/html",
        as_attachment=True,
        download_name="resultados_plot.html",
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
