"""Orquesta el procesamiento de PDFs e indexado."""
from pathlib import Path
from tqdm import tqdm
from .extractor import extraer_pdf, paginas_a_chunks
from .indexador import Indexador


def procesar_directorio(directorio: str, indexador: Indexador, forzar: bool = False):
    pdfs = list(Path(directorio).glob("**/*_para_firma.file"))
    if not pdfs:
        print(f"No se encontraron PDFs en: {directorio}")
        return

    nuevos = [p for p in pdfs if forzar or not indexador.ya_indexado(str(p))]
    print(f"PDFs encontrados: {len(pdfs)} | Por indexar: {len(nuevos)}")

    for pdf in tqdm(nuevos, desc="Indexando PDFs"):
        try:
            print(f"\nProcesando: {pdf.name}")
            doc = extraer_pdf(str(pdf))
            chunks = paginas_a_chunks(doc)
            indexador.indexar_chunks(chunks, str(pdf))
            print(f"  -> {len(chunks)} fragmentos indexados")
        except Exception as e:
            print(f"  ERROR en {pdf.name}: {e}")

    print(f"\nTotal fragmentos en índice: {indexador.total_chunks()}")
