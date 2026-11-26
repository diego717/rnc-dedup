"""
Genera un informe HTML en español, autocontenido (sin internet), que explica
cuánto pesa cada coincidencia o diferencia en el modelo entrenado.

Uso directo:  python informe_pesos.py resultados/modelo.json resultados/pesos_modelo.html
También lo llama deduplicar.py al terminar.
"""
from __future__ import annotations

import html
import json
import math
import re
import sys
from pathlib import Path

CAMPOS = {
    "cedula": ("Cédula", "El dato más fuerte: dos cédulas iguales casi siempre son la misma persona. "
                         "Una cédula distinta resta, pero con tope, porque a veces está mal tipeada."),
    "nombre1": ("Primer nombre", "Se compara por similitud, para tolerar tipeos como «Valetina» o «Patricoa». "
                                 "Los nombres comunes (María, Ana) suman menos que los raros."),
    "apellido1": ("Primer apellido", "Igual que el nombre, más un nivel para apellidos invertidos entre fuentes "
                                     "(«Techera Acosta» frente a «Acosta Techera»)."),
    "apellido2": ("Segundo apellido", "Suma cuando coincide. Que falte no resta: muchas fuentes no lo registran."),
    "fecha_nacimiento": ("Fecha de nacimiento", "Casi tan fuerte como la cédula. Un dígito mal tipeado sigue sumando, "
                                                "y fechas totalmente distintas restan."),
    "departamento": ("Departamento", "Suma poco y resta poco: una persona puede mudarse o atenderse en otro departamento."),
    "lateralidad": ("Lateralidad (mama derecha o izquierda)", "Pista débil: a veces falta en la fuente o hay tumores en ambas mamas."),
}


def etiqueta(texto: str) -> str:
    t = texto.strip()
    if t.lower().startswith("exact match"):
        return "Coinciden exacto"
    m = re.search(r"Levenshtein distance.*<=\s*(\d+)", t)
    if m:
        n = int(m.group(1))
        return "Difieren en 1 carácter" if n == 1 else f"Difieren en hasta {n} caracteres"
    m = re.search(r"Jaro-Winkler.*>=\s*([\d.]+)", t)
    if m:
        v = float(m.group(1))
        return ("Muy parecidos" if v >= 0.9 else "Parecidos") + f" (similitud ≥ {str(v).replace('.', ',')})"
    m = re.search(r"Abs date difference <=\s*(\d+)\s*(\w+)", t)
    if m:
        n, u = int(m.group(1)), m.group(2).lower()
        unidad = {"day": "día", "month": "mes", "year": "año"}.get(u.rstrip("s"), u)
        plural = {"día": "días", "mes": "meses", "año": "años"}
        return f"Diferencia de hasta {n} {unidad if n == 1 else plural.get(unidad, unidad)}"
    if t.lower().startswith("all other"):
        return "Distintos (ningún caso anterior)"
    return t


def veces(w: float) -> str:
    f = 2 ** abs(w)
    if f < 10:
        s = f"{f:.1f}".replace(".", ",")
    else:
        s = f"{round(f):,}".replace(",", ".")
    return f"{s} veces más probable que sean la misma persona" if w >= 0 else \
           f"{s} veces más probable que sean personas distintas"


