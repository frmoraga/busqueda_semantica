"""Extrae texto, tablas e imágenes de PDFs."""
import fitz  # pymupdf
import pdfplumber
import pytesseract
import re
from PIL import Image
from pathlib import Path
import io

_RE_UNIDAD = re.compile(r'[/\\]u_(\d+)[/\\]')
# Indica que pdfplumber solo capturó el sello de verificación (u_37 y similares)
_RE_SOLO_VERIFICA = re.compile(r"dictuc\.cl/verifica", re.IGNORECASE)

pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ─────────────────────────────────────────────
# FORMATOS CONOCIDOS
# ─────────────────────────────────────────────
# A — Mayoría de unidades (INFORME DE ENSAYOS / cabecera formal):
#   INFORME DE ENSAYOS Nº 1 234567 / FECHA DD-MM-YYYY
#   Razón Social EMPRESA / RUT XX.XXX.XXX-X
#
# B — u_20 (dos bloques Mandante + Cliente):
#   Antecedentes del Cliente / Razón Social CLIENTE REAL
#
# C — u_22, u_23 (Calibración):
#   CERTIFICADO DE CALIBRACIÓN Nº / Razón Social (Customer) X
#
# D — _para_firma con sello inicial:
#   1234567 DD-MM-YYYY Informe Nº ...
#
# E — u_30 (Ensayos Dinámicos):
#   INFORME Nº 1234567 / Fecha:DD-MM-YYYY / CLIENTE : EMPRESA
#
# F — u_32 (Certificados de Cemento):
#   CERTIFICADO Nº1 234567 FECHA DD-MM-YYYY
#   Solicitante Nombre Rut solicitante: XX.XXX.XXX-X
#
# G — u_6, u_8 (Fisicoquímica / Alimentos):
#   IN°1234567 / Fecha: 06 de enero de 2026
#   Solicitado por: EMPRESA RUT XX.XXX.XXX-X
#
# H — u_36, u_37 (Informes sísmicos / técnicos breves):
#   Informe N° 1234567
#   Informe Sísmico Proyecto "..." para "EMPRESA"
#   Santiago, 26 de marzo de 2026
#
# I — u_38 (Morteros / obra):
#   INFORME DE ENSAYO Nº [N°] FECHA [fecha]
#   Nombre Solicitante EMPRESA / RUT Solicitante XX.XXX.XXX-X
# ─────────────────────────────────────────────

# ── NÚMERO ──────────────────────────────────
_RE_NUMERO = re.compile(
    r"(?:INFORME\s+DE\s+ENSAYOS?|INFORME|CERTIFICADO\s+DE\s+(?:CALIBRACI.{0,5}N|CONFORMIDAD[^.]{0,30})|Informe)\s+"
    r"N.{0,3}?\s*(1?\s*\d{5,7})",
    re.IGNORECASE
)
# Formato G: IN°1234567
_RE_NUMERO_IN = re.compile(r"\bIN[°º]\s*(\d{6,7})", re.IGNORECASE)
# Formato H: "Informe Número XXXXXXX" — \s* para manejar pymupdf sin espacios
_RE_NUMERO_NUM = re.compile(r"Informe\s*N[úu]mero\s*(\d{6,7})", re.IGNORECASE)
# Fallback amplio: 7 dígitos que empieza con 1 — usa lookahead/behind para
# manejar texto pymupdf concatenado donde no hay \b entre dígito y letra
_RE_NUMERO_BROAD = re.compile(r"(?<!\d)(1\d{6})(?!\d)")

