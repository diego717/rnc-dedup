"""
Deduplicación probabilística de casos de cáncer que llegan desde varias
fuentes, usando Splink (modelo Fellegi-Sunter) sobre DuckDB.

Pasos:
  1. Normaliza nombres, apellidos, cédula y fechas.
  2. Entrena el modelo sin etiquetas (EM) y estima frecuencias de nombres.
  3. Genera pares candidatos, los agrupa en "casos" (clusters).
  4. Arma una cola de revisión humana con los pares dudosos.
  5. Calcula indicadores de calidad tipo IARC por caso consolidado.
  6. Si existe datos/verdad.csv (solo con datos sintéticos), evalúa
     precisión y exhaustividad contra la verdad y contra la línea base
     "deduplicar solo por cédula exacta".

Uso:
  python deduplicar.py --entrada datos/registros.csv --salida resultados
"""
from __future__ import annotations

import argparse
import json
import unicodedata
from itertools import combinations
from pathlib import Path

import pandas as pd
import splink.comparison_library as cl
import splink.comparison_level_library as cll
from splink import Linker, SettingsCreator, block_on
from splink.backends.duckdb import DuckDBAPI

from generar_sinteticos import cedula_valida


# ---------------------------------------------------------------- normalización
def normalizar_texto(s) -> str | None:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    s = str(s).strip()
    if not s:
        return None
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("-", " ")
    return " ".join(s.split())


def _txt(s) -> str | None:
    return s.strip() or None if isinstance(s, str) else None


def preparar(df: pd.DataFrame) -> pd.DataFrame:
    filas = []
    for r in df.to_dict("records"):
        f = {k: _txt(v) if k != "unique_id" else int(v) for k, v in r.items()}
        for c in ["nombre1", "nombre2", "apellido1", "apellido2", "departamento"]:
            f[c] = normalizar_texto(f[c])
        # "de leon" -> "deleon": partículas de apellidos compuestos
        for c in ["apellido1", "apellido2"]:
            f[c] = f[c].replace(" ", "") if f[c] else None
        ci = "".join(ch for ch in (f["cedula"] or "") if ch.isdigit())
        f["cedula"] = ci.zfill(8) if ci else None
        f["cedula_ok"] = bool(ci) and cedula_valida(ci)
        f["anio_nac"] = f["fecha_nacimiento"][:4] if f["fecha_nacimiento"] else None
        filas.append(f)
    return pd.DataFrame(filas).astype(object)


# ---------------------------------------------------------------- modelo
def comparacion_apellido1() -> cl.CustomComparison:
    """Apellido paterno, con un nivel extra para apellidos invertidos
    (p.ej. 'Techera Acosta' vs 'Acosta Techera'), algo común entre fuentes."""
    jw = "jaro_winkler_similarity(apellido1_l, apellido1_r)"
    return cl.CustomComparison(
        output_column_name="apellido1",
        comparison_levels=[
            cll.NullLevel("apellido1"),
            cll.ExactMatchLevel("apellido1").configure(tf_adjustment_column="apellido1"),
            {"sql_condition": f"{jw} >= 0.92", "label_for_charts": "Jaro-Winkler >= 0.92"},
            {"sql_condition": "apellido1_l = apellido2_r OR apellido2_l = apellido1_r",
             "label_for_charts": "Apellidos invertidos"},
            {"sql_condition": f"{jw} >= 0.85", "label_for_charts": "Jaro-Winkler >= 0.85"},
            cll.ElseLevel(),
        ],
    )


