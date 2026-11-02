"""Interfaz web del sistema de búsqueda semántica."""
import gradio as gr
import sys
import ollama
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.indexador import Indexador
from src.consultor import consultar, verificar_ollama, MODELO_LLM
from src.pipeline import procesar_directorio

indexador = Indexador()
PDF_DIR = Path(__file__).parent / "pdfs"
PDF_DIR.mkdir(exist_ok=True)


def indexar_pdfs(forzar_reindexado):
    try:
        procesar_directorio(str(PDF_DIR), indexador, forzar=forzar_reindexado)
        return f"Indexado completado. Total fragmentos: {indexador.total_chunks()}"
    except Exception as e:
        return f"Error durante indexado: {e}"


def _encabezado(f: dict) -> str:
    partes = []
    if f.get("numero_informe"):
        partes.append(f"Informe N° {f['numero_informe']}")
    if f.get("fecha_emision"):
        partes.append(f"Fecha: {f['fecha_emision']}")
    if f.get("razon_social"):
        partes.append(f.get("razon_social"))
    if f.get("rut"):
        partes.append(f"RUT {f['rut']}")
    if f.get("unidad_dictuc"):
        partes.append(f.get("unidad_dictuc"))
    partes.append(f"Pág. {f['pagina']}  |  Relevancia: {f['similitud']}")
    return "  ·  ".join(partes)


_MIN_CHARS_CONTEXTO = 80


def _texto_util(texto: str) -> bool:
    """Descarta chunks que son artefactos OCR o texto casi vacío."""
    texto = texto.strip()
    if len(texto) < _MIN_CHARS_CONTEXTO:
        return False
    letras = sum(c.isalpha() for c in texto)
    return letras / len(texto) >= 0.4


_PROMPT_CONTEXTO = (
    "Eres un asistente experto en informes técnicos de laboratorio. "
    "A partir del siguiente fragmento de un informe, responde en UNA sola frase corta: "
    "¿qué ensayo o análisis se realizó, sobre qué material/elemento y en qué lugar o proyecto? "
    "No menciones a DICTUC. No copies el texto. Solo la frase.\n\nFragmento:\n"
)


def _contexto_llm(texto: str) -> str:
    if not texto.strip() or len(texto) < 80:
        return ""
    try:
        r = ollama.chat(
            model=MODELO_LLM,
            messages=[{"role": "user", "content": _PROMPT_CONTEXTO + texto[:1200]}],
            options={"temperature": 0}
        )
        return r["message"]["content"].strip()
    except Exception:
        return ""