# ── FECHA ────────────────────────────────────
# Formato A/C: "Fecha de emisión DD-MM-YYYY" o "FECHA DD-MM-YYYY" (espacio opcional)
_RE_FECHA = re.compile(
    r"(?:Fecha\s+de\s+emisi.{0,3}n|FECHA)\s*(\d{2}[-/]\d{2}[-/]\d{4})",
    re.IGNORECASE
)
# Formato E: "Fecha:DD-MM-YYYY" o "Fecha :DD-MM-YYYY"
_RE_FECHA_COLON = re.compile(r"Fecha\s*:\s*(\d{2}[-/]\d{2}[-/]\d{4})", re.IGNORECASE)
# Formato G/H: "Fecha: 06 de enero de 2026" o "Santiago, 26 de marzo de 2026"
_RE_FECHA_TEXTO = re.compile(
    r"(?:Fecha\s*:\s*|(?:Santiago|Valpara[ií]so|Concepci[oó]n|Vi[ñn]a|Arica|"
    r"Antofagasta|Iquique|La\s+Serena|Rancagua|Talca|Temuco|Puerto\s+Montt)[^,\n]*,\s*)"
    r"(\d{1,2}(?:\s+de)?\s+\w+(?:\s+de)?\s+\d{4})",
    re.IGNORECASE
)
# Fallback: fecha en texto sin prefijo ("06 de Enero de 2026" o "22 abril 2026")
_RE_FECHA_TEXTO_LIBRE = re.compile(
    r"\b(\d{1,2}(?:\s+de)?\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
    r"septiembre|octubre|noviembre|diciembre)(?:\s+de)?\s+\d{4})\b",
    re.IGNORECASE
)
# Fallback: fecha suelta DD-MM-YYYY o DD/MM/YYYY en los primeros 150 chars
_RE_FECHA_SUELTA = re.compile(r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b")

_MESES = {
    "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
    "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
    "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12"
}


def _fecha_texto_a_iso(texto: str) -> str:
    """'26 de marzo de 2026' o '22 abril 2026' → '26-03-2026'. Devuelve '' si no parsea."""
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)(?:\s+de)?\s+(\d{4})", texto, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", texto, re.IGNORECASE)
    if not m:
        return ""
    dia, mes_txt, anio = m.group(1), m.group(2).lower(), m.group(3)
    mes_num = _MESES.get(mes_txt)
    if mes_num and int(anio) >= 2000:
        return f"{dia.zfill(2)}-{mes_num}-{anio}"
    return ""


# ── RAZÓN SOCIAL ─────────────────────────────
# Formato A/C: "Razón Social (Customer)? :? NOMBRE"
_RE_RAZON = re.compile(r"Raz.{0,3}n\s+[Ss]ocial\s*(?:\([^)]*\))?\s*:?\s*(.+)", re.IGNORECASE)
# Formato B: bajo "Antecedentes del Cliente"
_RE_RAZON_CLIENTE = re.compile(
    r"Antecedentes\s+del\s+Cliente\s+Raz.{0,3}n\s+[Ss]ocial\s*:?\s*(.+)",
    re.IGNORECASE
)
# Formato E: "CLIENTE : EMPRESA"
_RE_RAZON_CLIENTE_LABEL = re.compile(r"^CLIENTE\s*:\s*(.+)", re.IGNORECASE | re.MULTILINE)
# Formato F: "Solicitante NOMBRE Rut solicitante"
_RE_RAZON_SOLICITANTE = re.compile(
    r"Solicitante\s+(.+?)\s+[Rr]ut\s+solicitante",
    re.IGNORECASE
)
# Formato G: "Solicitado por :? NOMBRE [RUT/\n]"
_RE_RAZON_SOLICITADO = re.compile(
    r"Solicitado\s+por\s*:?\s*(.+?)(?:\s+RUT\s|\s*\n)",
    re.IGNORECASE
)
# Formato H: 'para "EMPRESA SPA"' o 'para “EMPRESA”' — informes sísmicos/técnicos
_RE_RAZON_PARA = re.compile(r'\bpara\s+[“"]([^”"\n]+)[”"]', re.IGNORECASE)
# Formato I: "Nombre Solicitante EMPRESA"
_RE_RAZON_NOM_SOLIC = re.compile(r"Nombre\s+Solicitante\s+(.+)", re.IGNORECASE)

