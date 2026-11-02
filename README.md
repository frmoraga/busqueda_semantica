# Búsqueda semántica de informes técnicos

*Local semantic search (RAG) over ~4,400 confidential lab reports in Spanish. Python, ChromaDB, multilingual-e5, Ollama, Gradio.*

En el laboratorio donde trabajo hay miles de informes de ensayo en PDF repartidos en carpetas por área. Preguntas como "¿ya hicimos un estudio parecido?" o "¿ya trabajamos para esta empresa, o en esta obra?" dependían de que alguien se acordara, o de abrir PDFs uno por uno. Esto es un buscador que entiende el contenido de los informes y responde esas preguntas en segundos.

La restricción principal es que los informes son de clientes y son confidenciales, así que nada puede salir del computador. No hay OpenAI ni ninguna API externa: la extracción, los embeddings, la base vectorial y el LLM corren en local.

**Stack:** Python 3.11 · pdfplumber · PyMuPDF · Tesseract · sentence-transformers (`multilingual-e5-large`) · ChromaDB · Ollama (llama3.1) · Gradio

## Qué hace

- Busca por significado y no por palabra exacta. "Resistencia a compresión de probetas de hormigón" encuentra informes que dicen "ensayo de rotura de cilindros".
- Filtra por RUT del cliente. También se puede buscar solo por RUT para ver todo lo que se ha hecho para una empresa.
- Opcionalmente le pasa los fragmentos encontrados a llama3.1, que arma una respuesta citando número de informe y fecha.
- Tiene una pestaña para revisar conflictos de interés: describes un estudio nuevo y te muestra los informes anteriores más parecidos, con el solicitante, la constructora, la obra, el contacto y la dirección. Ahí se ve rápido si ya trabajamos en el mismo proyecto para otra parte.

## Cómo funciona

```
 PDFs ──► extractor.py ──────► chunks + metadatos ──► indexador.py ──► ChromaDB
          pdfplumber            (n° informe, fecha,    e5-large (GPU)
          PyMuPDF                cliente, RUT, obra…)
          Tesseract (OCR)
                                                                         │
                                     app.py (Gradio) ◄───────────────────┤
                                                                         │
                                     consultor.py (Ollama) ◄─────────────┘
```

1. **Extracción.** pdfplumber saca el texto y las tablas de cada página. Solo cuando una página no tiene texto seleccionable (escaneos, fotos) se pasa por Tesseract. Al principio hacía OCR a todas las imágenes, duplicaba texto y era lentísimo.
2. **Metadatos.** De la primera página salen número de informe, fecha, cliente, RUT, tipo de ensayo y el área que lo emitió. En los informes de obra salen además constructora, obra, contacto y lugar. Si la portada no trae suficiente información, se reintenta con las páginas 2 y 3.
3. **Indexado.** El texto se corta en chunks de 500 palabras con solape de 100, se generan los embeddings con `intfloat/multilingual-e5-large` y todo se guarda en ChromaDB con los metadatos al lado. Es incremental: guarda un hash MD5 por archivo y solo procesa lo nuevo o lo que cambió.
4. **Búsqueda.** La consulta se convierte en embedding y se busca por similitud coseno. Los filtros se aplican dentro de ChromaDB, antes de la búsqueda vectorial.

## Lo que más costó

**Los formatos.** Pensé que los informes tenían un formato. Tienen como veinte. Cada área usa su propia plantilla: unos dicen "Razón Social", otros "Solicitado por" o "Nombre del Mandante", los informes sísmicos ponen `para "EMPRESA"` en el título y los certificados de calibración vienen en bilingüe. `extractor.py` tiene regex para cada formato y las prueba de la más específica a la más general. Si aun así faltan datos, le pasa el texto a llama3.1 para que intente sacarlos.

**Caracteres rotos.** Muchos PDFs traen la `º`, la `ó` o la `í` como `�`. Por eso las regex usan `.{0,3}` en lugar de la letra (`Raz.{0,3}n Social`). Es feo, pero funciona.

**PDFs con dos capas de texto.** Algunos informes tienen un sello de verificación sobre la portada y pdfplumber solo leía el sello. En esos casos cambio a PyMuPDF, que decodifica mejor esas fuentes.

**Las muestras chicas engañan.** Una regex que funciona con los tres PDFs que tienes abiertos casi siempre falla en el cuarto formato. Terminé validando cada cambio contra muestras aleatorias de todas las áreas (`diagnostico_extraccion.py`). Así la extracción subió de ~60% a ~94%, y así apareció un bug en que una regex se tragaba 2.000 caracteres de basura porque en un formato pdfplumber pegaba las palabras sin espacios.