def buscar_conflictos(descripcion: str, rut: str, empresa: str, obra: str, umbral: float, n_max: int) -> str:
    descripcion = descripcion.strip()
    rut         = rut.strip()
    empresa     = empresa.strip()
    obra        = obra.strip()

    if not descripcion and not rut and not empresa and not obra:
        return "Ingresa al menos una descripción, un RUT, un nombre de empresa o una obra/proyecto."

    # Si no hay consulta semántica, usar empresa/obra como texto de búsqueda (embedding de contexto)
    query       = descripcion or empresa or obra
    solo_filtro = not descripcion  # no aplicar umbral cuando la búsqueda es solo por RUT/empresa/obra

    fragmentos = indexador.buscar(query, n_resultados=50, rut_filter=rut)

    # Post-filtrar por nombre de empresa (coincidencia parcial, sin importar mayúsculas)
    if empresa:
        fragmentos = [
            f for f in fragmentos
            if empresa.lower() in f.get("razon_social", "").lower()
        ]

    # Post-filtrar por obra/proyecto (coincidencia parcial) — permite ver si otra empresa
    # (constructora, solicitante distinto) ya tiene estudios sobre la misma obra.
    if obra:
        fragmentos = [
            f for f in fragmentos
            if obra.lower() in f.get("obra", "").lower()
        ]

    # Descartar chunks con texto basura
    candidatos = [f for f in fragmentos if _texto_util(f.get("texto", ""))]

    # Aplicar umbral de similitud solo cuando hay búsqueda semántica
    if not solo_filtro:
        candidatos = [
            f for f in candidatos
            if isinstance(f["similitud"], float) and f["similitud"] >= umbral
        ]

    # Un resultado por informe: conservar el chunk con mayor similitud
    por_informe: dict[str, dict] = {}
    for f in candidatos:
        clave = f["numero_informe"] or f["fuente"]
        sim_f = f["similitud"] if isinstance(f["similitud"], float) else 0
        sim_actual = por_informe[clave]["similitud"] if clave in por_informe else -1
        sim_actual = sim_actual if isinstance(sim_actual, float) else 0
        if clave not in por_informe or sim_f > sim_actual:
            por_informe[clave] = f

    conflictos = sorted(
        por_informe.values(),
        key=lambda x: x["similitud"] if isinstance(x["similitud"], float) else 0,
        reverse=True
    )[:n_max]

    if not conflictos:
        msg = "No se encontraron estudios previos"
        if not solo_filtro:
            msg += f" con similitud ≥ {umbral:.0%}"
        filtros = [p for p in [f"RUT {rut}" if rut else "", empresa] if p]
        if filtros:
            msg += f" para {' / '.join(filtros)}"
        return msg + "."

    titulo = (
        f"### {len(conflictos)} estudio(s) existentes"
        if solo_filtro else
        f"### {len(conflictos)} estudio(s) previo(s) potencialmente en conflicto"
    )
    partes = [titulo + "\n"]

    for i, f in enumerate(conflictos, 1):
        sim      = f["similitud"]
        sim_txt  = f"{sim:.0%}" if isinstance(sim, float) else "—"
        informe  = f.get("numero_informe") or "—"
        empresa_ = f.get("razon_social")   or "—"
        rut_     = f.get("rut")            or "—"
        fecha    = f.get("fecha_emision")  or "—"
        unidad   = f.get("unidad") or indexador.unidad_desde_fuente(f.get("fuente", "")) or "—"
        unidad_d = f.get("unidad_dictuc") or "—"
        fuente   = f.get("fuente", "")
        ref      = f"Informe N° {informe}" if informe != "—" else f"Archivo: {fuente}"
        sim_col  = f"&nbsp;·&nbsp; Similitud: {sim_txt}" if not solo_filtro else ""

        tipo = f.get("tipo_ensayo", "").strip()
        if not tipo and i <= 3:
            tipo = _contexto_llm(f.get("texto", ""))

        filas = [
            f"| N° Informe | {informe} |",
            f"| Empresa (solicitante) | {empresa_} |",
            f"| RUT | {rut_} |",
            f"| Unidad (carpeta) | {unidad} |",
            f"| Unidad DICTUC | {unidad_d} |",
            f"| Fecha | {fecha} |",
        ]
        # Campos relacionales (constructora/obra/contacto/lugar): solo si el informe los trae
        # — sirven para detectar si otra empresa ya está vinculada al mismo proyecto/contacto.
        if f.get("obra"):
            filas.append(f"| Obra/Proyecto | {f['obra']} |")
        if f.get("constructora"):
            filas.append(f"| Constructora | {f['constructora']} |")
        if f.get("contacto"):
            filas.append(f"| Contacto | {f['contacto']} |")
        if f.get("lugar"):
            filas.append(f"| Lugar | {f['lugar']} |")

        partes.append(
            f"---\n"
            f"**#{i} — {ref}{sim_col}**\n\n"
            f"| Campo | Valor |\n"
            f"|---|---|\n" + "\n".join(filas) + "\n\n"
            f"**Tipo de análisis / ensayo:**\n\n> {tipo or '_(no determinado)_'}\n"
        )

    return "\n".join(partes)


def buscar_y_responder(pregunta, n_fragmentos, usar_llm, filtro_rut):
    if not pregunta.strip() and not filtro_rut.strip():
        return "", ""

    fragmentos = indexador.buscar(pregunta, n_resultados=n_fragmentos, rut_filter=filtro_rut.strip())

    if not fragmentos:
        return "No se encontraron fragmentos relevantes.", ""

    fuentes = "\n".join([f"• {_encabezado(f)}" for f in fragmentos])

    if usar_llm:
        if not verificar_ollama():
            respuesta = "Ollama no está corriendo o el modelo no está descargado.\nEjecuta: ollama run llama3.1"
        else:
            respuesta = consultar(pregunta, fragmentos)
    else:
        partes = []
        for f in fragmentos:
            partes.append(f"**{_encabezado(f)}**\n\n{f['texto']}")
        respuesta = "\n\n---\n\n".join(partes)

    return respuesta, fuentes