# ── RUT ──────────────────────────────────────
# Formato A/C: "RUT (Id. Company)? :? XX.XXX.XXX-X"
_RE_RUT = re.compile(r"RUT\s*(?:\([^)]*\))?\s*:?\s*([\d.]+\s*[-]\s*[0-9kK])", re.IGNORECASE)
# Formato B: bajo "Antecedentes del Cliente"
_RE_RUT_CLIENTE = re.compile(
    r"Antecedentes\s+del\s+Cliente.*?RUT\s*:?\s*([\d.]+\s*[-]\s*[0-9kK])",
    re.IGNORECASE | re.DOTALL
)
# Formato F: "Rut solicitante: XX.XXX.XXX-X"
_RE_RUT_SOLICITANTE = re.compile(
    r"[Rr]ut\s+solicitante\s*:?\s*([\d.]+\s*[-]\s*[0-9kK])",
    re.IGNORECASE
)
# Formato I: "RUT Solicitante XX.XXX.XXX-X"
_RE_RUT_NOM_SOLIC = re.compile(r"RUT\s+Solicitante\s+([\d.]+\s*[-]\s*[0-9kK])", re.IGNORECASE)
# Formato u_20/u_28 (CERTIFICADO con Mandante): "Nombre del Mandante X RUT del Mandante Y"
_RE_RAZON_MANDANTE = re.compile(
    r"Nombre\s+del\s+Mandante\s+(.+?)\s+RUT\s+del\s+Mandante",
    re.IGNORECASE | re.DOTALL
)
_RE_RUT_MANDANTE = re.compile(
    r"RUT\s+del\s+Mandante\s+([\d.]+\s*[-]\s*[0-9kK])",
    re.IGNORECASE
)
# Formato u_38 bare: "Solicitante NOMBRE RUT XX.XXX.XXX-X" (sin "solicitante" tras RUT)
_RE_RAZON_SOLIC_BARE = re.compile(
    r"^Solicitante\s+(.+?)\s+RUT\s+[\d]",
    re.IGNORECASE | re.MULTILINE
)
# Formato H sin comillas: 'para EMPRESA ...' (u_24/u_36/u_37) — sin IGNORECASE para evitar
# capturar texto de cuerpo que empieza en minúscula (e.g., "para el diseño de")
_RE_RAZON_PARA_NOQUOTES = re.compile(
    r'\b[Pp]ara\s+([A-ZÁÉÍÓÚÑ][^"\n]{3,80}?)'
    r'(?=\s+(?:Santiago|Valpara|Concep|Vi[ñn]a|Arica|Antofagasta|RUT|\d{2}[-/])|\s*[,\n]|\s*$)',
    re.MULTILINE
)
# Formato H concatenado (pymupdf une palabras): 'SoldaduraParaEMPRESA SACiudad'
# Sin \b porque "Para" puede estar unido a la palabra anterior sin espacio
_RE_RAZON_PARA_CONCAT = re.compile(
    r'[Pp]ara([A-ZÁÉÍÓÚÑ][^"\n]{5,80}?)'
    r'(?=Santiago|Valpara|Concep|Vi[ñn]a|Arica|Antofagasta|[,.\s]\d{1,2}\s+de)',
    re.MULTILINE
)

# ── UNIDAD DICTUC (área/laboratorio que realiza el informe) ──
# Formato G/F (alimentos, cemento): "Laboratorio de ensayo Unidad de Aromas y Sabores, Área..."
#                                    "Laboratorio de ensayo Laboratorio de Cemento – Unidad Ingeniería de Materiales"
# Formato calibración: "Laboratorio de calibración DICTUC S.A. – Sección Ensayos Mecánicos"
# (?!\s*Din.{0,3}micos) excluye el letterhead de u_30 "LABORATORIO DE ENSAYOS DINÁMICOS": ahí
# "ensayo" no es una etiqueta seguida de un valor, es el nombre completo de la unidad (ver
# _RE_UNIDAD_CONOCIDA). La cota (.{1,150}?) es red de seguridad: en textos con columnas
# concatenadas sin espacios (pdfplumber), el \b de la etiqueta de corte puede no calzar nunca
# y sin cota el grupo capturaría de más hasta el final del documento.
_RE_UNIDAD_LAB = re.compile(
    r"(?:^|\n)\s*Laboratorio\s+de\s+(?:ensayos?|calibraci.{0,3}n)(?!\s*Din.{0,3}micos)\b\s*:?\s*(.{1,150}?)\s*(?:\n\s*(?:Direcci.{0,3}n|Lugar)\b|\Z)",
    re.IGNORECASE | re.DOTALL
)
# Formato C (certificación): "Organismo Certificador DICTUC S.A. – Unidad Servicios Mecánicos (OCP)"
_RE_UNIDAD_ORGANISMO = re.compile(
    r"Organismo\s+Certificador\s+DICTUC\s+S\.?A\.?\s*[–\-]\s*(.+?)(?:\n|\.\s|\(|$)",
    re.IGNORECASE
)
# Letterhead directo al inicio de página (ej. auditorías BPM): "Unidad de Fisicoquímica y Microbiología"
_RE_UNIDAD_LETRHEAD = re.compile(r"^\s*(Unidad\s+de\s+[^\n]{3,60})", re.IGNORECASE)
# Frases de área/unidad DICTUC conocidas, mencionadas como texto plano en cualquier parte
# de la página (firma, pie de página, cuerpo). Cubre formatos donde no hay etiqueta explícita.
_RE_UNIDAD_CONOCIDA = re.compile(
    r"(Servicios\s+Mec.{0,3}nicos\s+y\s+Metrol.{0,3}gicos"
    r"|Ensayos\s+Mec.{0,3}nicos"
    r"|Ensayos\s+Din.{0,3}micos"
    r"|Ingenier.{0,3}a\s+de\s+Materiales"
    r"|Resistencia\s+de\s+Materiales"
    r"|Fisicoqu.{0,3}mica\s+y\s+Microbiolog.{0,3}a"
    r"|(?:Unidad|Centro)\s+de\s+Aromas\s+y\s+Sabores"
    r"|Experto\s+T.{0,3}cnico\s+Independiente"
    r"|Ingenier.{0,3}a\s+S.{0,3}smica"
    r"|Geotecnia)",
    re.IGNORECASE
)
# "Resistencia de Materiales" y "Ingeniería de Materiales" son la misma unidad DICTUC
# (distintos informes/firmas usan uno u otro nombre indistintamente) -> normalizar a uno solo.
_RE_UNIDAD_MATERIALES_SINONIMO = re.compile(r"Resistencia\s+de\s+Materiales", re.IGNORECASE)


