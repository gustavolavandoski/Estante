"""Identificação de autor, título e ano: DOI/ISBN em bases abertas (CrossRef, OpenAlex,
Open Library, Google Books) e, sem identificador, os metadados e o texto do próprio arquivo."""
import re
import time
from pathlib import Path

import requests

from . import __version__
from .nomes import capitalizar, norm, similaridade, suavizar_titulo

HTTP = requests.Session()


def configurar(email=""):
    """A CrossRef pede um e-mail de contato (polite pool); é opcional."""
    contato = f"; mailto:{email}" if email else ""
    HTTP.headers["User-Agent"] = f"Estante/{__version__} (organizador de biblioteca pessoal{contato})"


configurar()


def get_json(url, **params):
    for tentativa in range(3):
        try:
            r = HTTP.get(url, params=params or None, timeout=25)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(5 * (tentativa + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            time.sleep(2 * (tentativa + 1))
    return None


# ------------------------------------------------------------ identificadores
RE_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)
RE_ISBN = re.compile(r"(?:ISBN(?:-1[03])?[:\s]*)?\b((?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dXx])\b")


def isbn_valido(isbn):
    if len(isbn) == 10:
        if not re.fullmatch(r"\d{9}[\dX]", isbn):
            return False
        return sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(isbn)) % 11 == 0
    if len(isbn) == 13 and isbn.isdigit() and isbn[:3] in ("978", "979"):
        return sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn)) % 10 == 0
    return False


def achar_dois(texto):
    vistos = []
    for m in RE_DOI.finditer(texto):
        doi = m.group(1).rstrip(".,;:)]}'").lower()
        if doi not in vistos:
            vistos.append(doi)
    return vistos


def achar_isbns(texto):
    vistos = []
    for exige_rotulo in (True, False):  # primeiro os precedidos de "ISBN"
        for m in RE_ISBN.finditer(texto):
            if exige_rotulo and "isbn" not in m.group(0).lower():
                continue
            isbn = re.sub(r"[\s-]", "", m.group(1)).upper()
            if not exige_rotulo and len(isbn) != 13:
                continue
            if isbn_valido(isbn) and isbn not in vistos:
                vistos.append(isbn)
    return vistos


# ------------------------------------------------------------------- fontes
def de_crossref(item):
    pessoas = item.get("author") or item.get("editor") or []
    sobrenome = (pessoas[0].get("family") or pessoas[0].get("name") or "") if pessoas else ""
    titulo = (item.get("title") or [""])[0]
    sub = item.get("subtitle") or [""]
    if sub and sub[0] and norm(sub[0]) not in norm(titulo):
        titulo = f"{titulo}: {sub[0]}"
    ano = ""
    for campo in ("published-print", "issued", "published-online", "created"):
        partes = (item.get(campo) or {}).get("date-parts") or [[None]]
        if partes[0] and partes[0][0]:
            ano = str(partes[0][0])
            break
    return {"sobrenome": capitalizar(sobrenome), "titulo": suavizar_titulo(titulo), "ano": ano}


def de_openalex(w):
    autores = w.get("authorships") or []
    nome = (autores[0].get("author") or {}).get("display_name", "") if autores else ""
    return {"sobrenome": capitalizar(nome.split()[-1]) if nome else "",
            "titulo": suavizar_titulo(w.get("title") or ""), "ano": str(w.get("publication_year") or "")}


def buscar_doi(doi):
    j = get_json(f"https://api.crossref.org/works/{doi}")
    if j and j.get("message", {}).get("title"):
        return de_crossref(j["message"]), "CrossRef (DOI)"
    w = get_json(f"https://api.openalex.org/works/https://doi.org/{doi}")
    if w and w.get("title"):
        return de_openalex(w), "OpenAlex (DOI)"
    return None, None


def buscar_isbn(isbn):
    j = get_json("https://api.crossref.org/works", filter=f"isbn:{isbn}", rows=5)
    itens = (j or {}).get("message", {}).get("items", [])
    itens.sort(key=lambda i: i.get("type") not in ("book", "monograph", "edited-book", "reference-book"))
    if itens and itens[0].get("title"):
        return de_crossref(itens[0]), "CrossRef (ISBN)"
    j = get_json("https://openlibrary.org/api/books", bibkeys=f"ISBN:{isbn}", format="json", jscmd="data")
    if j:
        d = next(iter(j.values()))
        autores = d.get("authors") or []
        titulo = d.get("title", "") + (f": {d['subtitle']}" if d.get("subtitle") else "")
        ano = (re.findall(r"\d{4}", d.get("publish_date", "")) or [""])[0]
        if titulo:
            return {"sobrenome": capitalizar(autores[0]["name"].split()[-1]) if autores else "",
                    "titulo": suavizar_titulo(titulo), "ano": ano}, "Open Library (ISBN)"
    j = get_json("https://www.googleapis.com/books/v1/volumes", q=f"isbn:{isbn}")
    if j and j.get("items"):
        v = j["items"][0]["volumeInfo"]
        autores = v.get("authors") or []
        titulo = v.get("title", "") + (f": {v['subtitle']}" if v.get("subtitle") else "")
        return {"sobrenome": capitalizar(autores[0].split()[-1]) if autores else "",
                "titulo": suavizar_titulo(titulo), "ano": v.get("publishedDate", "")[:4]}, "Google Books (ISBN)"
    return None, None


