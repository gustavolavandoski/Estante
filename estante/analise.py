"""Análise de uma pasta: lê cada documento, identifica a obra, sugere tema e marca duplicatas."""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import arquivos, metadados, temas
from .config import Cache


@dataclass
class Item:
    caminho: str
    sha1: str = ""
    sobrenome: str = ""
    titulo: str = ""
    ano: str = ""
    tema: str = ""
    fonte: str = ""
    identificador: str = ""
    confianca: str = "nenhuma"
    anotacoes: int = 0
    observacao: str = ""
    incluir: bool = False
    duplicata_de: str = ""
    destino: str = ""  # relativo à pasta de destino; calculado em organizar.planejar
    extras: dict = field(default_factory=dict)

    @property
    def ext(self):
        return os.path.splitext(self.caminho)[1].lower()


CAMPOS_CACHE = ("sobrenome", "titulo", "ano", "fonte", "identificador", "confianca", "observacao")


def analisar_um(caminho, online, cache, lista_temas):
    h = arquivos.sha1(caminho)
    guardado = cache.obter(h)
    # um resultado offline guardado é refeito quando a consulta online é ligada
    if guardado and (guardado.get("online") or not online):
        info, r = guardado["info"], guardado["resultado"]
    else:
        info = arquivos.ler(caminho)
        r = metadados.identificar(caminho, info, online)
        info = {**info, "texto": info["texto"][:15000]}
        cache.guardar(h, {"online": online, "info": info, "resultado": r})
    it = Item(caminho=caminho, sha1=h, anotacoes=info.get("anotacoes", 0),
              **{k: r.get(k, "") or ("nenhuma" if k == "confianca" else "") for k in CAMPOS_CACHE})
    it.extras["impressao"] = info.get("impressao", "")
    it.tema = temas.sugerir(f"{it.titulo} {os.path.basename(caminho)} {info.get('texto', '')}", lista_temas)
    it.incluir = it.confianca in ("alta", "media")
    return it


def analisar_pasta(raiz, online=True, ignorar=(), progresso=None, cancelar=None, trabalhadores=6):
    """Analisa todos os documentos sob `raiz`. `progresso(n, total, caminho)` é chamado a cada arquivo;
    `cancelar()` interrompe a análise se devolver True."""
    caminhos = arquivos.listar(raiz, ignorar)
    cache, lista_temas = Cache(), temas.carregar()
    itens = []
    with ThreadPoolExecutor(max_workers=trabalhadores if online else max(2, os.cpu_count() or 2)) as ex:
        futuros = {ex.submit(analisar_um, c, online, cache, lista_temas): c for c in caminhos}
        for n, fut in enumerate(as_completed(futuros), 1):
            if cancelar and cancelar():
                ex.shutdown(wait=False, cancel_futures=True)
                break
            c = futuros[fut]
            try:
                itens.append(fut.result())
            except Exception as e:  # noqa: BLE001
                itens.append(Item(caminho=c, fonte="erro", observacao=f"erro: {str(e)[:100]}"))
            if progresso:
                progresso(n, len(caminhos), c)
    itens.sort(key=lambda i: i.caminho.lower())
    marcar_duplicatas(itens)
    return itens


def marcar_duplicatas(itens):
    """Em cada grupo de cópias da mesma obra, fica a com mais anotações (e, no empate, a com
    melhor identificação); as demais saem da seleção e são marcadas como duplicata."""
    ordem_conf = {"alta": 0, "media": 1, "baixa": 2, "nenhuma": 3}
    grupos = arquivos.agrupar_duplicatas([{"sha1": i.sha1, "impressao": i.extras.get("impressao")} for i in itens])
    for g in grupos:
        membros = [itens[k] for k in g]
        principal = min(membros, key=lambda i: (-i.anotacoes, ordem_conf.get(i.confianca, 4), len(i.caminho)))
        for m in membros:
            if m is principal:
                continue
            m.duplicata_de = principal.caminho
            nota = "cópia idêntica" if m.sha1 == principal.sha1 else "mesma obra"
            if m.anotacoes and m.sha1 != principal.sha1:
                # anotações diferentes nunca são descartadas: a cópia segue selecionada
                m.sobrenome, m.titulo, m.ano, m.tema = principal.sobrenome, principal.titulo, principal.ano, principal.tema
                nota = f"outra cópia anotada ({m.anotacoes} anotações), mesma obra"
            else:
                m.incluir = False
            m.observacao = "; ".join(x for x in (f"{nota} de {os.path.basename(principal.caminho)}", m.observacao) if x)
