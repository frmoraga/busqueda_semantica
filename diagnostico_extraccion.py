"""
Diagnóstico de extracción PDF — versión rápida.
- Sin fallback LLM (mide solo regex puro)
- Paralelo: ThreadPoolExecutor con N workers
- 20 archivos aleatorios por unidad
"""
import sys
import random
import json
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Forzar UTF-8 en la consola Windows
if sys.stdout.encoding != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

# Parchamos _extraer_metadata para que NUNCA llame al LLM
import src.extractor as _ext_mod
_ext_mod._extraer_metadata = _ext_mod._extraer_metadata_regex

from src.extractor import extraer_pdf

PDF_DIR = Path(__file__).parent / "pdfs"
MUESTRA  = 100
WORKERS  = 8   # hilos paralelos
CAMPOS   = ["numero_informe", "fecha_emision", "razon_social", "rut", "tipo_ensayo"]

VERDE = "\033[92m"; ROJO = "\033[91m"; AMAR = "\033[93m"
RESET = "\033[0m";  GRIS = "\033[90m"


def evaluar_campo(nombre, valor):
    if not valor:
        return False, "vacío"
    if nombre == "numero_informe" and (not valor.isdigit() or len(valor) < 6):
        return False, f"inválido: '{valor}'"
    if nombre == "fecha_emision":
        parts = valor.replace("/", "-").split("-")
        if len(parts) != 3:
            return False, f"inválido: '{valor}'"
    if nombre == "rut" and "-" not in valor:
        return False, f"inválido: '{valor}'"
    return True, ""


def procesar_archivo(ruta: Path) -> dict:
    try:
        doc = extraer_pdf(str(ruta))
        meta = doc.get("metadata", {})
        texto_p1 = doc["paginas"][0]["texto"] if doc.get("paginas") else ""
        campos = {}
        for campo in CAMPOS:
            ok, warn = evaluar_campo(campo, meta.get(campo, ""))
            campos[campo] = {"valor": meta.get(campo, ""), "ok": ok, "warn": warn}
        return {
            "archivo": ruta.name,
            "paginas": len(doc.get("paginas", [])),
            "campos": campos,
            "texto_p1": texto_p1[:400].replace("\n", " ") if texto_p1 else "[sin texto]"
        }
    except Exception as e:
        return {"archivo": ruta.name, "error": str(e), "paginas": 0, "campos": {}, "texto_p1": ""}


def diagnosticar_unidad(unidad: str, archivos: list) -> dict:
    muestra = random.sample(archivos, min(MUESTRA, len(archivos)))
    resultados = []
    contadores = defaultdict(int)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futuros = {pool.submit(procesar_archivo, r): r for r in muestra}
        for fut in as_completed(futuros):
            r = fut.result()
            resultados.append(r)
            if "error" not in r:
                for campo in CAMPOS:
                    if r["campos"].get(campo, {}).get("ok"):
                        contadores[campo] += 1

    total = len(muestra)
    tasa = {c: contadores[c] / total for c in CAMPOS}
    return {"unidad": unidad, "total": total, "tasa": tasa, "resultados": resultados}


