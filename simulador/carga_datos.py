import pandas as pd

def cargar_datos(file_precios, file_solar, file_consumo, config):
    df_p = pd.read_excel(file_precios)
    df_s = pd.read_excel(file_solar)
    df_c = pd.read_excel(file_consumo)

    dt_col = config["datetime_column"]

    # --- CONVERSIÓN A DATETIME ---
    df_p["datetime"] = pd.to_datetime(df_p[dt_col])
    df_s["datetime"] = pd.to_datetime(df_s[dt_col])
    df_c["datetime"] = pd.to_datetime(df_c[dt_col])

    # --- MERGE PRECIOS + SOLAR ---
    df = df_p.merge(
        df_s[["datetime", config["pv_column"]]],
        on="datetime",
        how="left"
    )

    # --- MERGE CONSUMO ---
    df = df.merge(
        df_c[["datetime", config["consumption_column"]]],
        on="datetime",
        how="left"
    )

    # --- ESCALADO FV ---
    df["pv_MWh"] = df[config["pv_column"]] * (
        config["pv_mw"] / config["pv_reference_mw"]
    )

    return df
