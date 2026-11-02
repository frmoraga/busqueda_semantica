"""Responde preguntas usando RAG con Ollama."""
import ollama


MODELO_LLM = "llama3.1"

PROMPT_SISTEMA = """Eres un asistente experto en análisis de informes técnicos de laboratorio.
Tu tarea es responder preguntas basándote ÚNICAMENTE en los fragmentos de documentos proporcionados.

Instrucciones:
- Sintetiza la información relevante de los fragmentos; NO los copies textualmente.
- Menciona siempre los informes de origen (número de informe y fecha) cuando sea pertinente.
- Si varios informes tienen información relacionada, compáralos o agrúpalos.
- Si la información no está en los fragmentos, indícalo claramente.
- Sé claro, estructurado y conciso. Usa listas o secciones cuando ayude a la claridad.
- Responde siempre en español."""


def construir_contexto(fragmentos: list[dict]) -> str:
    partes = []
    for f in fragmentos:
        encabezado_parts = [f"Documento: {f['fuente']}", f"Pág. {f['pagina']}"]
        if f.get("numero_informe"):
            encabezado_parts.append(f"Informe N° {f['numero_informe']}")
        if f.get("fecha_emision"):
            encabezado_parts.append(f"Fecha: {f['fecha_emision']}")
        if f.get("razon_social"):
            encabezado_parts.append(f"Empresa: {f['razon_social']}")
        encabezado = " | ".join(encabezado_parts)
        partes.append(f"[{encabezado}]\n{f['texto']}")
    return "\n\n---\n\n".join(partes)


def consultar(pregunta: str, fragmentos: list[dict]) -> str:
    contexto = construir_contexto(fragmentos)

    prompt = f"""A continuación se presentan fragmentos relevantes de informes técnicos:

{contexto}

---

Pregunta: {pregunta}

Proporciona una respuesta elaborada y estructurada basándote únicamente en los fragmentos anteriores. Cita los informes relevantes cuando corresponda."""

    respuesta = ollama.chat(
        model=MODELO_LLM,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": prompt}
        ]
    )

    return respuesta["message"]["content"]


_PROMPT_METADATA = """Extrae del siguiente texto de un informe técnico los campos indicados.
La empresa que haces el informe es siempre DICTUC S.A. — el campo razon_social debe ser el CLIENTE (quien solicitó el estudio), nunca DICTUC.

Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional:
{
  "numero_informe": "número del informe o certificado (solo dígitos, sin espacios)",
  "fecha_emision": "fecha en formato DD-MM-YYYY",
  "razon_social": "nombre de la empresa cliente",
  "rut": "RUT del cliente en formato XX.XXX.XXX-X"
}

Si un campo no se puede determinar, usa cadena vacía "".

Texto:
"""


def extraer_metadata_llm(texto_pagina1: str) -> dict:
    """Extrae metadatos usando el LLM local. Fallback: devuelve dict vacío."""
    try:
        respuesta = ollama.chat(
            model=MODELO_LLM,
            messages=[{"role": "user", "content": _PROMPT_METADATA + texto_pagina1[:1500]}],
            options={"temperature": 0}
        )
        contenido = respuesta["message"]["content"].strip()
        # Extraer el JSON aunque venga con texto alrededor
        import re, json
        m = re.search(r'\{.*\}', contenido, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {}


def verificar_ollama() -> bool:
    try:
        modelos = ollama.list()
        nombres = [
            m.model if hasattr(m, "model") else m.get("name", "")
            for m in (modelos.models if hasattr(modelos, "models") else modelos.get("models", []))
        ]
        return any(MODELO_LLM in n for n in nombres)
    except Exception:
        return False
