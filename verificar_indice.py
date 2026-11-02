"""Verifica calidad del contenido indexado en ChromaDB."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import chromadb
from collections import defaultdict, Counter

DB_DIR = Path(__file__).parent / "data" / "chromadb"
client = chromadb.PersistentClient(path=str(DB_DIR))
col = client.get_collection("documentos")

total = col.count()
print(f"Total chunks en índice: {total:,}")
print()

# Obtener muestra de 200 chunks
data = col.get(limit=200, include=["documents", "metadatas"])
metas = data["metadatas"]
docs  = data["documents"]

# ── Calidad de metadatos ─────────────────────────────────────────────────────
campos = ["numero_informe", "fecha_emision", "razon_social", "rut", "tipo_ensayo", "unidad"]
contadores = defaultdict(int)
for m in metas:
    for c in campos:
        if m.get(c, "").strip():
            contadores[c] += 1

n = len(metas)
print(f"{'Campo':<20} {'Con valor':>10}  {'%':>6}")
print("-" * 40)
for c in campos:
    pct = contadores[c] / n * 100
    bar = "█" * int(pct // 5)
    print(f"  {c:<18} {contadores[c]:>8}/{n}  {pct:>5.1f}%  {bar}")

# ── Unidades presentes ────────────────────────────────────────────────────────
unidades = Counter(m.get("unidad", "?") for m in metas)
print(f"\nUnidades representadas ({len(unidades)}):")
for u, cnt in sorted(unidades.items()):
    print(f"  {u:<12} {cnt} chunks")

# ── Ejemplos de chunks ───────────────────────────────────────────────────────
print("\n── 3 chunks de ejemplo ──────────────────────────────────────────")
for i in [0, 50, 150]:
    if i >= len(docs):
        break
    m = metas[i]
    texto = docs[i][:200].replace("\n", " ")
    print(f"\n  [{i}] Unidad: {m.get('unidad','')}  |  Informe: {m.get('numero_informe','')}  |  Fecha: {m.get('fecha_emision','')}")
    print(f"       Razón: {m.get('razon_social','')[:60]}")
    print(f"       Tipo:  {m.get('tipo_ensayo','')[:60]}")
    print(f"       Texto: {texto}...")

# ── Chunks sin texto ─────────────────────────────────────────────────────────
vacios = sum(1 for d in docs if len(d.strip()) < 20)
print(f"\nChunks con texto muy corto (<20 chars): {vacios}/{n}")

# ── Longitud promedio de chunk ───────────────────────────────────────────────
avg_len = sum(len(d) for d in docs) / len(docs)
print(f"Longitud promedio de chunk: {avg_len:.0f} chars")
print()
print("=== FIN VERIFICACION ===")