def configurar() -> SettingsCreator:
    return SettingsCreator(
        link_type="dedupe_only",
        comparisons=[
            cl.LevenshteinAtThresholds("cedula", [1, 2]),
            cl.JaroWinklerAtThresholds("nombre1", [0.92, 0.85]).configure(
                term_frequency_adjustments=True),
            comparacion_apellido1(),
            cl.JaroWinklerAtThresholds("apellido2", [0.92, 0.85]),
            cl.DateOfBirthComparison(
                "fecha_nacimiento", input_is_string=True,
                datetime_metrics=["month", "year"], datetime_thresholds=[1, 1]),
            cl.ExactMatch("departamento"),
            cl.ExactMatch("lateralidad"),
        ],
        blocking_rules_to_generate_predictions=[
            block_on("cedula"),
            block_on("apellido1", "fecha_nacimiento"),
            block_on("nombre1", "apellido1", "anio_nac"),
            block_on("apellido2", "fecha_nacimiento"),
            block_on("nombre1", "fecha_nacimiento"),
            block_on("fecha_nacimiento", "departamento"),
        ],
        retain_intermediate_calculation_columns=False,
        additional_columns_to_retain=["fuente"],
    )


def entrenar(linker: Linker) -> None:
    linker.training.estimate_probability_two_random_records_match(
        [block_on("cedula"), block_on("apellido1", "fecha_nacimiento")], recall=0.8)
    linker.training.estimate_u_using_random_sampling(max_pairs=2e6)
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("fecha_nacimiento"))
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("cedula"))
    linker.training.estimate_parameters_using_expectation_maximisation(
        block_on("nombre1", "apellido1"))


# ---------------------------------------------------------------- calidad
def indicadores(df: pd.DataFrame, clusters: pd.DataFrame) -> dict:
    m = df.merge(clusters[["unique_id", "cluster_id"]], on="unique_id")
    por_caso = m.groupby("cluster_id")["fuente"].agg(set)
    n = len(por_caso)
    mv = por_caso.map(lambda s: "AP" in s).mean()
    dco = por_caso.map(lambda s: s == {"CD"}).mean()
    return {
        "registros_recibidos": int(len(df)),
        "casos_consolidados": int(n),
        "registros_por_caso": round(len(df) / n, 2),
        "pct_verificacion_microscopica": round(100 * mv, 1),
        "pct_solo_certificado_defuncion": round(100 * dco, 1),
        "pct_registros_sin_cedula": round(100 * df["cedula"].isna().mean(), 1),
        "pct_cedulas_invalidas": round(
            100 * ((~df["cedula_ok"]) & df["cedula"].notna()).mean(), 1),
        "pct_sin_fecha_nacimiento": round(100 * df["fecha_nacimiento"].isna().mean(), 1),
        "pct_sin_departamento": round(100 * df["departamento"].isna().mean(), 1),
    }


# ---------------------------------------------------------------- evaluación
def pares_de_clusters(asig: pd.Series) -> set[tuple[int, int]]:
    pares = set()
    for _, ids in asig.groupby(asig):
        for a, b in combinations(sorted(ids.index), 2):
            pares.add((a, b))
    return pares


def unir_con_revision(clusters: pd.DataFrame, dudosos: pd.DataFrame,
                      verdad: pd.Series) -> pd.Series:
    """Simula que un revisor humano confirma correctamente los pares dudosos."""
    padre = dict(zip(clusters.unique_id, clusters.cluster_id))
    raiz = {c: c for c in set(padre.values())}

    def buscar(x):
        while raiz[x] != x:
            raiz[x] = raiz[raiz[x]]
            x = raiz[x]
        return x

    for a, b in zip(dudosos.unique_id_l, dudosos.unique_id_r):
        if verdad[a] == verdad[b]:  # el revisor dice "es la misma persona"
            raiz[buscar(padre[a])] = buscar(padre[b])
    return pd.Series({u: buscar(c) for u, c in padre.items()})