def _limpiar_unidad(texto: str) -> str:
    texto = re.sub(r"\s+", " ", texto).strip(" .,")
    # Si el texto capturado incluye un tramo "Unidad X" / "Sección X", quedarse solo con eso
    m = re.search(r"((?:Unidad|Secci.{0,3}n)\s+.+)$", texto, re.IGNORECASE)
    if m:
        texto = m.group(1).strip(" .,")
    texto = _RE_UNIDAD_MATERIALES_SINONIMO.sub("Ingeniería de Materiales", texto)
    return texto


def _extraer_unidad_dictuc(texto_pagina1: str, tipo_ensayo: str = "") -> str:
    t = texto_pagina1

    # Acotado a los primeros 2000 caracteres: evita que el (.+?) con DOTALL
    # se extienda de más si el documento no trae ninguna etiqueta de corte.
    m = _RE_UNIDAD_LAB.search(t[:2000])
    if m:
        return _limpiar_unidad(m.group(1))

    m = _RE_UNIDAD_ORGANISMO.search(t)
    if m:
        return _limpiar_unidad(m.group(1))

    m = _RE_UNIDAD_LETRHEAD.match(t)
    if m:
        base = m.group(1).strip()
        resto = t[m.end():m.end() + 100].lstrip()
        m_area = re.match(r"(.{0,3}rea\s+[^\n]{3,60})", resto, re.IGNORECASE)
        if m_area:
            return f"{base} — {m_area.group(1).strip()}"
        return base

    m = _RE_UNIDAD_CONOCIDA.search(t)
    if m:
        return _limpiar_unidad(m.group(1))

    # Heurística de alta confianza: el formato estándar de ensayo de morteros/hormigones
    # nunca declara la unidad como texto (queda solo en el sello de firma, que es una imagen),
    # pero ese ensayo siempre lo realiza Ingeniería de Materiales.
    if re.search(r"MORTEROS?\s+Y\s+HORMIGONES|ENSAYO[S]?\s+DE\s+HORMIG.{0,3}N", tipo_ensayo, re.IGNORECASE):
        return "Ingeniería de Materiales"

    return ""


# ── CAMPOS RELACIONALES (para cruce de conflictos de interés) ──
# El "Solicitante" (razon_social) es quien paga el informe, pero en ensayos de obra
# (hormigón/morteros) la "Constructora" y la "Obra" son entidades/proyectos distintos que
# también son relevantes para detectar conflictos (ej. mismo proyecto, distinto solicitante).
_RE_OBRA = re.compile(r"(?:^|\n)\s*Obra\s+(.+?)(?:\n|$)", re.IGNORECASE)
_RE_CONSTRUCTORA = re.compile(r"(?:^|\n)\s*Constructora\s+(.+?)(?:\n|$)", re.IGNORECASE)

# Persona de contacto del cliente — varía de nombre según formato, pero identifica al mismo
# tipo de entidad (posible punto de cruce entre razones sociales distintas).
_RE_CONTACTO_ATENCION = re.compile(
    r"Atenci.{0,3}n\s*:?\s*(.+?)(?:\s+Orden\s+de\s+Compra|\n|$)", re.IGNORECASE
)
_RE_CONTACTO_CONTRAPARTE = re.compile(
    r"Nombre\s+contraparte\s+t.{0,3}cnica\s*:?\s*(.+?)(?:\n|$)", re.IGNORECASE
)

