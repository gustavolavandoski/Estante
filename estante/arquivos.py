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
                      r"cataloga|copyright|associa[cç]|\bclube\b|comando|estado-maior|for[cç]as|armad[ao]s|manual|campanha|"
                      r"assinatura|requerente|secretaria|executiv|disputa|estrat[eé]g|elementos|\bevents?\b|"
                      r"capabilit|protection|"
                      # palavras com sufixo de substantivo comum, que não são sobrenomes ("Operações", "Sociedade"),
                      # sem pegar Nascimento, Trindade, Piedade
                      r"\w(ções|sões|(?<!trin)(?<!pie)dades?|mentos|ências|âncias|ismos?|tions?|ments?|it(y|ies)|ness|"
                      r"ship|ics)\b", re.I)
PALAVRAS_COMUNS = {"em", "no", "na", "nos", "nas", "para", "com", "sobre", "por", "of", "the", "in", "for", "on",
                   "to", "with", "and", "an", "at", "from", "um", "uma", "o", "a", "os", "as", "meu", "minha", "my", "our"}
RE_POR = re.compile(r"\b(?:by|por)\s+([A-ZÀ-Ý][\w'’.-]+(?:\s+(?:de|da|do|dos|das|van|von)?\s*[A-ZÀ-Ý][\w'’.-]+){1,4})")


# títulos, postos e ordinais antes do nome ("Prof. Dr.", "Aspirante", "Maj", "1st")
RE_TRATAMENTO = re.compile(r"^(?:(?:prof(?:essor|a)?|dra?|me|msc?|phd|sir|mr?s?|aspirante|cadete|gen|brig|cel|ten|"
                           r"maj|cap|sgt|cmte|alm|cav|inf)\.?\s+|\d+\s*(?:st|nd|rd|th|º|ª)\s+)+", re.I)


def nome_de_pessoa(linha):
    """Primeiro nome de pessoa de uma linha de autores ("Fulano de Tal1, Beltrano"), ou ''."""
    linha = re.sub(r"[\ue000-\uf8ff]", "", linha)  # símbolos de fontes como Symbol/Wingdings (o "*" da nota)
    sem_tratamento = RE_TRATAMENTO.sub("", linha.strip())
    com_posto = sem_tratamento != linha.strip()
    linha = sem_tratamento
    linha = re.sub(r"[\d*¹²³⁴†‡§]+(?=\s|,|;|$)", "", linha).strip()  # marcas de nota
    if re.search(r"\s[-–—/]\s*[A-Z]{2}$", linha):  # "POUSO ALEGRE - MG": cidade e estado
        return ""
    linha = re.sub(r"\s+[-–—]\s+(\w{1,4}\b\.?\s*){1,3}$", "", linha)  # "RODRIGO SANTOS COSTA - Maj Art"
    if len(linha) > 120 or NAO_NOME.search(linha):
        return ""
    primeiro = re.split(r",|;|\s+and\s+|\s+e\s+|\s*&\s*", linha)[0].strip()
    palavras = primeiro.split()
    if com_posto and re.fullmatch(r"[A-ZÀ-Ý][\w'’-]+", primeiro):
        return primeiro  # "Gen Machado", "MAJ Israel": posto e sobrenome
    if not RE_NOME.match(primeiro) or any(p.lower() in PALAVRAS_COMUNS for p in palavras):
        return ""
    return primeiro


RE_SECAO = re.compile(r"^(resumo|abstract|introdu|palavras|keywords|sum[aá]rio|contents|agradec)", re.I)


def _candidatos_titulo(linhas):
    """Grupos de linhas (índices) que podem ser o título, do mais destacado ao menos: as de fonte parecida,
    da maior para a menor; e, quando o corpo do texto já tem a maior fonte, os trechos em negrito."""
    tamanhos = sorted({t for t, x, _ in linhas if not re.fullmatch(r"[\d\W]*", x)}, reverse=True)
    vistos = set()
    for tam in tamanhos:
        if tam in vistos:
            continue
        grupo = [i for i, (t, x, _) in enumerate(linhas) if tam * 0.96 <= t <= tam and len(x) > 1]
        vistos.update(linhas[i][0] for i in grupo)
        if not grupo:
            continue
        if len(" ".join(linhas[i][1] for i in grupo)) <= 250:
            yield grupo, False
            continue
        # título do tamanho do corpo: só o negrito o destaca
        run = []
        for i, (t, x, negrito) in enumerate(linhas):
            continua = run and (negrito or (x.isupper() and linhas[run[-1]][1].isupper()))
            if t >= tam * 0.96 and (continua or (not run and negrito)) and not RE_SECAO.match(x):
                run.append(i)
            elif run:
                yield run, True
                run = []
        if run:
            yield run, True
        return  # abaixo da fonte do corpo só há notas e rodapés


def _autor_perto(linhas, primeira, ultima):
    """Nome logo abaixo do título; senão, logo acima (capas que põem o autor antes do título):
    o primeiro da lista de autores, ou seja, o mais alto de um bloco seguido de nomes."""
    for _, x, _ in linhas[ultima + 1: ultima + 6]:
        autor = nome_de_pessoa(x)
        if autor:
            return autor
    autor = ""
    for _, x, _ in reversed(linhas[max(0, primeira - 6): primeira]):
        nome = nome_de_pessoa(x)
        if nome:
            autor = nome
        elif autor:
            break
    if autor:
        return autor
    m = RE_POR.search(" ".join(x for _, x, _ in linhas))
    return nome_de_pessoa(m.group(1)) if m else ""


def titulo_e_autor(doc, pagina=0):
    """Palpite de título (o texto com a maior fonte da página, ou em negrito) e do autor (nome logo abaixo
    ou logo acima do título, ou "by/por Fulano" na página)."""
    try:
        blocos = doc[pagina].get_text("dict")["blocks"]
    except Exception:  # noqa: BLE001
        return "", ""
    linhas = []
    for b in blocos:
        for l in b.get("lines", []):
            spans = [s for s in l["spans"] if s["text"].strip()]
            if spans:
                texto = re.sub(r"_{3,}|\s+", " ", " ".join(s["text"].strip() for s in spans)).strip()  # "DA GUERRA ____"
                if not texto:
                    continue
                linhas.append((round(max(s["size"] for s in spans), 1), texto,
                               all(s["flags"] & 16 for s in spans)))
    autor_destacado = ""
    for grupo, por_negrito in _candidatos_titulo(linhas):
        # continuação do título em letra minúscula e fonte um pouco menor ("Crimen Organizado… / en Latinoamérica")
        while (grupo[-1] + 1 < len(linhas) and linhas[grupo[-1] + 1][1][:1].islower()
               and linhas[grupo[-1] + 1][0] >= linhas[grupo[-1]][0] * 0.75):
            grupo = grupo + [grupo[-1] + 1]
        titulo = re.sub(r"\s+", " ", " ".join(linhas[i][1] for i in grupo)).strip()
        if len(titulo) < 8 or len(titulo) > 250:
            continue  # sigla ou logotipo em fonte enorme ("CNT"): o título é o próximo
        nome = nome_de_pessoa(titulo)
        if por_negrito and nome and len(nome) >= len(titulo) - 3:
            autor_destacado = autor_destacado or nome  # autor em negrito antes do título
            continue
        return titulo, autor_destacado or _autor_perto(linhas, grupo[0], grupo[-1])
    return "", ""


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
