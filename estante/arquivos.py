"""Leitura local dos documentos: hash, texto, metadados internos, anotações e impressão de conteúdo."""
import hashlib
import os
import re
import threading

import pymupdf

from . import config

pymupdf.TOOLS.mupdf_display_errors(False)

EXTENSOES = (".pdf", ".epub")
# documentos que a Estante não lê: não são identificados, mas saem da bagunça para uma pasta própria
OUTROS_FORMATOS = (".doc", ".docx", ".odt", ".rtf", ".txt", ".mobi", ".azw", ".azw3", ".djvu", ".ppt", ".pptx", ".odp")
PAGINAS_LIDAS = 8
PAGINAS_CAPA = 3  # onde se procura o título e o autor (capa, folha de rosto, 1ª página do artigo)
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


def listar(raiz, ignorar=(), extensoes=EXTENSOES):
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
                    if n.lower().endswith(extensoes) and not n.startswith("~$")]
    return sorted(achados, key=str.lower)


# hashes já calculados, por caminho, valendo enquanto tamanho e data de modificação não mudarem:
# reanalisar a biblioteca não relê gigabytes de PDFs
ARQ_HASHES = "hashes.json"
_hashes, _trava, _mudou = None, threading.Lock(), False


def _tabela():
    global _hashes
    if _hashes is None:
        _hashes = config.carregar(ARQ_HASHES, {})
    return _hashes


def _chave(path):
    return os.path.normcase(os.path.abspath(curto(path)))