# Ubicación física donde se realizó el trabajo/muestreo (distinta de la dirección de DICTUC,
# que es siempre Vicuña Mackenna 4860 y no aporta nada al cruce de conflictos).
_RE_LUGAR_OBRA = re.compile(r"Direcci.{0,3}n\s+de\s+la\s+obra\s+(.+?)(?:\n|$)", re.IGNORECASE)
_RE_LUGAR_MUESTREO_DIR = re.compile(r"Direcci.{0,3}n\s+del\s+muestreo\s+(.+?)(?:\n|$)", re.IGNORECASE)
_RE_LUGAR_MUESTREO = re.compile(r"Lugar\s+de\s+muestreo\s+(.+?)(?:\n|$)", re.IGNORECASE)
_RE_LUGAR_BODEGA = re.compile(r"Direcci.{0,3}n\s+de\s+bodega\s+(.+?)(?:\n|$)", re.IGNORECASE)


def _extraer_relacionales(texto_pagina1: str) -> dict:
    t = texto_pagina1

    obra = ""
    m = _RE_OBRA.search(t)
    if m:
        obra = re.sub(r"\s+", " ", m.group(1)).strip()

    constructora = ""
    m = _RE_CONSTRUCTORA.search(t)
    if m:
        constructora = re.sub(r"\s+", " ", m.group(1)).strip()

    contacto = ""
    m = _RE_CONTACTO_ATENCION.search(t)
    if m:
        contacto = re.sub(r"\s+", " ", m.group(1)).strip()
    if not contacto:
        m = _RE_CONTACTO_CONTRAPARTE.search(t)
        if m:
            contacto = re.sub(r"\s+", " ", m.group(1)).strip()

    lugar = ""
    for patron in (_RE_LUGAR_OBRA, _RE_LUGAR_MUESTREO_DIR, _RE_LUGAR_MUESTREO, _RE_LUGAR_BODEGA):
        m = patron.search(t)
        if m:
            lugar = re.sub(r"\s+", " ", m.group(1)).strip()
            break

    return {
        "obra":         obra,
        "constructora": constructora,
        "contacto":     contacto,
        "lugar":        lugar,
    }


# ── TIPO DE ENSAYO ───────────────────────────
_RE_LINEA_RUIDO = re.compile(
    r"^(INFORME\s+DE\s+ENSAYO[S]?\s+N|Informe\s+N|CERTIFICADO\s+DE\s+(CALIBRACI|CONFORMIDAD)|"
    r"FECHA\s+\d|\(|Resoluc|Fecha\s+de\s+emisi|Laboratorio\s+de|Direcci|"
    r"RUT|Raz[oó]n|Orden\s+de|Cotizaci|Ticket|Nombre|Correo|Tel[eé]f|"
    r"Solicitado|Solicitante|Unidad\s+de\s+Fisico|LABORATORIO\s+DE)",
    re.IGNORECASE
)


def _extraer_tipo_ensayo(texto_pagina1: str) -> str:
    m_corte = re.search(
        r"Antecedentes\s+del\s+(?:Mandante|Servicio|Cliente)|I\.\s+Antecedentes|"
        r"IDENTIFICACI[OÓ]N|DATOS\s+DEL\s+MUESTREO",
        texto_pagina1, re.IGNORECASE
    )
    bloque = texto_pagina1[:m_corte.start()] if m_corte else texto_pagina1[:600]

    tipo_parts = []
    for linea in bloque.split("\n"):
        l = linea.strip()
        if not l or len(l) < 6:
            continue
        m_inline = re.match(
            r"INFORME\s+DE\s+ENSAYO(?:S?)(?:\s+OFICIAL)?\s+(?!N[°º]?\s*\d)(.{6,})",
            l, re.IGNORECASE
        )
        if m_inline:
            tipo_parts.append(m_inline.group(1).strip())
            continue
        if _RE_LINEA_RUIDO.match(l):
            continue
        if re.match(r"^INFORME\s+DE\s+ENSAYO[S]?$", l, re.IGNORECASE):
            continue
        if re.match(r"^[\d\s\-/]+$", l):
            continue
        tipo_parts.append(l)

    tipo = " | ".join(tipo_parts[:2]).strip()

    if not tipo and re.search(r"CERTIFICADO\s+DE\s+CALIBRACI", texto_pagina1, re.IGNORECASE):
        tipo = "Calibración"
    if not tipo and re.search(r"CERTIFICADO\s+DE\s+CONFORMIDAD", texto_pagina1, re.IGNORECASE):
        tipo = "Certificado de Conformidad"

    return tipo[:120]


