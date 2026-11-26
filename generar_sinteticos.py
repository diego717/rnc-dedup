"""
Generador de datos SINTÉTICOS que imitan cómo llegan los casos al
Registro Nacional de Cáncer (RNC): el mismo caso entra desde varias
fuentes (anatomía patológica, historia clínica, certificado de
defunción, FNR, HCE oncológica) con errores típicos de transcripción.

Ningún dato es real. Los nombres salen de listas comunes en Uruguay y
las cédulas se generan al azar con dígito verificador válido.

Salida:
  datos/registros.csv        -> lo que "recibe" el registro (sin la verdad)
  datos/verdad.csv           -> unique_id -> persona_id (solo para evaluar)
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

NOMBRES_F = [
    "María", "Ana", "Laura", "Carmen", "Silvia", "Graciela", "Patricia", "Marta",
    "Rosa", "Beatriz", "Mónica", "Claudia", "Gabriela", "Lucía", "Valentina",
    "Sofía", "Florencia", "Natalia", "Andrea", "Carolina", "Mariana", "Verónica",
    "Alicia", "Teresa", "Elena", "Susana", "Cristina", "Adriana", "Fernanda",
    "Daniela", "Paola", "Lorena", "Mercedes", "Inés", "Raquel", "Nelly", "Olga",
]
APELLIDOS = [
    "Rodríguez", "González", "Fernández", "Martínez", "Pérez", "García", "López",
    "Sosa", "Silva", "Díaz", "Suárez", "Pereira", "Álvarez", "Cabrera", "Ramos",
    "Núñez", "Castro", "Méndez", "Acosta", "Olivera", "Benítez", "De León",
    "Da Silva", "Techera", "Correa", "Viera", "Moreira", "Píriz", "Machado",
    "Ferreira", "Sánchez", "Romero", "Gómez", "Hernández", "Cardozo", "Morales",
    "Rocha", "Barrios", "Bentancor", "Larrosa", "De Los Santos", "Umpiérrez",
]
DEPARTAMENTOS = {  # peso aproximado por población
    "Montevideo": 39, "Canelones": 15, "Maldonado": 5, "Salto": 4, "Colonia": 3.5,
    "Paysandú": 3.3, "San José": 3.3, "Rivera": 3.1, "Tacuarembó": 2.6,
    "Cerro Largo": 2.5, "Soriano": 2.4, "Artigas": 2.1, "Rocha": 2.0,
    "Florida": 2.0, "Lavalleja": 1.7, "Durazno": 1.7, "Río Negro": 1.6,
    "Treinta y Tres": 1.4, "Flores": 0.7,
}
# Topografía CIE-O-3 mama (C50.x) y morfologías frecuentes
TOPOGRAFIAS = ["C50.0", "C50.1", "C50.2", "C50.3", "C50.4", "C50.5", "C50.8", "C50.9"]
TOPO_PESOS = [2, 8, 10, 4, 40, 6, 10, 20]
MORFOLOGIAS = ["8500/3", "8520/3", "8522/3", "8480/3", "8211/3", "8500/2"]
MORFO_PESOS = [72, 12, 5, 3, 2, 6]

FUENTES = ["AP", "HC", "FNR", "HCEO", "CD"]  # CD = certificado de defunción


def digito_verificador(base: str) -> int:
    """Dígito verificador de cédula uruguaya (7 dígitos de base)."""
    base = base.zfill(7)
    pesos = [2, 9, 8, 7, 6, 3, 4]
    s = sum(int(d) * p for d, p in zip(base, pesos))
    return (10 - s % 10) % 10


def cedula_valida(ci: str) -> bool:
    ci = "".join(c for c in (ci or "") if c.isdigit())
    if len(ci) < 7 or len(ci) > 8:
        return False
    ci = ci.zfill(8)
    return digito_verificador(ci[:7]) == int(ci[7])


def nueva_cedula(rng: random.Random, usadas: set[str]) -> str:
    while True:
        base = str(rng.randint(1_000_000, 6_500_000))
        ci = base + str(digito_verificador(base))
        if ci not in usadas:
            usadas.add(ci)
            return ci


# ---------- errores típicos ----------
VECINAS = dict(zip("qwertyuiopasdfghjklzxcvbnm", "wqrtyuiopoafdgfhjklkzxcvbnm"))
SIN_TILDE = str.maketrans("áéíóúÁÉÍÓÚ", "aeiouAEIOU")


def tipeo(s: str, rng: random.Random) -> str:
    if len(s) < 3:
        return s
    i = rng.randrange(1, len(s))
    op = rng.choice(["sub", "del", "dup", "swap"])
    if op == "sub":
        c = s[i].lower()
        return s[:i] + VECINAS.get(c, c) + s[i + 1:]
    if op == "del":
        return s[:i] + s[i + 1:]
    if op == "dup":
        return s[:i] + s[i] + s[i:]
    if i < len(s) - 1:
        return s[:i] + s[i + 1] + s[i] + s[i + 2:]
    return s


def error_cedula(ci: str, rng: random.Random) -> str:
    i = rng.randrange(len(ci))
    if rng.random() < 0.5 and i < len(ci) - 1:  # transposición
        return ci[:i] + ci[i + 1] + ci[i] + ci[i + 2:]
    return ci[:i] + str(rng.randint(0, 9)) + ci[i + 1:]


def error_fecha(f: str, rng: random.Random) -> str:
    """Cambia un dígito de la fecha (tipeo), manteniendo una fecha válida."""
    for _ in range(20):
        pos = rng.choice([2, 3, 5, 6, 8, 9])  # dígitos de año (decena/unidad), mes y día
        nueva = f[:pos] + str(rng.randint(0, 9)) + f[pos + 1:]
        if nueva == f:
            continue
        try:
            date.fromisoformat(nueva)
            return nueva
        except ValueError:
            continue
    return f


def fmt_fecha(d: date) -> str:
    return d.isoformat()


def corromper(reg: dict, rng: random.Random, nivel: float) -> dict:
    r = dict(reg)
    if rng.random() < 0.25 * nivel:
        r["apellido1"] = r["apellido1"].translate(SIN_TILDE).upper()
        r["nombre1"] = r["nombre1"].translate(SIN_TILDE).upper()
    if rng.random() < 0.12 * nivel:
        r["apellido1"] = tipeo(r["apellido1"], rng)
    if rng.random() < 0.08 * nivel and r["apellido2"]:
        r["apellido2"] = tipeo(r["apellido2"], rng)
    if rng.random() < 0.10 * nivel:
        r["nombre1"] = tipeo(r["nombre1"], rng)
    if rng.random() < 0.30 * nivel:
        r["nombre2"] = ""
    if rng.random() < 0.20 * nivel:
        r["apellido2"] = ""
    if rng.random() < 0.05 * nivel and r["apellido2"]:  # apellidos invertidos
        r["apellido1"], r["apellido2"] = r["apellido2"], r["apellido1"]
    if rng.random() < 0.15 * nivel:
        r["cedula"] = ""
    elif rng.random() < 0.06 * nivel:
        r["cedula"] = error_cedula(r["cedula"], rng)
    if rng.random() < 0.05 * nivel:  # día y mes invertidos
        d = date.fromisoformat(r["fecha_nacimiento"])
        if d.day <= 12:
            r["fecha_nacimiento"] = fmt_fecha(date(d.year, d.day, d.month))
    elif rng.random() < 0.04 * nivel:
        r["fecha_nacimiento"] = error_fecha(r["fecha_nacimiento"], rng)
    elif rng.random() < 0.04 * nivel:
        r["fecha_nacimiento"] = ""
    if rng.random() < 0.06 * nivel:
        r["departamento"] = ""
    elif rng.random() < 0.05 * nivel:  # se mudó, o la fuente registra otro domicilio
        r["departamento"] = rng.choice([d for d in DEPARTAMENTOS if d != r["departamento"]])
    if rng.random() < 0.06 * nivel:
        r["lateralidad"] = ""
    elif rng.random() < 0.02 * nivel:
        r["lateralidad"] = "Izquierda" if r["lateralidad"] == "Derecha" else "Derecha"
    return r


def generar(n_personas: int, semilla: int, nivel: float, outdir: Path) -> None:
    rng = random.Random(semilla)
    usadas: set[str] = set()
    deps, dep_pesos = zip(*DEPARTAMENTOS.items())
    registros, verdad = [], []
    uid = 0

    for pid in range(1, n_personas + 1):
        # edad al diagnóstico: mayoría > 50 años
        edad = int(min(95, max(22, rng.gauss(61, 13))))
        f_diag = date(2021, 1, 1) + timedelta(days=rng.randrange(0, 365 * 2))
        f_nac = f_diag - timedelta(days=edad * 365 + rng.randrange(0, 365))
        persona = {
            "nombre1": rng.choice(NOMBRES_F),
            "nombre2": rng.choice(NOMBRES_F) if rng.random() < 0.6 else "",
            "apellido1": rng.choice(APELLIDOS),
            "apellido2": rng.choice(APELLIDOS),
            "cedula": nueva_cedula(rng, usadas),
            "fecha_nacimiento": fmt_fecha(f_nac),
            "sexo": "F",
            "departamento": rng.choices(deps, dep_pesos)[0],
            "topografia": rng.choices(TOPOGRAFIAS, TOPO_PESOS)[0],
            "morfologia": rng.choices(MORFOLOGIAS, MORFO_PESOS)[0],
            "lateralidad": rng.choice(["Derecha", "Izquierda"]),
        }
        fallece = rng.random() < 0.18

        # ¿desde qué fuentes llega este caso?
        fuentes = []
        if rng.random() < 0.92:  # verificación microscópica
            fuentes.append("AP")
        if rng.random() < 0.70:
            fuentes.append("HC")
        if rng.random() < 0.35:
            fuentes.append("FNR")
        if rng.random() < 0.55:
            fuentes.append("HCEO")
        if fallece:
            fuentes.append("CD")
        if not fuentes:
            fuentes.append("CD")  # caso conocido solo por defunción (DCO)
        # a veces la misma fuente lo reporta dos veces (p.ej., 2 biopsias)
        if "AP" in fuentes and rng.random() < 0.15:
            fuentes.append("AP")

        for fuente in fuentes:
            reg = corromper(persona, rng, nivel)
            # la fecha de incidencia difiere según la fuente
            delta = {"AP": 0, "HC": rng.randint(-20, 40), "FNR": rng.randint(15, 120),
                     "HCEO": rng.randint(0, 60), "CD": rng.randint(90, 700)}[fuente]
            reg["fecha_evento"] = fmt_fecha(f_diag + timedelta(days=delta))
            if fuente == "CD":
                reg["morfologia"] = ""  # el certificado no trae histología
                reg["topografia"] = "C50.9"
                reg["lateralidad"] = ""
            if fuente in ("FNR",) and rng.random() < 0.5:
                reg["lateralidad"] = ""
            uid += 1
            reg = {"unique_id": uid, "fuente": fuente, **reg}
            registros.append(reg)
            verdad.append({"unique_id": uid, "persona_id": pid})

    rng.shuffle(registros)
    outdir.mkdir(parents=True, exist_ok=True)
    cols = ["unique_id", "fuente", "nombre1", "nombre2", "apellido1", "apellido2",
            "cedula", "fecha_nacimiento", "sexo", "departamento", "fecha_evento",
            "topografia", "morfologia", "lateralidad"]
    with open(outdir / "registros.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(registros)
    with open(outdir / "verdad.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["unique_id", "persona_id"])
        w.writeheader()
        w.writerows(verdad)
    print(f"{n_personas} personas sintéticas -> {len(registros)} registros en {outdir}/")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--personas", type=int, default=4000)
    ap.add_argument("--semilla", type=int, default=42)
    ap.add_argument("--nivel-error", type=float, default=1.0,
                    help="multiplicador de la tasa de errores (1.0 = realista, 2.0 = muy sucio)")
    ap.add_argument("--salida", type=Path, default=Path("datos"))
    a = ap.parse_args()
    generar(a.personas, a.semilla, a.nivel_error, a.salida)