def imprimir_reporte(inf: dict):
    u, t = inf["unidad"], inf["total"]
    print(f"\n{'='*70}")
    print(f"  UNIDAD {u.upper()}  —  {t} archivos")
    print(f"{'='*70}")

    print(f"\n  {'Campo':<22} {'Éxito':>7}  Barra")
    print(f"  {'-'*52}")
    for campo, tasa in inf["tasa"].items():
        pct = int(tasa * 100)
        barra = "█" * (pct // 5) + "░" * (20 - pct // 5)
        c = VERDE if pct >= 80 else (AMAR if pct >= 50 else ROJO)
        print(f"  {campo:<22} {c}{pct:>5}%{RESET}  {c}{barra}{RESET}")

    fallos = [r for r in inf["resultados"]
              if "error" not in r and not all(r["campos"].get(c, {}).get("ok") for c in CAMPOS)]
    if fallos:
        print(f"\n  {AMAR}Ejemplos con campos faltantes:{RESET}")
        for r in fallos[:3]:
            print(f"\n    {GRIS}Archivo:{RESET} {r['archivo']}")
            for campo in CAMPOS:
                est = r["campos"].get(campo, {})
                ok, val, warn = est.get("ok", False), est.get("valor", ""), est.get("warn", "")
                ico = f"{VERDE}✓{RESET}" if ok else f"{ROJO}✗{RESET}"
                print(f"      {ico} {campo:<22} {val or ROJO+warn+RESET}")
            print(f"    {GRIS}Texto p1:{RESET} {r['texto_p1'][:250]}")

    perfectos = [r for r in inf["resultados"]
                 if "error" not in r and all(r["campos"].get(c, {}).get("ok") for c in CAMPOS)]
    if perfectos:
        e = perfectos[0]
        print(f"\n  {VERDE}Ejemplo OK:{RESET} {e['archivo']}")
        for campo in CAMPOS:
            print(f"      {VERDE}✓{RESET} {campo:<22} {e['campos'][campo]['valor']}")

    errores = [r for r in inf["resultados"] if "error" in r]
    if errores:
        print(f"\n  {ROJO}Errores de lectura ({len(errores)}):{RESET}")
        for r in errores[:2]:
            print(f"    - {r['archivo']}: {r['error'][:120]}")


def main():
    random.seed(42)

    unidades = {}
    for f in PDF_DIR.rglob("*_para_firma.file"):
        partes = f.parts
        idx = next((i for i, p in enumerate(partes) if p.startswith("u_")), None)
        if idx is not None:
            unidades.setdefault(partes[idx], []).append(f)

    if not unidades:
        print(f"{ROJO}No se encontraron archivos .file en {PDF_DIR}{RESET}")
        return

    print(f"\nUnidades: {sorted(unidades.keys())}")
    print(f"Procesando {MUESTRA} archivos/unidad con {WORKERS} hilos paralelos (sin LLM)\n")

    todos = []
    for unidad in sorted(unidades.keys()):
        archivos = unidades[unidad]
        print(f"  {unidad} ({len(archivos)} archivos)...", end="", flush=True)
        inf = diagnosticar_unidad(unidad, archivos)
        todos.append(inf)
        prom = sum(inf["tasa"].values()) / len(CAMPOS)
        c = VERDE if prom >= 0.8 else (AMAR if prom >= 0.5 else ROJO)
        print(f" {c}{int(prom*100)}%{RESET}")

    for inf in todos:
        imprimir_reporte(inf)

    print(f"\n\n{'='*70}")
    print("  RESUMEN GLOBAL")
    print(f"{'='*70}")
    print(f"\n  {'Unidad':<10} {'N°Inf':>6} {'Fecha':>6} {'Razón':>6} {'RUT':>6} {'Tipo':>6}  {'Prom':>6}")
    print(f"  {'-'*58}")
    for inf in todos:
        t = inf["tasa"]
        prom = sum(t.values()) / len(CAMPOS)
        c = VERDE if prom >= 0.8 else (AMAR if prom >= 0.5 else ROJO)
        print(f"  {inf['unidad']:<10} "
              f"{int(t['numero_informe']*100):>5}% "
              f"{int(t['fecha_emision']*100):>5}% "
              f"{int(t['razon_social']*100):>5}% "
              f"{int(t['rut']*100):>5}% "
              f"{int(t['tipo_ensayo']*100):>5}%  "
              f"{c}{int(prom*100):>5}%{RESET}")

    out = Path(__file__).parent / "diagnostico_resultado.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(todos, f, ensure_ascii=False, indent=2)
    print(f"\n  JSON guardado en: {out}\n")


if __name__ == "__main__":
    main()