def _extraer_metadata_regex(texto_pagina1: str) -> dict:
    t = texto_pagina1

    # ── NÚMERO ──
    numero = ""
    for patron in (_RE_NUMERO_IN, _RE_NUMERO_NUM, _RE_NUMERO):
        m = patron.search(t)
        if m:
            numero = re.sub(r"\s+", "", m.group(1))
            break
    if not numero:
        m = _RE_NUMERO_BROAD.search(t[:300])
        if m:
            numero = m.group(1)

    # ── FECHA ──
    fecha = ""
    # 1) Formato con prefijo formal
    for patron in (_RE_FECHA, _RE_FECHA_COLON):
        m = patron.search(t)
        if m:
            fecha = _fecha_valida(m.group(1))
            if fecha:
                break
    # 2) Fecha en texto natural ("26 de marzo de 2026")
    if not fecha:
        m = _RE_FECHA_TEXTO.search(t)
        if m:
            fecha = _fecha_valida(_fecha_texto_a_iso(m.group(1)))
    # 3) Fecha en texto libre sin prefijo de ciudad ("06 de enero de 2026", "22 abril 2026")
    if not fecha:
        m = _RE_FECHA_TEXTO_LIBRE.search(t)
        if m:
            fecha = _fecha_valida(_fecha_texto_a_iso(m.group(1)))
    # 4) Fecha suelta en los primeros 150 caracteres (sello de _para_firma)
    if not fecha:
        m = _RE_FECHA_SUELTA.search(t[:150])
        if m:
            fecha = _fecha_valida(m.group(1))

    # ── RAZÓN SOCIAL ──
    razon = ""
    # Formato u_20/u_28 (Mandante — más específico, va primero)
    m = _RE_RAZON_MANDANTE.search(t)
    if m:
        razon = m.group(1).strip()
    # Formato B (cliente bajo "Antecedentes del Cliente")
    if not razon:
        m = _RE_RAZON_CLIENTE.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato A/C (Razón Social genérica)
    if not razon:
        m = _RE_RAZON.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato F (Solicitante ... Rut solicitante)
    if not razon:
        m = _RE_RAZON_SOLICITANTE.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato G (Solicitado por)
    if not razon:
        m = _RE_RAZON_SOLICITADO.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato I (Nombre Solicitante)
    if not razon:
        m = _RE_RAZON_NOM_SOLIC.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato u_38 bare (Solicitante NOMBRE RUT XX — sin "solicitante" tras RUT)
    if not razon:
        m = _RE_RAZON_SOLIC_BARE.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato E (CLIENTE :)
    if not razon:
        m = _RE_RAZON_CLIENTE_LABEL.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato H (para "EMPRESA")
    if not razon:
        m = _RE_RAZON_PARA.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato H sin comillas: para EMPRESA Santiago... (u_24/u_36/u_37)
    if not razon:
        m = _RE_RAZON_PARA_NOQUOTES.search(t)
        if m:
            razon = m.group(1).strip()
    # Formato H concatenado (pymupdf): ParaEMPRESA SACiudad
    if not razon:
        m = _RE_RAZON_PARA_CONCAT.search(t)
        if m:
            razon = m.group(1).strip()

    # Limpiar basura al final; RUT\b (sin \b inicial) para capturar "SpaRUT:" de pymupdf
    if razon:
        razon = re.split(r"\s{2,}|RUT\b|\bDirecci|\bContrap", razon)[0].strip()
        # Colapsar saltos de línea internos (nombres de empresa envueltos en 2 líneas)
        razon = re.sub(r"\s+", " ", razon).strip()

    # ── RUT ──
    rut = ""
    # Formato u_20/u_28 (Mandante)
    m = _RE_RUT_MANDANTE.search(t)
    if m:
        rut = _normalizar_rut(re.sub(r"\s+", "", m.group(1)).rstrip("."))
    # Formato B
    if not rut:
        m = _RE_RUT_CLIENTE.search(t)
        if m:
            rut = _normalizar_rut(re.sub(r"\s+", "", m.group(1)).rstrip("."))
    # Formato F
    if not rut:
        m = _RE_RUT_SOLICITANTE.search(t)
        if m:
            rut = _normalizar_rut(re.sub(r"\s+", "", m.group(1)).rstrip("."))
    # Formato I
    if not rut:
        m = _RE_RUT_NOM_SOLIC.search(t)
        if m:
            rut = _normalizar_rut(re.sub(r"\s+", "", m.group(1)).rstrip("."))
    # Formato A/C/G (RUT genérico)
    if not rut:
        m = _RE_RUT.search(t)
        if m:
            rut = _normalizar_rut(re.sub(r"\s+", "", m.group(1)).rstrip("."))

    return {
        "numero_informe": numero,
        "fecha_emision":  fecha,
        "razon_social":   razon,
        "rut":            rut,
    }