def sha1(path):
    global _mudou
    st = os.stat(longo(path))
    marca = [st.st_size, st.st_mtime_ns]
    with _trava:
        guardado = _tabela().get(_chave(path))
    if guardado and guardado[:2] == marca:
        return guardado[2]
    h = hashlib.sha1()
    with open(longo(path), "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    with _trava:
        _tabela()[_chave(path)] = [*marca, h.hexdigest()]
        _mudou = True
    return h.hexdigest()


def registrar_movido(origem, destino, h):
    """Arquivo movido (mesmo conteúdo): o hash acompanha o novo caminho."""
    global _mudou
    with _trava:
        _tabela().pop(_chave(origem), None)
        st = os.stat(longo(destino))
        _tabela()[_chave(destino)] = [st.st_size, st.st_mtime_ns, h]
        _mudou = True


def salvar_hashes():
    global _mudou
    with _trava:
        if _mudou:
            # some com o que não existe mais, para a tabela não crescer sem fim
            for k in [k for k in _tabela() if not os.path.exists(longo(k))]:
                del _tabela()[k]
            config.salvar(ARQ_HASHES, _tabela())
            _mudou = False


# um nome de pessoa: 2 a 6 palavras com inicial maiúscula (partículas como "de", "van" no meio)
RE_NOME = re.compile(r"^[A-ZÀ-Ý][\w'’.-]+(?:\s+(?:(?:de|da|do|dos|das|del|della|di|du|van|von|der|le|la)\s+)*"
                     r"[A-ZÀ-Ý][\w'’.-]*){1,5}$")
NAO_NOME = re.compile(r"universi|revista|journal|review|institut|escola|school|faculdade|college|departa?ment|"
                      r"programa|program|resumo|abstract|introdu|volume|cap[ií]tulo|chapter|edi[cç]|press|editora|"
                      r"minist|governo|government|ex[eé]rcito|army|marinha|navy|centro|cent(er|re)|report|relat[oó]rio|"
                      r"artigo|article|working paper|research|pesquisa|segurança|security|defesa|defen[cs]e|"
                      # cidades e palavras de capa que parecem nomes ("Porto Alegre", "View project"…)
                      r"s[aã]o paulo|rio de janeiro|porto alegre|bras[ií]lia|belo horizonte|am[eé]rica latina|"
                      r"washington|london|new york|\bview\b|project|general|warfare|cyber|track|policy|strategy|"
                      r"espacial|declara|profissional|editor|\(ed|\beds?\.|\borgs?\.|coord|curso|course|\bdados\b|"
                      r"\bestados\b|\bforce\b|\bltda\b|\binc\b|\bcorp|\bllc\b|\bs\.?a\.?$|funda[cç][aã]o|foundation|"
                      r"cataloga|copyright|associa[cç]|\bclube\b", re.I)
PALAVRAS_COMUNS = {"em", "no", "na", "nos", "nas", "para", "com", "sobre", "por", "of", "the", "in", "for", "on",
                   "to", "with", "and", "an", "at", "from", "um", "uma", "o", "a", "os", "as"}
RE_POR = re.compile(r"\b(?:by|por)\s+([A-ZÀ-Ý][\w'’.-]+(?:\s+(?:de|da|do|dos|das|van|von)?\s*[A-ZÀ-Ý][\w'’.-]+){1,4})")


def nome_de_pessoa(linha):
    """Primeiro nome de pessoa de uma linha de autores ("Fulano de Tal1, Beltrano"), ou ''."""
    linha = re.sub(r"[\d*¹²³⁴†‡§]+(?=\s|,|;|$)", "", linha).strip()  # marcas de nota
    if len(linha) > 120 or NAO_NOME.search(linha):
        return ""
    primeiro = re.split(r",|;|\s+and\s+|\s+e\s+|\s*&\s*", linha)[0].strip()
    palavras = primeiro.split()
    if not RE_NOME.match(primeiro) or any(p.lower() in PALAVRAS_COMUNS for p in palavras):
        return ""
    return primeiro


def titulo_e_autor(doc, pagina=0):
    """Palpite de título (o texto com a maior fonte da página) e do autor (nome logo abaixo do título,
    ou "by/por Fulano" na página)."""
    try:
        blocos = doc[pagina].get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return "", ""
    linhas = []
    for b in blocos:
        for l in b.get("lines", []):
            spans = [s for s in l["spans"] if s["text"].strip()]
            if spans:
                linhas.append((round(max(s["size"] for s in spans), 1), " ".join(s["text"].strip() for s in spans)))
    if not linhas:
        return "", ""
    maior = max(t for t, _ in linhas)
    no_titulo = [i for i, (t, x) in enumerate(linhas) if t >= maior - 0.5 and len(x) > 1]
    titulo = re.sub(r"\s+", " ", " ".join(linhas[i][1] for i in no_titulo)).strip()
    if not 8 <= len(titulo) <= 250:
        return "", ""
    autor = ""
    for _, x in linhas[no_titulo[-1] + 1: no_titulo[-1] + 6]:
        autor = nome_de_pessoa(x)
        if autor:
            break
    if not autor:
        m = RE_POR.search(" ".join(x for _, x in linhas))
        autor = nome_de_pessoa(m.group(1)) if m else ""
    return titulo, autor


def ler(path):
    """Extrai tudo o que é necessário de um documento abrindo-o uma única vez."""
    info = {"texto": "", "meta": {}, "paginas": 0, "anotacoes": 0, "impressao": "", "titulo_fonte": "",
            "titulos_capa": [], "capa_ids": [], "capa": "", "erro": ""}
    try:
        doc = pymupdf.open(longo(path))
    except Exception as e:  # noqa: BLE001
        info["erro"] = f"não abriu: {str(e)[:80]}"
        return info
    try:
        info["paginas"] = doc.page_count
        info["meta"] = {k: v for k, v in (doc.metadata or {}).items() if v}
        paginas = [doc[i].get_text() for i in range(min(PAGINAS_LIDAS, doc.page_count))]
        info["texto"] = "\n".join(paginas)
        info["capa"] = "\n".join(paginas[:PAGINAS_CAPA])
        if doc.is_pdf:
            info["anotacoes"] = sum(1 for pg in doc for a in (pg.annots() or []) if a.type[1] in TIPOS_MARCACAO)
            info["capa_ids"] = [ta for ta in (titulo_e_autor(doc, i) for i in range(min(PAGINAS_CAPA, doc.page_count))) if ta[0]]
            info["titulos_capa"] = [t for t, _ in info["capa_ids"]]
            info["titulo_fonte"] = (info["titulos_capa"] or [""])[0]
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