**El prefijo de e5.** El modelo espera `passage: ` delante del texto al indexar y `query: ` al buscar. Sin eso los resultados empeoran harto y no aparece ningún error que te avise.

**Filtrar antes, no después.** La primera versión del filtro por RUT traía los 10 fragmentos más parecidos y recién ahí filtraba por RUT, así que casi siempre devolvía vacío. Ahora el filtro va como cláusula `where` en ChromaDB.

## Números

Prueba de carga con 100 informes aleatorios por cada una de las 21 áreas, en una RTX 4070 Ti:

| | |
|---|---|
| Archivos procesados | 1.491 de 1.491, sin errores |
| Fragmentos indexados | 12.287 |
| Tiempo total | 17 min (~1,4 archivos/s) |
| Promedio por archivo | 466 ms extracción, 229 ms embeddings |

Extracción de metadatos solo con regex, sin ayuda del LLM (373 informes de las 21 áreas):

| Campo | Éxito |
|---|---|
| N° de informe | 99,7% |
| Fecha | 97,9% |
| Cliente | 94,6% |
| Tipo de ensayo | 89,0% |
| RUT | 85,5% |

El RUT queda más bajo porque algunos formatos simplemente no lo traen (ensayos dinámicos y parte de los geotécnicos).

## Cómo correrlo

Lo desarrollé en Windows 11 con Python 3.11. Además de Python necesitas:

- [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) con el idioma español. El código lo busca en `C:\Program Files\Tesseract-OCR\tesseract.exe` (la ruta está al inicio de `src/extractor.py`).
- [Ollama](https://ollama.com) con `ollama pull llama3.1`. Es opcional, la búsqueda funciona sin LLM.
- Una GPU NVIDIA también es opcional. Sin GPU funciona igual, pero indexar es bastante más lento.

```powershell
python -m venv venv
.\venv\Scripts\activate

# Solo si tienes GPU NVIDIA (yo uso CUDA 12.4)
pip install torch --index-url https://download.pytorch.org/whl/cu124

pip install -r requirements.txt
python app.py
```

La interfaz queda en http://127.0.0.1:7860. La primera vez descarga el modelo de embeddings (~2 GB) desde Hugging Face; después funciona sin internet.

Los PDFs van en la carpeta `pdfs/` (puede tener subcarpetas) y se indexan desde la pestaña *Indexar*, o con `python indexar.py` si son muchos.

Un par de detalles:

- El sistema busca archivos `*_para_firma.file`, que es como vienen los informes en el laboratorio (son PDFs con otra extensión). Para probarlo con PDFs normales hay que cambiar ese patrón en `src/pipeline.py`, `indexar.py` y `app.py`.
- Si Gradio falla con `TypeError: unhashable type: 'dict'`, es la versión de starlette: `pip install starlette==0.40.0`.

## Estructura

```
app.py                     interfaz Gradio (Buscar, Conflictos, Indexar, Estado)
src/extractor.py           texto, tablas, OCR y metadatos de cada PDF
src/indexador.py           ChromaDB + embeddings, búsqueda con filtros
src/pipeline.py            indexado incremental de una carpeta
src/consultor.py           prompts y llamadas a Ollama
indexar.py                 indexado masivo por consola
diagnostico_extraccion.py  tasa de extracción por área con muestras aleatorias
verificar_indice.py        revisa la calidad de lo que quedó en ChromaDB
test_indexar.py            prueba de carga (N archivos por área)
```

## Sobre los datos

No hay PDFs en el repo y no los va a haber, porque son informes de clientes. Tampoco se sube el índice (`data/`), porque guarda el texto de los informes. Los ejemplos que aparecen en los comentarios y en la interfaz son inventados.

## Pendiente

- Generar PDFs de ejemplo con datos falsos para que cualquiera pueda probarlo sin los informes reales.
- Detectar cruces de forma automática, por ejemplo listar las obras que aparecen con más de un solicitante distinto. Hoy eso se hace a mano con los filtros.
- Búsqueda híbrida con BM25. La dependencia ya está, falta conectarla.
- Un formato de informes geotécnicos sale con letras intercaladas (dos capas de texto mezcladas) y eso ensucia los embeddings de esa área.
- Tests unitarios para las regex, con textos de ejemplo de cada formato.
- Probar `multilingual-e5-small` si hace falta que la búsqueda sea más rápida.
