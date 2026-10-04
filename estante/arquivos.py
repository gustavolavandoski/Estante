"""Leitura local dos documentos: hash, texto, metadados internos, anotações e impressão de conteúdo."""
import hashlib
import os
import re

import pymupdf

pymupdf.TOOLS.mupdf_display_errors(False)

EXTENSOES = (".pdf", ".epub")
PAGINAS_LIDAS = 8
TIPOS_MARCACAO = {"Highlight", "Underline", "StrikeOut", "Squiggly", "Ink", "Text", "FreeText",
                  "Square", "Circle", "Line", "Polygon", "PolyLine", "Caret", "Stamp"}


def longo(p):
    """No Windows, o prefixo \\\\?\\ libera caminhos com mais de 260 caracteres."""
    p = os.path.abspath(str(p))
    if os.name != "nt" or p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p


def curto(p):
    p = str(p)
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    return p[4:] if p.startswith("\\\\?\\") else p


def listar(raiz, ignorar=()):
    """Todos os documentos sob `raiz` (recursivo), ignorando as pastas em `ignorar`."""
    ignorar = [os.path.normcase(os.path.abspath(i)) for i in ignorar if i]
    achados = []
    for pasta, subpastas, nomes in os.walk(longo(raiz), onerror=lambda e: None):
        visivel = os.path.normcase(curto(pasta))
        if any(visivel == i or visivel.startswith(i + os.sep) for i in ignorar):
            subpastas[:] = []
            continue
        subpastas[:] = [s for s in subpastas if not s.startswith(("$", ".")) and s != "System Volume Information"]
        achados += [os.path.join(curto(pasta), n) for n in nomes
                    if n.lower().endswith(EXTENSOES) and not n.startswith("~$")]
    return sorted(achados, key=str.lower)


def sha1(path):
    h = hashlib.sha1()
    with open(longo(path), "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def titulo_maior_fonte(doc):
    """Palpite de título: o texto com a maior fonte da primeira página."""
    try:
        blocos = doc[0].get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return ""
    spans = [(round(s["size"], 1), s["text"].strip()) for b in blocos for l in b.get("lines", [])
             for s in l["spans"] if len(s["text"].strip()) > 1]
    if not spans:
        return ""
    maior = max(t for t, _ in spans)
    titulo = re.sub(r"\s+", " ", " ".join(x for t, x in spans if t >= maior - 0.5)).strip()
    return titulo if 8 <= len(titulo) <= 250 else ""


def ler(path):
    """Extrai tudo o que é necessário de um documento abrindo-o uma única vez."""
    info = {"texto": "", "meta": {}, "paginas": 0, "anotacoes": 0, "impressao": "", "titulo_fonte": "", "erro": ""}
    try:
        doc = pymupdf.open(longo(path))
    except Exception as e:  # noqa: BLE001
        info["erro"] = f"não abriu: {str(e)[:80]}"
        return info
    try:
        info["paginas"] = doc.page_count
        info["meta"] = {k: v for k, v in (doc.metadata or {}).items() if v}
        info["texto"] = "\n".join(doc[i].get_text() for i in range(min(PAGINAS_LIDAS, doc.page_count)))
        if doc.is_pdf:
            info["anotacoes"] = sum(1 for pg in doc for a in (pg.annots() or []) if a.type[1] in TIPOS_MARCACAO)
            info["titulo_fonte"] = titulo_maior_fonte(doc)
        primeiras = re.sub(r"\W+", "", " ".join(doc[i].get_text() for i in range(min(5, doc.page_count))).lower())
        if len(primeiras) > 300:  # escaneados sem texto só são comparados pelo hash
            info["impressao"] = f"{doc.page_count}:" + hashlib.sha1(primeiras[:20000].encode()).hexdigest()
    except Exception as e:  # noqa: BLE001
        info["erro"] = f"erro de leitura: {str(e)[:80]}"
    finally:
        doc.close()
    return info


def agrupar_duplicatas(itens):
    """Une itens com o mesmo hash ou o mesmo conteúdo (páginas + texto inicial).
    `itens`: lista de dicts com 'sha1' e 'impressao'. Retorna lista de grupos (listas de índices) com 2+ itens."""
    pai = list(range(len(itens)))

    def raiz(x):
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    for campo in ("sha1", "impressao"):
        primeiro = {}
        for i, it in enumerate(itens):
            k = it.get(campo)
            if not k:
                continue
            if k in primeiro:
                pai[raiz(i)] = raiz(primeiro[k])
            else:
                primeiro[k] = i
    grupos = {}
    for i in range(len(itens)):
        grupos.setdefault(raiz(i), []).append(i)
    return [g for g in grupos.values() if len(g) > 1]