def evaluar(clusters: pd.DataFrame, verdad: pd.DataFrame, df: pd.DataFrame,
            dudosos: pd.DataFrame) -> dict:
    v = verdad.set_index("unique_id")["persona_id"]
    p = clusters.set_index("unique_id")["cluster_id"]
    reales = pares_de_clusters(v)
    pred = pares_de_clusters(p)
    tp = len(reales & pred)

    # línea base: unir solo registros con la misma cédula exacta
    base = df.set_index("unique_id")["cedula"].dropna()
    pred_base = pares_de_clusters(base)
    tp_b = len(reales & pred_base)

    def met(tp, pred, reales):
        prec = tp / len(pred) if pred else 0
        rec = tp / len(reales) if reales else 0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0
        return {"precision": round(prec, 4), "exhaustividad": round(rec, 4), "f1": round(f1, 4)}

    rev = unir_con_revision(clusters, dudosos, v)
    pred_rev = pares_de_clusters(rev)
    tp_r = len(reales & pred_rev)

    casos_reales = v.nunique()
    casos_base = base.nunique() + int(df["cedula"].isna().sum())
    return {
        "personas_reales": int(casos_reales),
        "splink": {**met(tp, pred, reales), "casos_estimados": int(p.nunique()),
                   "error_conteo_casos_pct": round(100 * (p.nunique() - casos_reales) / casos_reales, 1)},
        "splink_mas_revision_humana": {**met(tp_r, pred_rev, reales), "casos_estimados": int(rev.nunique()),
                   "error_conteo_casos_pct": round(100 * (rev.nunique() - casos_reales) / casos_reales, 1)},
        "solo_cedula_exacta": {**met(tp_b, pred_base, reales), "casos_estimados": int(casos_base),
                               "error_conteo_casos_pct": round(100 * (casos_base - casos_reales) / casos_reales, 1)},
    }


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", type=Path, default=Path("datos/registros.csv"))
    ap.add_argument("--verdad", type=Path, default=Path("datos/verdad.csv"))
    ap.add_argument("--salida", type=Path, default=Path("resultados"))
    ap.add_argument("--umbral", type=float, default=0.95,
                    help="probabilidad mínima para unir automáticamente")
    ap.add_argument("--umbral-revision", type=float, default=0.5,
                    help="pares entre este valor y --umbral van a revisión humana")
    a = ap.parse_args()
    a.salida.mkdir(parents=True, exist_ok=True)

    crudo = pd.read_csv(a.entrada, dtype=str, keep_default_na=False)
    crudo["unique_id"] = crudo["unique_id"].astype(int)
    df = preparar(crudo)

    db = DuckDBAPI()
    sdf = db.register(df)
    linker = Linker(sdf, configurar(), log_level=30)
    entrenar(linker)

    pred = linker.inference.predict(threshold_match_probability=a.umbral_revision)
    clusters = linker.clustering.cluster_pairwise_predictions_at_threshold(
        pred, threshold_match_probability=a.umbral).as_pandas_dataframe()
    pares = pred.as_pandas_dataframe()

    # cola de revisión: pares dudosos, con los datos de ambos lados
    cols = ["unique_id", "fuente", "nombre1", "apellido1", "apellido2", "cedula",
            "fecha_nacimiento", "departamento", "lateralidad"]
    dud = pares[(pares.match_probability >= a.umbral_revision) &
                (pares.match_probability < a.umbral)].copy()
    l = df[cols].add_suffix("_l")
    r = df[cols].add_suffix("_r")
    dud = (dud[["unique_id_l", "unique_id_r", "match_probability", "match_weight"]]
           .merge(l, on="unique_id_l").merge(r, on="unique_id_r")
           .sort_values("match_probability", ascending=False))
    dud.to_csv(a.salida / "cola_revision.csv", index=False)

    casos = df.merge(clusters[["unique_id", "cluster_id"]], on="unique_id")
    casos.to_csv(a.salida / "registros_con_caso.csv", index=False)

    linker.misc.save_model_to_json(str(a.salida / "modelo.json"), overwrite=True)
    try:
        linker.visualisations.match_weights_chart().save(str(a.salida / "pesos_modelo.html"))
    except Exception as e:  # el gráfico es opcional
        print("No se pudo guardar el gráfico de pesos:", e)

    resumen = {"indicadores_calidad": indicadores(df, clusters),
               "pares_en_revision": int(len(dud)),
               "umbral_union_automatica": a.umbral}
    if a.verdad.exists():
        resumen["evaluacion"] = evaluar(clusters, pd.read_csv(a.verdad), df, dud)
    (a.salida / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False))
    print(json.dumps(resumen, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