def generar(modelo_path: Path, salida: Path, peso_minimo: float | None = None) -> None:
    modelo = json.loads(Path(modelo_path).read_text(encoding="utf-8"))
    p = modelo.get("probability_two_random_records_match", 1e-4)
    prior = math.log2(p / (1 - p))

    filas = []  # (campo, etiqueta, peso, con_tope)
    for comp in modelo["comparisons"]:
        col = comp["output_column_name"]
        for lvl in comp["comparison_levels"]:
            if lvl.get("is_null_level"):
                continue
            m, u = lvl.get("m_probability"), lvl.get("u_probability")
            if not m or not u:
                continue
            w = math.log2(m / u)
            tope = peso_minimo is not None and abs(w - peso_minimo) < 1e-6
            filas.append((col, etiqueta(lvl.get("label_for_charts", "")), w, tope))

    maxabs = max([abs(prior)] + [abs(f[2]) for f in filas])
    escala = 190 / maxabs  # px por unidad

    def barra(w: float) -> str:
        ancho = max(2, abs(w) * escala)
        if w >= 0:
            return f'<div class="bar pos" style="margin-left:200px;width:{ancho:.0f}px"></div>'
        return f'<div class="bar neg" style="margin-left:{200 - ancho:.0f}px;width:{ancho:.0f}px"></div>'

    def fmt(w: float) -> str:
        return f"{w:+.1f}".replace(".", ",").replace("-", "−")

    bloques = []
    bloques.append(f'''
<div class="campo">
  <h3>Punto de partida</h3>
  <p class="exp">Antes de mirar ningún dato: tomados dos registros al azar, lo más probable es que sean personas distintas.</p>
  <div class="fila"><span class="lbl">Dos registros cualesquiera</span><span class="track">{barra(prior)}</span><span class="w">{fmt(prior)}</span><span class="v">{veces(prior)}</span></div>
</div>''')

    for col, (titulo, explicacion) in CAMPOS.items():
        propias = [f for f in filas if f[0] == col]
        if not propias:
            continue
        lineas = []
        for _, lbl, w, tope in propias:
            marca = ' <span class="tope">con tope</span>' if tope else ""
            lineas.append(f'<div class="fila"><span class="lbl">{html.escape(lbl)}{marca}</span>'
                          f'<span class="track">{barra(w)}</span><span class="w">{fmt(w)}</span>'
                          f'<span class="v">{veces(w)}</span></div>')
        bloques.append(f'''
<div class="campo">
  <h3>{html.escape(titulo)}</h3>
  <p class="exp">{html.escape(explicacion)}</p>
  {''.join(lineas)}
</div>''')

    # ejemplo trabajado con los pesos reales
    def peso(col, pref):
        for c, l, w, _ in filas:
            if c == col and l.startswith(pref):
                return w
        return 0.0
    ej = [("Punto de partida", prior),
          ("Cédula: falta en uno de los dos", 0.0),
          ("Primer nombre: «Valentina» y «Valetina» son muy parecidos", peso("nombre1", "Muy parecidos")),
          ("Primer apellido: coinciden exacto", peso("apellido1", "Coinciden")),
          ("Segundo apellido: falta en uno", 0.0),
          ("Fecha de nacimiento: coinciden exacto", peso("fecha_nacimiento", "Coinciden")),
          ("Departamento: coinciden", peso("departamento", "Coinciden"))]
    total = sum(w for _, w in ej)
    prob = 1 / (1 + 2 ** (-total))
    filas_ej = "".join(f"<tr><td>{html.escape(t)}</td><td class='n'>{fmt(w)}</td></tr>" for t, w in ej)

    tope_txt = ""
    if peso_minimo is not None:
        tope_txt = (f"<p>Ninguna diferencia resta más de {fmt(peso_minimo)} (las marcadas «con tope»). "
                    "Así ninguna pista sola alcanza para separar dos registros: un dígito mal tipeado no "
                    "puede vetar una unión si todo lo demás coincide.</p>")

    documento = f'''<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Cómo decide el modelo: peso de cada pista</title>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif; color:#1E2A38; background:#F7F5F0; margin:0; padding:40px; }}
  main {{ max-width: 1100px; margin: 0 auto; }}
  h1 {{ font-size: 30px; margin: 0 0 8px; }}
  h2 {{ font-size: 22px; margin: 36px 0 8px; }}
  h3 {{ font-size: 18px; margin: 0 0 4px; }}
  p {{ line-height: 1.55; color:#3D4756; max-width: 80ch; }}
  .leyenda {{ display:flex; gap:24px; font-size:14px; margin:16px 0 8px; }}
  .leyenda i {{ display:inline-block; width:14px; height:14px; border-radius:3px; vertical-align:-2px; margin-right:6px; }}
  .campo {{ background:#fff; border:1px solid #DDD5C8; border-radius:12px; padding:18px 22px; margin:14px 0; }}
  .exp {{ font-size:14px; margin:0 0 10px; }}
  .fila {{ display:grid; grid-template-columns: 290px 400px 56px 1fr; align-items:center; gap:12px; padding:5px 0; border-top:1px solid #F0EBE3; font-size:14px; }}
  .track {{ position:relative; height:18px; background: linear-gradient(to right, transparent 199px, #9AA3AE 199px, #9AA3AE 201px, transparent 201px); }}
  .bar {{ height:18px; border-radius:3px; }}
  .pos {{ background:#2F7D4F; }} .neg {{ background:#B23E5A; }}
  .w {{ text-align:right; font-variant-numeric: tabular-nums; font-weight:600; }}
  .v {{ color:#5A6574; font-size:13px; }}
  .tope {{ font-size:11px; background:#F3DDE2; color:#7A2238; border-radius:999px; padding:1px 8px; margin-left:6px; white-space:nowrap; }}
  table {{ border-collapse: collapse; margin-top: 8px; background:#fff; }}
  td {{ padding:8px 14px; border-bottom:1px solid #E6E0D6; font-size:15px; }}
  td.n {{ text-align:right; font-variant-numeric: tabular-nums; }}
  tr.total td {{ font-weight:700; border-top:2px solid #1E2A38; }}
  @media (max-width: 1000px) {{ .fila {{ grid-template-columns: 1fr; }} .track {{ width:400px; max-width:100%; }} }}
</style></head>
<body><main>
<h1>Cómo decide el modelo si dos registros son la misma persona</h1>
<p>El modelo funciona como un registrador experto que suma pistas. Compara dos registros campo por campo y, según lo que encuentra
(coinciden, se parecen, son distintos o falta el dato), cada campo <b>suma</b> o <b>resta</b> puntos. Al final suma todo:
si el total es alto, son la misma persona; si es bajo, son personas distintas; si queda en el medio, el par va a revisión humana.</p>
<p>Los puntos no los puso nadie a mano: el modelo los aprendió de los datos, viendo qué tan seguido aparece cada situación entre
registros de la misma persona y entre personas distintas.</p>

<h2>Cómo leer las barras</h2>
<div class="leyenda"><span><i style="background:#2F7D4F"></i>Suma: indica que son la misma persona</span><span><i style="background:#B23E5A"></i>Resta: indica que son personas distintas</span></div>
<p>El número es el peso en escala logarítmica (base 2): cada punto duplica la evidencia. +10 significa que esa situación es unas
1.000 veces más común entre registros de la misma persona que entre personas distintas. Si un dato falta en uno de los dos
registros, ese campo no suma ni resta.</p>
{tope_txt}

<h2>El peso de cada pista</h2>
{''.join(bloques)}

<h2>Un ejemplo</h2>
<p>Dos registros: uno de anatomía patológica sin cédula y otro de historia clínica con el nombre mal tipeado.</p>
<table>{filas_ej}<tr class="total"><td>Total</td><td class="n">{fmt(total)}</td></tr></table>
<p>Con un total de {fmt(total)}, la probabilidad de que sean la misma persona es de
{"más de 99,9" if prob > 0.999 else f"{100 * prob:.1f}".replace(".", ",")} %. {"Se unen automáticamente." if prob >= 0.95 else "Va a revisión humana." if prob >= 0.5 else "Quedan separados."}</p>
</main></body></html>'''
    Path(salida).write_text(documento, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    generar(Path(sys.argv[1]), Path(sys.argv[2]))
