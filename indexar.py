"""Indexación completa de todos los PDFs _para_firma.file."""
import sys
import time
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))

from src.indexador import Indexador
from src.extractor import extraer_pdf, paginas_a_chunks
from tqdm import tqdm

PDF_DIR = Path(__file__).parent / "pdfs"

print(f"Cargando modelo y base de datos...")
indexador = Indexador()

# Suprimir barra de progreso por archivo (muy ruidosa con miles de archivos)
_encode_orig = indexador.modelo.encode
def _encode_silent(*args, **kwargs):
    kwargs["show_progress_bar"] = False
    return _encode_orig(*args, **kwargs)
indexador.modelo.encode = _encode_silent

pdfs = sorted(PDF_DIR.rglob("*_para_firma.file"))
nuevos = [p for p in pdfs if not indexador.ya_indexado(str(p))]

print(f"\nPDFs encontrados : {len(pdfs)}")
print(f"Por indexar      : {len(nuevos)}")
print(f"Ya indexados     : {len(pdfs) - len(nuevos)}")

if not nuevos:
    print("\nNada nuevo que indexar.")
    sys.exit(0)

errores = []
t0 = time.time()
chunks_totales = 0

for pdf in tqdm(nuevos, desc="Indexando", unit="pdf"):
    try:
        doc = extraer_pdf(str(pdf))
        chunks = paginas_a_chunks(doc)
        if chunks:
            indexador.indexar_chunks(chunks, str(pdf))
            chunks_totales += len(chunks)
    except Exception as e:
        errores.append((pdf.name, str(e)))

elapsed = time.time() - t0
mins, secs = divmod(int(elapsed), 60)

print(f"\n{'='*60}")
print(f"  INDEXACIÓN COMPLETADA")
print(f"{'='*60}")
print(f"  Tiempo         : {mins}m {secs}s")
print(f"  Archivos       : {len(nuevos) - len(errores)} OK  |  {len(errores)} errores")
print(f"  Chunks nuevos  : {chunks_totales:,}")
print(f"  Total en índice: {indexador.total_chunks():,}")

if errores:
    print(f"\n  Errores ({len(errores)}):")
    for nombre, err in errores[:20]:
        print(f"    {nombre}: {err[:100]}")
    if len(errores) > 20:
        print(f"    ... y {len(errores) - 20} más")
