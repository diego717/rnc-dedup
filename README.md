# rnc-dedup — prototipo de deduplicación de casos de cáncer

Prototipo **con datos 100 % sintéticos** para mostrar cómo un motor de
*record linkage* probabilístico puede ayudar a un registro poblacional de
cáncer (como el Registro Nacional de Cáncer de la CHLCC) a consolidar los
casos que llegan repetidos desde varias fuentes.

> Ningún dato de este repositorio es real. Los registros se generan al azar
> con nombres comunes y cédulas con dígito verificador válido. El objetivo es
> que el registro pueda correr la herramienta **en su propia infraestructura**,
> sin que nadie de afuera vea datos de pacientes.

## El problema

El RNC describe que cada caso entra por varias fuentes (anatomía patológica,
historias clínicas, certificados de defunción, FNR, HCE oncológica) y que
evitar casos repetidos exige procesos complejos que, junto con la depuración
final, llevan meses. Entre fuentes cambian tildes y mayúsculas, faltan
cédulas, hay tipeos, apellidos invertidos, día y mes invertidos, etc.

## Qué hace

| Script | Función |
|---|---|
| `generar_sinteticos.py` | Crea personas ficticias con cáncer de mama (CIE-O-3 C50.x) y las "reporta" desde 1 a 5 fuentes con errores realistas. Guarda la verdad aparte, solo para evaluar. |
| `deduplicar.py` | Normaliza los datos, entrena un modelo Fellegi-Sunter con [Splink](https://github.com/moj-analytical-services/splink) **sin etiquetas**, agrupa los registros en casos, arma una cola de revisión humana y calcula indicadores de calidad (verificación microscópica, solo certificado de defunción, cédulas inválidas, campos faltantes). |

Salidas en `resultados/`:

- `registros_con_caso.csv`: cada registro con su `cluster_id` (caso consolidado).
- `cola_revision.csv`: pares dudosos (probabilidad entre 0,5 y 0,95), lado a lado, para que una persona decida.
- `resumen.json`: indicadores de calidad y, si hay verdad, la evaluación.
- `modelo.json` y `pesos_modelo.html`: el modelo entrenado y un gráfico de cuánto pesa cada campo. El modelo es explicable: cada decisión se puede auditar.

## Resultados con datos sintéticos (4.000 personas)

| Escenario | Método | Precisión | Exhaustividad | Error en el conteo de casos |
|---|---|---|---|---|
| Errores realistas (11.341 registros) | Solo cédula exacta | 1,000 | 0,640 | +53,6 % |
| | Splink automático | 1,000 | 0,975 | +3,8 % |
| | Splink + revisión de 81 pares | 1,000 | 0,978 | +3,3 % |
| Errores al doble | Solo cédula exacta | 1,000 | 0,402 | +94,3 % |
| | Splink automático | 1,000 | 0,912 | +13,3 % |

"Error en el conteo" = cuánto se sobreestimaría la incidencia si no se
deduplicara bien. Un registro que une solo por cédula contaría la mitad de
los casos dos veces.

**Ojo:** los datos sintéticos son más fáciles que los reales (los errores
siguen reglas conocidas y no hay homónimos con la misma fecha de nacimiento,
mellizas, etc.). La precisión perfecta seguramente no se mantenga con datos
reales; el valor final solo se puede medir dentro del registro.

## Cómo correrlo

```bash
pip install -r requirements.txt
python generar_sinteticos.py                # --personas 4000 --nivel-error 1.0
python deduplicar.py                        # --umbral 0.95 --umbral-revision 0.5
```

Corre en segundos en una notebook (DuckDB embebido, sin servidor).

## Próximos pasos posibles

1. Interfaz web simple para trabajar la cola de revisión (aceptar / rechazar par).
2. Reglas de tumores primarios múltiples (IARC/IACR): dos C50 en la misma
   persona pueden ser un solo caso o dos (mama bilateral).
3. Formulario electrónico del protocolo de captura con validación en origen
   (dígito verificador de cédula, códigos CIE-O-3, fechas coherentes).
4. Linkage con identificadores cifrados (filtros de Bloom) si alguna fuente no
   puede compartir nombres en claro.
5. Validación con datos reales, **dentro del RNC y con su autorización**.

## Licencia

MIT. Se puede usar, adaptar y correr libremente.
