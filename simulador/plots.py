import plotly.graph_objects as go


def _build_figure(df_res, config):
    """Construye y devuelve el go.Figure con las 4 series de resultados."""
    x = df_res["datetime"]
    soc = df_res["soc"]
    precio = df_res[config["price_column"]]
    pv = df_res["pv_MWh"]
    consumo = df_res[config["consumption_column"]]

    fig = go.Figure()

    # 1) SOC (eje y)
    fig.add_trace(go.Scatter(
        x=x, y=soc,
        name="SOC batería",
        mode="lines",
        line=dict(color="#1f77b4", width=2),
        yaxis="y"
    ))

    # 2) Precio (eje y2)
    fig.add_trace(go.Scatter(
        x=x, y=precio,
        name="Precio electricidad (€/MWh)",
        mode="lines",
        line=dict(color="#d62728", width=2),
        yaxis="y2"
    ))

    # 3) Producción FV (eje y3)
    fig.add_trace(go.Scatter(
        x=x, y=pv,
        name="Producción FV (MWh)",
        mode="lines",
        line=dict(color="#2ca02c", width=2),
        yaxis="y3"
    ))

    # 4) Consumo planta biometano (eje y3 compartido, línea discontinua naranja)
    fig.add_trace(go.Scatter(
        x=x, y=consumo,
        name="Consumo planta biometano (MWh)",
        mode="lines",
        line=dict(color="#ff7f0e", width=2, dash="dash"),
        yaxis="y3"
    ))

    # Calcular rango del eje y3 para que cubra tanto FV como consumo
    y3_max = max(pv.max(), consumo.max()) * 1.05

    fig.update_layout(
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.12, xanchor="left", x=0),

        xaxis=dict(
            title="Tiempo",
            domain=[0.05, 0.90],
            rangeselector=dict(
                buttons=list([
                    dict(count=24, label="24h", step="hour", stepmode="backward"),
                    dict(count=7, label="7d", step="day", stepmode="backward"),
                    dict(count=1, label="1m", step="month", stepmode="backward"),
                    dict(count=3, label="3m", step="month", stepmode="backward"),
                    dict(step="all", label="Todo")
                ])
            ),
            rangeslider=dict(visible=True),
            type="date"
        ),

        # Eje izquierdo: SOC
        yaxis=dict(
            title="SOC (0–1)",
            range=[0, 1],
            tickformat=".2f"
        ),

        # Eje derecho 1: Precio
        yaxis2=dict(
            title="Precio (€/MWh)",
            overlaying="y",
            side="right",
            anchor="x",
            showgrid=False
        ),

        # Eje derecho 2: FV y Consumo (escala compartida)
        yaxis3=dict(
            title="FV / Consumo (MWh)",
            overlaying="y",
            side="right",
            anchor="free",
            position=0.97,
            range=[0, y3_max],
            showgrid=False
        ),

        margin=dict(r=80, l=60, t=30, b=120)
    )

    return fig


def plot_resultados_interactivo(df_res, config, out_html="resultados_plot.html"):
    """
    HTML interactivo con 4 líneas:
      - SOC batería (0..1) eje izquierdo
      - Precio (€/MWh) eje derecho (y2)
      - Producción FV (MWh) eje derecho adicional (y3) desplazado
      - Consumo planta biometano (MWh) eje y3 compartido, línea discontinua naranja
    Incluye range slider, zoom y botones de rango.
    """
    fig = _build_figure(df_res, config)
    fig.write_html(out_html, include_plotlyjs="cdn")
    return out_html


def get_plot_json(df_res, config):
    """Devuelve la figura como JSON string para renderizar con Plotly.js en el navegador."""
    return _build_figure(df_res, config).to_json()
