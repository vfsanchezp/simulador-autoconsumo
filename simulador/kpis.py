# proyecto_simulacion/simulador/kpis.py

"""
Novedades respecto a la versión anterior:
  - Calcula automáticamente el LCOE de la FV (€/MWh) a partir de CAPEX, vida útil,
    horas equivalentes anuales reales (derivadas de la simulación) y tasa de descuento.
  - Calcula automáticamente los ciclos equivalentes anuales de la batería.
  - Deriva la vida útil de la batería a partir de los ciclos garantizados por el fabricante.
  - Calcula el LCOE de la batería (€/MWh descargado útil) con tasa de descuento.
  - KPI curtailment: % de producción FV perdida por curtailment.
  - LCOE FV calculado sobre energía FV aprovechada (excluyendo curtailment).
  - Expone todos estos valores como KPIs adicionales.
"""
import numpy as np

# ─────────────────────────────────────────────────────────────
# Helpers financieros
# ─────────────────────────────────────────────────────────────

def factor_anualizacion(r: float, n: int) -> float:
    """
    Devuelve el factor de anualización (suma de flujos descontados durante n años):
        FA = [1 - (1+r)^-n] / r
    Si r == 0 devuelve n (caso límite sin descuento).
    """
    if r == 0:
        return float(n)
    return (1 - (1 + r) ** (-n)) / r

