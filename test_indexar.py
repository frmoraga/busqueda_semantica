"""
Prueba de carga: indexa 30 archivos aleatorios por unidad usando GPU.
Escribe salida en test_indexar_log.txt para monitoreo sin bloquear.
"""
import sys
import random
import time
from pathlib import Path
from collections import defaultdict

LOG = Path(__file__).parent / "test_indexar_log.txt"

def log(msg=""):
    txt = str(msg)
    print(txt, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(txt + "\n")

LOG.write_text("", encoding="utf-8")  # limpiar log anterior

sys.path.insert(0, str(Path(__file__).parent))

from src.indexador import Indexador, DEVICE
from src.extractor import extraer_pdf, paginas_a_chunks

PDF_DIR = Path(__file__).parent / "pdfs"
MUESTRA  = 100
SEED     = 42

random.seed(SEED)

# ── Agrupar por unidad ──────────────────────────────────────────────────────
unidades: dict[str, list[Path]] = defaultdict(list)
for f in PDF_DIR.rglob("*_para_firma.file"):
    partes = f.parts
    idx = next((i for i, p in enumerate(partes) if p.startswith("u_")), None)
    if idx is not None:
        unidades[partes[idx]].append(f)

if not unidades:
    log(f"No se encontraron archivos en {PDF_DIR}")
    sys.exit(1)

# ── Muestra ─────────────────────────────────────────────────────────────────
muestra_total: list[Path] = []
for unidad in sorted(unidades):
    archivos = unidades[unidad]
    sel = random.sample(archivos, min(MUESTRA, len(archivos)))
    muestra_total.extend(sel)

log(f"GPU device     : {DEVICE}")
log(f"Unidades       : {len(unidades)}")
log(f"Archivos/unidad: {MUESTRA}")
log(f"Total a indexar: {len(muestra_total)}")
log()

# ── Cargar indexador (carga modelo en GPU) ──────────────────────────────────
log("Cargando modelo de embeddings en GPU...")
indexador = Indexador()

# Suprimir barra de progreso de sentence-transformers por archivo
_enc_orig = indexador.modelo.encode
def _enc_silent(*a, **kw):
    kw["show_progress_bar"] = False
    return _enc_orig(*a, **kw)
indexador.modelo.encode = _enc_silent

log()

# ── Procesar ─────────────────────────────────────────────────────────────────
errores = []
t_global = time.time()
chunks_totales = 0
tiempos_extraccion = []
tiempos_embedding  = []

for i, pdf in enumerate(muestra_total, 1):
    try:
        t0 = time.time()
        doc = extraer_pdf(str(pdf))
        chunks = paginas_a_chunks(doc)
        t_ext = time.time() - t0
        tiempos_extraccion.append(t_ext)

        if chunks:
            t1 = time.time()
            indexador.indexar_chunks(chunks, str(pdf))
            t_emb = time.time() - t1
            tiempos_embedding.append(t_emb)
            chunks_totales += len(chunks)
        else:
            tiempos_embedding.append(0)

        if i % 20 == 0 or i == len(muestra_total):
            elapsed = time.time() - t_global
            vel = i / elapsed
            eta = (len(muestra_total) - i) / vel if vel > 0 else 0
            log(f"  [{i:>4}/{len(muestra_total)}]  "
                f"{vel:.1f} arch/s  |  ETA {eta/60:.1f} min  |  "
                f"chunks: {chunks_totales:,}")
    except Exception as e:
        errores.append((pdf.name, str(e)))
        log(f"  ERROR {pdf.name}: {str(e)[:100]}")

# ── Resumen ──────────────────────────────────────────────────────────────────
elapsed_total = time.time() - t_global
ok = len(muestra_total) - len(errores)

def p(vals):
    if not vals:
        return "-"
    return f"{sum(vals)/len(vals)*1000:.0f}ms"

log(f"\n{'='*60}")
log(f"  RESUMEN DE PRUEBA DE CARGA")
log(f"{'='*60}")
log(f"  GPU                 : {DEVICE.upper()}")
log(f"  Archivos procesados : {ok}/{len(muestra_total)}  ({len(errores)} errores)")
log(f"  Tiempo total        : {elapsed_total/60:.1f} min  ({elapsed_total:.0f}s)")
log(f"  Velocidad media     : {ok/elapsed_total:.2f} archivos/s")
log(f"  Extraccion promedio : {p(tiempos_extraccion)} por archivo")
log(f"  Embedding promedio  : {p(tiempos_embedding)} por archivo")
log(f"  Chunks generados    : {chunks_totales:,}")
log(f"  Total en indice     : {indexador.total_chunks():,}")

if errores:
    log(f"\n  Errores ({len(errores)}):")
    for nombre, err in errores[:10]:
        log(f"    {nombre}: {err[:100]}")

total_archivos = sum(len(v) for v in unidades.values())
vel = ok / elapsed_total if elapsed_total > 0 else 1
log(f"\n  Extrapol. al total ({total_archivos} archivos):")
log(f"    Tiempo estimado: ~{total_archivos/vel/60:.0f} minutos")
log()
log("=== FIN ===")