def _normalizar_rut(rut: str) -> str:
    rut = rut.strip().rstrip(".")
    m = re.match(r"^(\d+)[-]([0-9kK])$", rut.replace(".", "").replace(" ", ""))
    if not m:
        return ""
    cuerpo, dv = m.group(1), m.group(2).upper()
    cuerpo_fmt = ""
    for i, c in enumerate(reversed(cuerpo)):
        if i > 0 and i % 3 == 0:
            cuerpo_fmt = "." + cuerpo_fmt
        cuerpo_fmt = c + cuerpo_fmt
    return f"{cuerpo_fmt}-{dv}"


def _fecha_valida(fecha: str) -> str:
    if not fecha:
        return ""
    try:
        parts = fecha.replace("/", "-").split("-")
        if len(parts) == 3 and int(parts[2]) >= 2000:
            return fecha.replace("/", "-")
    except Exception:
        pass
    return ""


def _extraer_metadata(texto_pagina1: str) -> dict:
    meta = _extraer_metadata_regex(texto_pagina1)
    campos_ok = sum(1 for v in meta.values() if v)

    if campos_ok < 2 or not meta["numero_informe"]:
        try:
            from .consultor import extraer_metadata_llm
            llm = extraer_metadata_llm(texto_pagina1)
            for campo in ("numero_informe", "fecha_emision", "razon_social", "rut"):
                if not meta[campo] and llm.get(campo):
                    valor = str(llm[campo]).strip()
                    if campo == "fecha_emision":
                        valor = _fecha_valida(valor)
                    elif campo == "rut":
                        valor = _normalizar_rut(valor)
                    meta[campo] = valor
        except Exception:
            pass

    return meta


