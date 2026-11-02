"""Gestiona el índice vectorial y la búsqueda híbrida."""
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
from rank_bm25 import BM25Okapi
from pathlib import Path
import json
import hashlib
import torch


MODELO_EMBEDDINGS = "intfloat/multilingual-e5-large"
DB_DIR = Path(__file__).parent.parent / "data" / "chromadb"
HASHES_FILE = Path(__file__).parent.parent / "data" / "hashes.json"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
BATCH_SIZE = 128 if DEVICE == "cuda" else 32


class Indexador:
    def __init__(self):
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self.cliente = chromadb.PersistentClient(path=str(DB_DIR))
        self.coleccion = self.cliente.get_or_create_collection(
            name="documentos",
            metadata={"hnsw:space": "cosine"}
        )
        print(f"Cargando modelo de embeddings: {MODELO_EMBEDDINGS} (device={DEVICE})")
        self.modelo = SentenceTransformer(MODELO_EMBEDDINGS, device=DEVICE)
        self._cargar_hashes()

    def _cargar_hashes(self):
        if HASHES_FILE.exists():
            self._hashes = json.loads(HASHES_FILE.read_text(encoding="utf-8"))
        else:
            self._hashes = {}

    def _guardar_hashes(self):
        HASHES_FILE.parent.mkdir(parents=True, exist_ok=True)
        HASHES_FILE.write_text(json.dumps(self._hashes, ensure_ascii=False, indent=2), encoding="utf-8")

    def ya_indexado(self, ruta_pdf: str) -> bool:
        hash_actual = self._hash_archivo(ruta_pdf)
        return self._hashes.get(ruta_pdf) == hash_actual

    def _hash_archivo(self, ruta: str) -> str:
        return hashlib.md5(Path(ruta).read_bytes()).hexdigest()

    def indexar_chunks(self, chunks: list[dict], ruta_pdf: str):
        if not chunks:
            return

        textos = [c["texto"] for c in chunks]
        embeddings = self.modelo.encode(
            [f"passage: {t}" for t in textos],
            batch_size=BATCH_SIZE,
            show_progress_bar=True,
            normalize_embeddings=True
        ).tolist()

        self.coleccion.upsert(
            ids=[c["id"] for c in chunks],
            embeddings=embeddings,
            documents=textos,
            metadatas=[{
                "fuente":          c["fuente"],
                "pagina":          c["pagina"],
                "numero_informe":  c.get("numero_informe", ""),
                "fecha_emision":   c.get("fecha_emision", ""),
                "razon_social":    c.get("razon_social", ""),
                "rut":             c.get("rut", ""),
                "tipo_ensayo":     c.get("tipo_ensayo", ""),
                "unidad":          c.get("unidad", ""),
                "unidad_dictuc":   c.get("unidad_dictuc", ""),
                "obra":            c.get("obra", ""),
                "constructora":    c.get("constructora", ""),
                "contacto":        c.get("contacto", ""),
                "lugar":           c.get("lugar", ""),
            } for c in chunks]
        )

        self._hashes[ruta_pdf] = self._hash_archivo(ruta_pdf)
        self._guardar_hashes()

    def buscar(self, consulta: str, n_resultados: int = 5, rut_filter: str = "") -> list[dict]:
        where = {"rut": {"$eq": rut_filter}} if rut_filter else None

        # Sin consulta semántica: recuperar directamente por metadato
        if not consulta.strip():
            if not where:
                return []
            datos = self.coleccion.get(
                where=where,
                limit=n_resultados,
                include=["documents", "metadatas"]
            )
            return [
                {
                    "texto":          datos["documents"][i],
                    "fuente":         datos["metadatas"][i].get("fuente", ""),
                    "pagina":         datos["metadatas"][i].get("pagina", ""),
                    "numero_informe": datos["metadatas"][i].get("numero_informe", ""),
                    "fecha_emision":  datos["metadatas"][i].get("fecha_emision", ""),
                    "razon_social":   datos["metadatas"][i].get("razon_social", ""),
                    "rut":            datos["metadatas"][i].get("rut", ""),
                    "tipo_ensayo":    datos["metadatas"][i].get("tipo_ensayo", ""),
                    "unidad":         datos["metadatas"][i].get("unidad", ""),
                    "unidad_dictuc":  datos["metadatas"][i].get("unidad_dictuc", ""),
                    "obra":           datos["metadatas"][i].get("obra", ""),
                    "constructora":   datos["metadatas"][i].get("constructora", ""),
                    "contacto":       datos["metadatas"][i].get("contacto", ""),
                    "lugar":          datos["metadatas"][i].get("lugar", ""),
                    "similitud":      "-",
                }
                for i in range(len(datos["ids"]))
            ]

        embedding_consulta = self.modelo.encode(
            f"query: {consulta}",
            normalize_embeddings=True
        ).tolist()

        kwargs = {
            "query_embeddings": [embedding_consulta],
            "n_results":        n_resultados,
            "include":          ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        try:
            resultados = self.coleccion.query(**kwargs)
        except Exception:
            return []

        salida = []
        for i in range(len(resultados["ids"][0])):
            meta = resultados["metadatas"][0][i]
            salida.append({
                "texto":           resultados["documents"][0][i],
                "fuente":          meta.get("fuente", ""),
                "pagina":          meta.get("pagina", ""),
                "numero_informe":  meta.get("numero_informe", ""),
                "fecha_emision":   meta.get("fecha_emision", ""),
                "razon_social":    meta.get("razon_social", ""),
                "rut":             meta.get("rut", ""),
                "tipo_ensayo":     meta.get("tipo_ensayo", ""),
                "unidad":          meta.get("unidad", ""),
                "unidad_dictuc":   meta.get("unidad_dictuc", ""),
                "obra":            meta.get("obra", ""),
                "constructora":    meta.get("constructora", ""),
                "contacto":        meta.get("contacto", ""),
                "lugar":           meta.get("lugar", ""),
                "similitud":       round(1 - resultados["distances"][0][i], 3)
            })

        return salida

    def unidad_desde_fuente(self, nombre_archivo: str) -> str:
        """Recupera la unidad (u_XX) buscando el archivo en hashes.json por su nombre."""
        import re
        patron = re.compile(r'[/\\]u_(\d+)[/\\]')
        for ruta in self._hashes:
            if ruta.replace("\\", "/").endswith("/" + nombre_archivo):
                m = patron.search(ruta)
                if m:
                    return f"u_{m.group(1)}"
        return ""

    def total_chunks(self) -> int:
        return self.coleccion.count()