# títulos que são, na verdade, nomes de arquivo de diagramação/edição ("Revista 54.vp", "cap1.indd"…)
LIXO_META = re.compile(r"microsoft (word|powerpoint)|untitled|^document|\.[a-z]{1,4}\d?$"
                       r"|^\s*$|^title$|^pdf$|^c[oó]pia de", re.I)
RE_ANO = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")


def ano_provavel(texto, nome):
    """Ano do nome do arquivo; senão, o ano mais frequente nas primeiras linhas do texto."""
    no_nome = RE_ANO.findall(nome)
    if no_nome:
        return no_nome[-1], "ano tirado do nome do arquivo"
    anos = RE_ANO.findall(texto[:3000])
    if anos:
        return max(set(anos), key=anos.count), "ano tirado do texto (confira)"
    return "", "sem ano"


# --------------------------------------------------------------- identificação
def identificar(path, info, online=True):
    """Recebe o resultado de arquivos.ler(); devolve sobrenome, título, ano, fonte, identificador,
    confiança (alta/media/baixa/nenhuma) e observações."""
    obs = []
    if info.get("erro"):
        return {"fonte": "erro", "confianca": "nenhuma", "observacao": info["erro"]}
    texto, meta = info.get("texto", ""), info.get("meta", {})
    if len(texto.strip()) < 200:
        obs.append("pouco ou nenhum texto (escaneado?)")
    contexto = texto + " " + Path(path).stem  # para conferir o que as bases devolvem

    if online:
        candidatos = []
        for doi in achar_dois(texto + " " + meta.get("subject", "") + " " + meta.get("keywords", ""))[:3]:
            dados, fonte = buscar_doi(doi)
            if dados:
                candidatos.append((dados, fonte, f"doi:{doi}"))
        for isbn in achar_isbns(texto)[:3]:
            dados, fonte = buscar_isbn(isbn)
            if dados:
                candidatos.append((dados, fonte, f"isbn:{isbn}"))
        if candidatos:
            # um DOI pode ser de uma obra citada: fica o candidato cujo título mais aparece no arquivo
            dados, fonte, ident = max(candidatos, key=lambda c: similaridade(c[0]["titulo"], contexto))
            sim = similaridade(dados["titulo"], contexto)
            autor_ok = bool(dados["sobrenome"]) and norm(dados["sobrenome"]) in norm(contexto)
            if sim >= 0.8 and autor_ok and dados["ano"]:
                conf = "alta"
            elif sim >= 0.5:
                conf = "media"
            else:
                conf = "baixa"
                obs.append(f"o título da base não confere com o arquivo ({sim:.0%}); pode ser obra citada")
            if not autor_ok:
                obs.append("sobrenome não encontrado no texto")
            if sim >= 0.5:
                return {**dados, "fonte": fonte, "identificador": ident, "confianca": conf,
                        "observacao": "; ".join(obs)}
            reserva = {**dados, "fonte": fonte, "identificador": ident, "confianca": "baixa",
                       "observacao": "; ".join(obs)}
        else:
            reserva = None
    else:
        reserva = None

    # Sem identificador confiável: metadados internos do arquivo
    titulo_meta = (meta.get("title") or "").strip()
    autor_meta = (meta.get("author") or "").strip()
    if titulo_meta and not LIXO_META.search(titulo_meta) and len(titulo_meta) > 5:
        autor1 = re.split(r"[;,&]| and | e ", autor_meta)[0].strip() if autor_meta else ""
        ano, nota = ano_provavel(texto, Path(path).stem)
        sim = similaridade(titulo_meta, contexto)
        conf = "media" if sim >= 0.8 and autor1 and ano else "baixa"
        obs.append(nota)
        return {"sobrenome": capitalizar(autor1.split()[-1]) if autor1 else "", "titulo": suavizar_titulo(titulo_meta),
                "ano": ano, "fonte": "metadados do arquivo", "identificador": "", "confianca": conf,
                "observacao": "; ".join(obs)}
    if reserva:
        return reserva

    # Último recurso: o maior texto da capa
    if info.get("titulo_fonte"):
        ano, nota = ano_provavel(texto, Path(path).stem)
        obs += ["título lido na capa (maior fonte)", nota]
        return {"sobrenome": "", "titulo": suavizar_titulo(info["titulo_fonte"]), "ano": ano,
                "fonte": "texto da capa", "identificador": "", "confianca": "baixa", "observacao": "; ".join(obs)}

    obs.append("sem identificador nem metadados úteis")
    return {"fonte": "nenhuma", "confianca": "nenhuma", "observacao": "; ".join(obs)}