def extraer_pdf(ruta_pdf: str) -> dict:
    ruta = Path(ruta_pdf)
    resultado = {
        "nombre":   ruta.name,
        "ruta":     str(ruta),
        "metadata": {},
        "paginas":  []
    }

    fitz_portada_qr = False  # True si página 1 era portada QR y se usó fitz

    with pdfplumber.open(ruta) as pdf:
        doc_fitz = fitz.open(str(ruta))

        for num_pag, pagina in enumerate(pdf.pages):
            datos_pagina = {
                "numero":        num_pag + 1,
                "texto":         "",
                "tablas":        [],
                "imagenes_texto": []
            }

            texto = pagina.extract_text()
            if texto:
                datos_pagina["texto"] = texto.strip()

            # Si pdfplumber solo extrajo el sello de verificación (portada u_37),
            # intentar con pymupdf que decodifica fuentes no estándar mejor
            if len(datos_pagina["texto"]) < 300 and _RE_SOLO_VERIFICA.search(datos_pagina["texto"]):
                texto_fitz = doc_fitz[num_pag].get_text("text").strip()
                if len(texto_fitz) > len(datos_pagina["texto"]):
                    datos_pagina["texto"] = texto_fitz
                    if num_pag == 0:
                        fitz_portada_qr = True

            if num_pag == 0 and datos_pagina["texto"]:
                resultado["metadata"] = _extraer_metadata(datos_pagina["texto"])
                resultado["metadata"]["tipo_ensayo"] = _extraer_tipo_ensayo(datos_pagina["texto"])
                resultado["metadata"]["unidad_dictuc"] = _extraer_unidad_dictuc(
                    datos_pagina["texto"], resultado["metadata"]["tipo_ensayo"]
                )
                resultado["metadata"].update(_extraer_relacionales(datos_pagina["texto"]))

            tablas = pagina.extract_tables()
            for tabla in tablas:
                if tabla:
                    filas_limpias = [
                        [celda or "" for celda in fila]
                        for fila in tabla if any(c for c in fila if c)
                    ]
                    if filas_limpias:
                        datos_pagina["tablas"].append(filas_limpias)

            if not datos_pagina["texto"]:
                pagina_fitz = doc_fitz[num_pag]
                for img_info in pagina_fitz.get_images(full=True):
                    try:
                        img_data = doc_fitz.extract_image(img_info[0])
                        imagen = Image.open(io.BytesIO(img_data["image"]))
                        texto_ocr = pytesseract.image_to_string(imagen, lang="spa").strip()
                        if texto_ocr:
                            datos_pagina["imagenes_texto"].append(texto_ocr)
                    except Exception:
                        pass

            resultado["paginas"].append(datos_pagina)

        doc_fitz.close()

    # Cuando página 1 tiene muy poca info o era portada QR (fitz_portada_qr),
    # intentar extraer metadata de páginas 2–3; combina texto plano e imágenes OCR
    campos_ok = sum(1 for v in resultado["metadata"].values() if v)
    tipo_junk = "dictuc.cl/verifica" in resultado["metadata"].get("tipo_ensayo", "")
    if campos_ok < 3 or fitz_portada_qr or not resultado["metadata"].get("unidad_dictuc"):
        for pag in resultado["paginas"][1:3]:
            texto_ext = pag.get("texto", "") or " ".join(pag.get("imagenes_texto", []))
            if not texto_ext:
                continue
            meta_ext = _extraer_metadata(texto_ext)
            tipo_ext = _extraer_tipo_ensayo(texto_ext)
            for campo in ("numero_informe", "fecha_emision", "razon_social", "rut"):
                if not resultado["metadata"].get(campo) and meta_ext.get(campo):
                    resultado["metadata"][campo] = meta_ext[campo]
            if (not resultado["metadata"].get("tipo_ensayo") or tipo_junk) and tipo_ext:
                resultado["metadata"]["tipo_ensayo"] = tipo_ext
                tipo_junk = False
            if not resultado["metadata"].get("unidad_dictuc"):
                unidad_ext = _extraer_unidad_dictuc(texto_ext, resultado["metadata"].get("tipo_ensayo", ""))
                if unidad_ext:
                    resultado["metadata"]["unidad_dictuc"] = unidad_ext
            relacionales_ext = _extraer_relacionales(texto_ext)
            for campo, valor in relacionales_ext.items():
                if not resultado["metadata"].get(campo) and valor:
                    resultado["metadata"][campo] = valor
            if sum(1 for v in resultado["metadata"].values() if v) >= 5:
                break

    return resultado


def paginas_a_chunks(doc: dict, tamano_chunk: int = 500, solapamiento: int = 100) -> list[dict]:
    chunks = []
    meta = doc.get("metadata", {})

    for pagina in doc["paginas"]:
        partes = []

        if pagina["texto"]:
            partes.append(pagina["texto"])

        for tabla in pagina["tablas"]:
            filas_texto = [" | ".join(fila) for fila in tabla]
            partes.append("[TABLA]\n" + "\n".join(filas_texto))

        for texto_img in pagina["imagenes_texto"]:
            partes.append("[IMAGEN]\n" + texto_img)

        texto_completo = "\n\n".join(partes)
        palabras = texto_completo.split()

        inicio = 0
        while inicio < len(palabras):
            fin = inicio + tamano_chunk
            chunk_texto = " ".join(palabras[inicio:fin])
            m_unidad = _RE_UNIDAD.search(doc.get("ruta", ""))
            chunks.append({
                "texto":          chunk_texto,
                "fuente":         doc["nombre"],
                "pagina":         pagina["numero"],
                "numero_informe": meta.get("numero_informe", ""),
                "fecha_emision":  meta.get("fecha_emision", ""),
                "razon_social":   meta.get("razon_social", ""),
                "rut":            meta.get("rut", ""),
                "tipo_ensayo":    meta.get("tipo_ensayo", ""),
                "unidad":         f"u_{m_unidad.group(1)}" if m_unidad else "",
                "unidad_dictuc":  meta.get("unidad_dictuc", ""),
                "obra":           meta.get("obra", ""),
                "constructora":   meta.get("constructora", ""),
                "contacto":       meta.get("contacto", ""),
                "lugar":          meta.get("lugar", ""),
                "id":             f"{doc['nombre']}_p{pagina['numero']}_c{inicio}"
            })
            inicio += tamano_chunk - solapamiento

    return chunks