def calcular_kpis(df_res, config):
    """
    KPIs energéticos y económicos.

    KPIs de coste:
      1) Coste si todo fuese red (100% red)
      2) Coste FV + Red (sin batería)
      3) Coste FV + Batería + Red (con la simulación)

    KPIs de autoconsumo:
      4) % energía FV sobre total consumo (sin batería)
      5) % energía FV + batería sobre total consumo (con batería)

    KPIs técnicos FV:
      6) Horas equivalentes anuales de producción FV (h/año)
      7) LCOE FV (€/MWh) calculado con tasa de descuento sobre energía aprovechada
     11) % de curtailment FV (energía perdida / energía total producida)

    KPIs técnicos batería:
      8) Ciclos equivalentes anuales de la batería
      9) Vida útil estimada de la batería (años, a partir de ciclos garantizados)
     10) LCOE batería (€/MWh descargado útil) calculado con tasa de descuento

    Los valores de LCOE_FV y LCOE_BAT se inyectan en config para que main.py
    los use en el cálculo del coste horario detallado.
    """

    price_col  = config["price_column"]
    cons_col   = config["consumption_column"]

    # ── Series base ────────────────────────────────────────────
    load           = df_res[cons_col]
    price          = df_res[price_col]
    pv_to_load     = df_res["pv_to_load"]
    grid_import    = df_res["grid_import"]
    batt_discharge = df_res["batt_discharge"]
    batt_charge    = df_res["batt_charge"]
    pv_mwh         = df_res["pv_MWh"]          # producción FV total horaria (MWh)
    curtail        = df_res["curtail"]

    # ── Parámetros de inversión y financiación ──────────────────
    capex_fv      = float(config["capex_fv_eur_per_kwp"])         # €/kWp
    capex_bat     = float(config["capex_bat_eur_per_kwh"])        # €/kWh
    vida_fv       = int(config["vida_util_fv_anos"])              # años
    r             = float(config["tasa_descuento"])               # fracción (e.g. 0.06)
    ciclos_fab    = int(config["ciclos_garantizados_fabricante"]) # ciclos garantizados
    pv_mw         = float(config["pv_mw"])                       # MW instalados FV
    bat_mwh_cap   = float(config["battery_mwh"])                  # MWh de capacidad batería
    eta_ch        = float(config["eta_ch"])
    eta_dis       = float(config["eta_dis"])

    # Eficiencia round-trip (si no se define explícitamente en config, se calcula)
    eta_rt = float(config.get("eta_roundtrip_bat", eta_ch * eta_dis))

    # ── Determinar número de años simulados ────────────────────
    n_horas_simuladas = len(df_res)
    anos_simulados    = n_horas_simuladas / 8760.0

    # ─────────────────────────────────────────────────────────────
    # KPI 11 — % de curtailment FV
    # curtailment_pct = energía curtailada / energía FV total producida × 100
    # ─────────────────────────────────────────────────────────────
    energia_fv_total_sim   = pv_mwh.sum()                          # MWh generados en la simulación
    energia_curtail_sim    = curtail.sum()                          # MWh curtailados
    energia_fv_aprovechada = energia_fv_total_sim - energia_curtail_sim  # MWh realmente aprovechados

    if energia_fv_total_sim > 0:
        curtailment_pct = (energia_curtail_sim / energia_fv_total_sim) * 100.0
    else:
        curtailment_pct = 0.0

    # ─────────────────────────────────────────────────────────────
    # KPI 6 — Horas equivalentes anuales FV (sobre energía aprovechada)
    # ─────────────────────────────────────────────────────────────
    energia_fv_aprovechada_anual = energia_fv_aprovechada / anos_simulados  # MWh/año aprovechados
    energia_fv_anual             = energia_fv_total_sim / anos_simulados     # MWh/año total (para referencia)
    horas_eq_fv_anuales_total    = energia_fv_anual / pv_mw                  # h/año totales (referencia)

    # Horas equivalentes sobre energía aprovechada (para LCOE)
    horas_eq_fv_aprovechada = energia_fv_aprovechada_anual / pv_mw           # h/año aprovechadas

    # ─────────────────────────────────────────────────────────────
    # KPI 7 — LCOE FV (€/MWh) sobre energía aprovechada
    # Se usa horas_eq_fv_aprovechada en lugar de horas_eq_fv_anuales_total
    # para que el LCOE refleje solo la energía que realmente se utiliza
    # ─────────────────────────────────────────────────────────────
    FA_fv = factor_anualizacion(r, vida_fv)
    if horas_eq_fv_aprovechada > 0:
        lcoe_fv = (capex_fv / horas_eq_fv_aprovechada / FA_fv) * 1000.0  # €/MWh
    else:
        lcoe_fv = float("nan")

    # ─────────────────────────────────────────────────────────────
    # KPI 8 — Ciclos equivalentes anuales de la batería
    # ─────────────────────────────────────────────────────────────
    energia_descargada_sim  = batt_discharge.sum()
    ciclos_eq_anuales       = (energia_descargada_sim / bat_mwh_cap) / anos_simulados

    # ─────────────────────────────────────────────────────────────
    # KPI 9 — Vida útil estimada de la batería (años)
    # ─────────────────────────────────────────────────────────────
    if ciclos_eq_anuales > 0:
        vida_bat_estimada = ciclos_fab / ciclos_eq_anuales
    else:
        vida_bat_estimada = float("inf")

    vida_bat_anos = int(np.floor(vida_bat_estimada))

    # ─────────────────────────────────────────────────────────────
    # KPI 10 — LCOE batería (€/MWh descargado útil)
    # ─────────────────────────────────────────────────────────────
    FA_bat  = factor_anualizacion(r, vida_bat_anos) if vida_bat_anos > 0 else 1.0
    if ciclos_eq_anuales > 0 and FA_bat > 0:
        lcoe_bat = (capex_bat / (ciclos_eq_anuales * eta_rt * FA_bat)) * 1000.0
    else:
        lcoe_bat = float("nan")
    # Inyectamos los LCOE en config para que main.py los use en el coste horario
    config["pv_energy_cost_eur_per_mwh"]      = lcoe_fv if not np.isnan(lcoe_fv) else 0.0
    config["battery_energy_cost_eur_per_mwh"] = lcoe_bat if not np.isnan(lcoe_bat) else 0.0

    # ─────────────────────────────────────────────────────────────
    # KPI 1 — Coste 100% red
    # ─────────────────────────────────────────────────────────────
    coste_100_red = (load * price).sum()

    # ─────────────────────────────────────────────────────────────
    # KPI 2 — Coste FV + red (sin batería)
    # ─────────────────────────────────────────────────────────────
    lcoe_fv_safe = lcoe_fv if not np.isnan(lcoe_fv) else 0.0
    grid_sin_batt         = (load - pv_to_load).clip(lower=0.0)
    coste_fv_red_sin_batt = (grid_sin_batt * price).sum() + (pv_to_load * lcoe_fv_safe).sum()

    # ─────────────────────────────────────────────────────────────
    # KPI 3 — Coste FV + batería + red (con simulación)
    # ─────────────────────────────────────────────────────────────
    lcoe_bat_safe = lcoe_bat if not np.isnan(lcoe_bat) else 0.0
    coste_fv_batt_red = (
        (grid_import    * price).sum()
      + (pv_to_load     * lcoe_fv_safe).sum()
      + (batt_discharge * lcoe_bat_safe).sum()
    )

    # ─────────────────────────────────────────────────────────────
    # KPI 4 y 5 — Fracciones de energía
    # ─────────────────────────────────────────────────────────────
    e_total          = load.sum()
    frac_fv_sin_batt = (pv_to_load.sum() / e_total) if e_total > 0 else 0.0
    frac_fv_batt     = ((pv_to_load.sum() + batt_discharge.sum()) / e_total) if e_total > 0 else 0.0

    return {
        # ── Costes totales ────────────────────────────────────────
        "KPI1_coste_100_red_€":                    round(coste_100_red,          2),
        "KPI2_coste_FV_y_red_sin_bateria_€":       round(coste_fv_red_sin_batt,  2),
        "KPI3_coste_FV_bateria_y_red_€":           round(coste_fv_batt_red,      2),

        # ── Autoconsumo ───────────────────────────────────────────
        "KPI4_pct_energia_FV_sin_bateria_%":       round(frac_fv_sin_batt * 100, 2),
        "KPI5_pct_energia_FV_mas_bateria_%":       round(frac_fv_batt     * 100, 2),

        # ── FV ────────────────────────────────────────────────────
        "KPI6_horas_equivalentes_FV_h_ano":        round(horas_eq_fv_anuales_total, 1),
        "KPI7_LCOE_FV_€_MWh":                      round(lcoe_fv_safe,           2),
        "KPI11_curtailment_FV_%":                  round(curtailment_pct,         2),

        # ── Batería ───────────────────────────────────────────────
        "KPI8_ciclos_eq_anuales_bateria":          round(ciclos_eq_anuales,      1),
        "KPI9_vida_util_estimada_bateria_anos":    vida_bat_anos,
        "KPI10_LCOE_bateria_€_MWh":               round(lcoe_bat_safe,          2),

        # ── Parámetros usados (trazabilidad) ─────────────────────
        "─ CAPEX FV €/kWp":                        capex_fv,
        "─ CAPEX batería €/kWh":                   capex_bat,
        "─ Vida útil FV años":                     vida_fv,
        "─ Tasa de descuento":                     f"{r*100:.1f}%",
        "─ Ciclos garantizados fabricante":        ciclos_fab,
        "─ Eta round-trip bateria":                round(eta_rt,                 4),
    }
