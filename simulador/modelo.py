
import numpy as np


def simular(df, config):
    """
    Optimiza la descarga para horas caras, pero con horizonte:
    desde que la batería empieza a cargar (batt_charge>0 en el inicio del ciclo)
    hasta la siguiente oportunidad de carga FV (inicio de bloque de excedente),
    incluyendo madrugada y varios días si hace falta.

    Implementación:
    - Detectamos candidatos de "oportunidad FV" = inicios de bloque con excedente FV (>threshold).
    - Construimos ciclos dinámicamente:
      * elegimos un candidato como "fin de ciclo"
      * resolvemos LP desde inicio hasta ese candidato con penalización de SOC final baja
      * si al llegar al candidato la batería quedaría "llena" (sin hueco), extendemos el horizonte
        al siguiente candidato y resolvemos de nuevo (merge de periodos).
    - Esto aproxima tu requisito: el ciclo acaba cuando vuelve a existir opción real de cargar.
    """
    from scipy.optimize import linprog

    df = df.copy()
    n_total = len(df)

    cap = float(config["battery_mwh"])
    pmax = float(config["battery_mw"])
    eta_ch = float(config["eta_ch"])
    eta_dis = float(config["eta_dis"])
    soc_min = float(config["soc_min"])
    soc_max = float(config["soc_max"])
    soc_full_eps = float(config.get("soc_full_epsilon", 1e-4))

    soc = np.zeros(n_total + 1)
    soc[0] = float(config["soc_ini"])

    price = df[config["price_column"]].to_numpy(dtype=float)
    load = df[config["consumption_column"]].to_numpy(dtype=float)
    pv = df["pv_MWh"].to_numpy(dtype=float)

    pv_to_load = np.minimum(pv, load)
    deficit = np.maximum(load - pv, 0.0)
    excess_pv = np.maximum(pv - load, 0.0)

    # candidatos: inicio de bloque de excedente FV
    thr = float(config.get("excess_threshold_mwh", 1e-6))
    has_excess = excess_pv > thr
    cand = np.where(has_excess & ~np.r_[False, has_excess[:-1]])[0]

    # Si no hay excedente FV nunca, no hay ciclos:
    if len(cand) == 0:
        df_res = df.copy()
        df_res["pv_to_load"] = pv_to_load
        df_res["batt_charge"] = 0.0
        df_res["batt_discharge"] = 0.0
        df_res["grid_import"] = deficit
        df_res["curtail"] = 0.0
        df_res["soc"] = soc[0]
        return df_res

    end_soc_target = float(config.get("end_soc_target", soc_min))
    end_penalty = float(config.get("end_soc_penalty_eur_per_mwh", 2000.0))

    batt_charge = np.zeros(n_total)
    batt_discharge = np.zeros(n_total)
    grid_import = np.zeros(n_total)
    curtail = np.zeros(n_total)

    # ---------- LP de un horizonte [t0, t1) ----------
    def solve_lp(t0, t1, soc0):
        """
        Variables: x = [ch_0..ch_{n-1}, dis_0..dis_{n-1}, slack_end]
        - ch acotada por exceso FV y potencia
        - dis acotada por déficit y potencia
        - SOC dentro de [min, max]
        - Penalización por acabar por encima de end_soc_target mediante slack_end (soft)
        """
        n = t1 - t0
        if n <= 0:
            return np.array([]), np.array([])

        ch_ub = np.minimum(excess_pv[t0:t1], pmax)
        dis_ub = np.minimum(deficit[t0:t1], pmax)

        # Objetivo: minimizar coste red = sum((deficit - dis)*price) + end_penalty*slack
        # Constante sum(deficit*price) se ignora -> minimizamos sum(-dis*price) + penalty*slack
        bonus = float(config.get("charge_early_bonus_eur_per_mwh", 0.01))

        # pesos: más altos al inicio => incentiva cargar al principio
        if config.get("charge_early_shape", "linear") == "linear":
            w = np.linspace(1.0, 0.0, n)   # 1 al inicio, 0 al final
        else:
            w = np.ones(n)

        c = np.concatenate([
            -bonus * w,
            -price[t0:t1],
            np.array([end_penalty])
        ])

        bounds = (
            [(0.0, float(ch_ub[i])) for i in range(n)] +
            [(0.0, float(dis_ub[i])) for i in range(n)] +
            [(0.0, None)]  # slack_end >= 0
        )

        # SOC acumulado
        L = np.tril(np.ones((n, n), dtype=float))
        A_soc = np.hstack([
            (eta_ch / cap) * L,
            (-1.0 / (eta_dis * cap)) * L,
            np.zeros((n, 1))
        ])

        # soc(k+1) <= soc_max
        A1 = A_soc
        b1 = np.full(n, soc_max - soc0, dtype=float)

        # soc(k+1) >= soc_min  -> -A_soc x <= -(soc_min - soc0)
        A2 = -A_soc
        b2 = np.full(n, -(soc_min - soc0), dtype=float)

        A_ub = np.vstack([A1, A2])
        b_ub = np.concatenate([b1, b2])

        # Soft constraint final:
        # soc_end <= end_soc_target + slack/cap
        # => soc_end - slack/cap <= end_soc_target
        A_last = A_soc[-1, :].reshape(1, -1)
        A_end = A_last.copy()
        A_end[0, -1] = -1.0 / cap
        b_end = np.array([end_soc_target - soc0], dtype=float)

        A_ub = np.vstack([A_ub, A_end])
        b_ub = np.concatenate([b_ub, b_end])

        res = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if not res.success:
            # fallback: sin decisión (equivalente a no usar batería en ese tramo)
            return np.zeros(n), np.zeros(n)

        x = res.x
        ch = x[:n]
        dis = x[n:2*n]
        return ch, dis

    # --------------------------------------------------
    # Construcción dinámica de ciclos:
    # inicio del primer ciclo = primer candidato donde se pueda cargar (haya hueco)
    # Pero el hueco depende de SOC; al principio usamos soc_ini.
    # --------------------------------------------------
    # Encontrar un primer candidato que NO esté "lleno"
    start_ptr = 0
    while start_ptr < len(cand) and soc[cand[start_ptr]] >= (soc_max - soc_full_eps):
        start_ptr += 1

    # Si sigue lleno en todos los candidatos (raro), arrancamos en el primero igualmente
    if start_ptr >= len(cand):
        start_ptr = 0

    t_start = cand[start_ptr]

    # Simulamos el tramo anterior a t_start sin optimización (solo FV->load y red),
    # porque todavía no ha empezado un ciclo de “carga real”.
    for t in range(0, t_start):
        batt_charge[t] = 0.0
        batt_discharge[t] = 0.0
        grid_import[t] = deficit[t]
        curtail[t] = excess_pv[t]  # si estaba llena/no queremos contar carga antes de ciclo
        soc[t+1] = soc[t]  # SOC constante

    # Ahora, ciclo a ciclo
    current = t_start
    cand_idx = start_ptr + 1  # siguiente candidato potencial para fin de ciclo

    while current < n_total:
        # Elegimos un "fin" provisional = siguiente candidato de excedente o fin de serie
        if cand_idx < len(cand):
            t_end = cand[cand_idx]
        else:
            t_end = n_total

        # Re-solve con extensión si al llegar no queda hueco (condición de "opción real de cargar")
        # (Normalmente la penalización hará que sí haya hueco.)
        max_extend_steps = 10  # seguridad (evitar bucles raros)
        steps = 0

        while True:
            ch, dis = solve_lp(current, t_end, soc[current])

            # reconstruimos SOC sobre [current, t_end)
            soc_tmp = soc[current]
            for k in range(current, t_end):
                i = k - current
                soc_tmp = soc_tmp + (ch[i] * eta_ch - dis[i] / eta_dis) / cap
                soc_tmp = float(np.clip(soc_tmp, soc_min, soc_max))

            soc_at_end = soc_tmp

            # condición para que al llegar a t_end haya "opción real de cargar"
            # (si t_end es un candidato de excedente)
            if t_end < n_total and has_excess[t_end]:
                has_space = soc_at_end < (soc_max - soc_full_eps)
            else:
                has_space = True  # si es fin de la serie, no hace falta espacio

            if has_space or t_end == n_total or steps >= max_extend_steps:
                # aceptamos este horizonte
                break

            # si no hay hueco, extendemos hasta el siguiente candidato y resolvemos de nuevo
            cand_idx += 1
            if cand_idx < len(cand):
                t_end = cand[cand_idx]
            else:
                t_end = n_total
            steps += 1

        # Aplicar decisiones aceptadas y avanzar
        soc_k = soc[current]
        for k in range(current, t_end):
            i = k - current

            batt_charge[k] = ch[i]
            batt_discharge[k] = dis[i]

            grid_import[k] = deficit[k] - dis[i]
            curtail[k] = excess_pv[k] - ch[i]

            soc_k = soc_k + (ch[i] * eta_ch - dis[i] / eta_dis) / cap
            soc_k = float(np.clip(soc_k, soc_min, soc_max))
            soc[k+1] = soc_k

        # El siguiente ciclo debería empezar en la primera hora >= t_end donde:
        # - hay excedente FV (inicio de bloque) y
        # - la batería tiene hueco (soc < soc_max - eps)
        # Para ello buscamos el próximo candidato.
        current = t_end

        # mover cand_idx hasta el primer candidato >= current
        while cand_idx < len(cand) and cand[cand_idx] < current:
            cand_idx += 1

        # encontrar el siguiente inicio real de ciclo
        found = False
        j = cand_idx
        while j < len(cand):
            t_candidate = cand[j]
            if t_candidate >= n_total:
                break
            if soc[t_candidate] < (soc_max - soc_full_eps):
                current = t_candidate
                cand_idx = j + 1
                found = True
                break
            j += 1

        if not found:
            # no hay más ciclos “reales”; el resto lo simulamos sin batería
            for t in range(current, n_total):
                batt_charge[t] = 0.0
                batt_discharge[t] = 0.0
                grid_import[t] = deficit[t]
                curtail[t] = excess_pv[t]
                soc[t+1] = soc[t]
            break

    df_res = df.copy()
    df_res["pv_to_load"] = pv_to_load
    df_res["batt_charge"] = batt_charge
    df_res["batt_discharge"] = batt_discharge
    df_res["grid_import"] = grid_import
    df_res["curtail"] = curtail
    df_res["soc"] = soc[1:]
    return df_res