with gr.Blocks(title="Búsqueda Semántica de Informes") as app:
    gr.Markdown("# Búsqueda Semántica de Informes PDF")
    gr.Markdown(f"Coloca los archivos `.file` en la carpeta `pdfs/` (subcarpetas incluidas) y luego indexa.")

    with gr.Tab("Buscar"):
        with gr.Row():
            with gr.Column(scale=3):
                pregunta = gr.Textbox(label="Consulta", placeholder="¿Qué quieres saber?", lines=2)
            with gr.Column(scale=1):
                n_frag = gr.Slider(1, 10, value=5, step=1, label="Fragmentos a recuperar")
                usar_llm = gr.Checkbox(label="Usar LLM (requiere Ollama)", value=False)

        filtro_rut = gr.Textbox(label="Filtrar por RUT (opcional)", placeholder="Ej: 12.345.678-9", lines=1)
        btn_buscar = gr.Button("Buscar", variant="primary")
        respuesta_out = gr.Markdown(label="Respuesta")
        fuentes_out = gr.Textbox(label="Fuentes", interactive=False, lines=4)

        btn_buscar.click(
            fn=buscar_y_responder,
            inputs=[pregunta, n_frag, usar_llm, filtro_rut],
            outputs=[respuesta_out, fuentes_out]
        )

    with gr.Tab("Conflictos"):
        gr.Markdown(
            "## Verificar conflictos de interés\n"
            "Usa uno o más criterios de búsqueda. "
            "Puedes combinarlos: por ejemplo, descripción + RUT para ver si esa empresa ya hizo un estudio similar."
        )
        with gr.Row():
            with gr.Column(scale=3):
                desc_conflicto = gr.Textbox(
                    label="Descripción del nuevo estudio / proyecto (opcional)",
                    placeholder="Ej: estudio de suelos para el puente sobre el río Maipo en Melipilla",
                    lines=3
                )
            with gr.Column(scale=1):
                umbral = gr.Slider(0.5, 0.95, value=0.65, step=0.05, label="Umbral de similitud")
                n_conf = gr.Slider(1, 10, value=5, step=1, label="Máx. resultados a mostrar")
        with gr.Row():
            rut_conf = gr.Textbox(
                label="Filtrar por RUT (opcional)",
                placeholder="Ej: 12.345.678-9",
                scale=1
            )
            empresa_conf = gr.Textbox(
                label="Filtrar por nombre de empresa (opcional)",
                placeholder="Ej: DICTUC",
                scale=2
            )
            obra_conf = gr.Textbox(
                label="Filtrar por obra/proyecto (opcional)",
                placeholder="Ej: Edificio Plaza Central",
                scale=2
            )
        btn_conflicto = gr.Button("Verificar conflictos", variant="primary")
        resultado_conflicto = gr.Markdown(label="Resultado")
        btn_conflicto.click(
            fn=buscar_conflictos,
            inputs=[desc_conflicto, rut_conf, empresa_conf, obra_conf, umbral, n_conf],
            outputs=[resultado_conflicto]
        )
        gr.Markdown(
            "> **Nota:** Si la columna *Unidad* aparece como `—`, re-indexa los documentos "
            "desde el tab *Indexar* con la opción *Forzar reindexado* activada."
        )

    with gr.Tab("Indexar"):
        gr.Markdown(f"Archivos `.file` detectados en `pdfs/` y subcarpetas. Los documentos nuevos se indexan automáticamente.")
        forzar = gr.Checkbox(label="Forzar reindexado de todos los documentos", value=False)
        btn_indexar = gr.Button("Iniciar indexado", variant="primary")
        estado = gr.Textbox(label="Estado", interactive=False)
        btn_indexar.click(fn=indexar_pdfs, inputs=[forzar], outputs=[estado])

    with gr.Tab("Estado"):
        btn_estado = gr.Button("Actualizar")
        info = gr.Textbox(interactive=False, lines=5)

        def ver_estado():
            pdfs = list(PDF_DIR.glob("**/*_para_firma.file"))
            return (
                f"PDFs en carpeta: {len(pdfs)}\n"
                f"Fragmentos indexados: {indexador.total_chunks()}\n"
                f"Ollama disponible: {verificar_ollama()}"
            )

        btn_estado.click(fn=ver_estado, outputs=[info])
        app.load(fn=ver_estado, outputs=[info])


if __name__ == "__main__":
    app.launch(server_name="127.0.0.1", server_port=7860, share=False)
