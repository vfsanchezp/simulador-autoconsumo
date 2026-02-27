
import yaml
from simulador.carga_datos import cargar_datos
from simulador.modelo import simular
from simulador.kpis import calcular_kpis
from simulador.plots import plot_resultados_interactivo


def main():
    # ── 1. Config ──────────────────────────────────────────────
    with open("proyecto_simulacion/config.yaml", "r") as f:
        config = yaml.safe_load(f)

    # ── 2. Datos ───────────────────────────────────────────────
    print("Cargando datos...")
    df = cargar_datos(
        config["file_precios"],
        config["file_solar"],
        config["file_consumo"],
        config
    )

    # ── 3. Simulación ──────────────────────────────────────────
    print("Ejecutando simulación...")
    df_res = simular(df, config)

    # ── 4. KPIs ────────────────────────────────────────────────
    # calcular_kpis() calcula los LCOE y los inyecta en config
    # antes de calcular los costes totales, por lo que los KPI 2 y 3
    # ya reflejan los costes reales de la instalación.
    print("Calculando KPIs...")
    kpis = calcular_kpis(df_res, config)

    print("\n===== RESULTADOS =====")
    for k, v in kpis.items():
        print(f"  {k}: {v}")

    # ── 5. Excel de resultados ─────────────────────────────────
    # Los LCOE ya han sido calculados y están en config
    price_col   = config["price_column"]
    pv_cost     = float(config["pv_energy_cost_eur_per_mwh"])
    batt_cost   = float(config["battery_energy_cost_eur_per_mwh"])

    df_export = df_res.copy()

    df_export["coste_horario_€"] = (
        df_export["grid_import"]    * df_export[price_col]
      + df_export["pv_to_load"]     * pv_cost
      + df_export["batt_discharge"] * batt_cost
    )
    df_export["energia_bateria_MWh"] = df_export["soc"] * float(config["battery_mwh"])

    columnas_a_eliminar = [
        "Día",
        "Periodo",
        "Precio Electricidad (€/MWh)",
        "Cargos y peajes 6.1TD (€/MWh)",
        "datetime",
        "Producción solar",
        "soc",
    ]
    df_export.drop(columns=columnas_a_eliminar, inplace=True, errors="ignore")

    df_export.to_excel("resultados_simulacion.xlsx", index=False)
    print("\nArchivo generado: resultados_simulacion.xlsx")

    # ── 6. Gráfica interactiva ─────────────────────────────────
    out_html = "resultados_plot.html"
    plot_resultados_interactivo(df_res, config, out_html=out_html)
    print(f"Gráfica interactiva generada: {out_html}")
    print("Ábrela en el navegador para usar el zoom y el range slider.")


if __name__ == "__main__":
    main()
