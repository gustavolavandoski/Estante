"""Planejamento, execução (copiar ou mover), log e desfazer. Nada é apagado: no modo copiar os
originais ficam intactos; no modo mover, o log permite devolver cada arquivo ao lugar de origem."""
import csv
import datetime as dt
import os
import shutil

from .arquivos import EXTENSOES, longo, registrar_movido, salvar_hashes, sha1
from .nomes import limpar, nome_arquivo

POR_TEMA, POR_AUTOR, SEM_PASTAS = "tema", "autor", "nenhuma"
PASTA_LOGS = "_estante_logs"
PASTA_CONFIRMAR = "_A confirmar"  # identificação baixa/nenhuma: sai da bagunça, mas com o nome original
PASTA_DUPLICATAS = "_Duplicatas a excluir"  # cópias redundantes, separadas para você apagar (só no modo mover)
PASTA_FORMATOS = "_Outros formatos"  # .doc, .docx, .mobi…: não são lidos, mas saem da bagunça com o nome original
NAO_CONFIRMADOS = ("baixa", "nenhuma")
COLUNAS_CSV = ["incluir", "caminho", "sobrenome", "titulo", "ano", "tema", "destino", "confianca", "fonte",
               "identificador", "anotacoes", "duplicata_de", "observacao", "sha1"]


def pasta_de(item, modo):
    if modo == POR_TEMA:
        return limpar(item.tema) or "Outros"
    if modo == POR_AUTOR:
        return limpar(item.sobrenome) or "Sem autor"
    return ""


def planejar(itens, destino, modo):
    """Calcula o destino (relativo) de cada item selecionado, evitando nomes repetidos entre si
    e com arquivos diferentes que já existam na pasta de destino. Itens de confiança baixa ou
    nenhuma (não editados) vão para PASTA_CONFIRMAR e duplicatas para PASTA_DUPLICATAS, ambos
    com o nome original."""
    usados = set()
    por_caminho = {os.path.normcase(i.caminho): i for i in itens}
    for it in itens:
        it.destino = ""
    redundantes = [it for it in itens if it.incluir and _redundante(it, por_caminho)]
    for it in itens:
        if not it.incluir or it in redundantes:
            continue
        if it.ext not in EXTENSOES:
            rel = os.path.join(PASTA_FORMATOS, os.path.basename(it.caminho))
        elif it.confianca in NAO_CONFIRMADOS:
            rel = os.path.join(PASTA_CONFIRMAR, os.path.basename(it.caminho))
        else:
            rel = os.path.join(pasta_de(it, modo), nome_arquivo(it.sobrenome, it.titulo, it.ano, it.ext))
        it.destino = _livre(rel, it, destino, usados)
    # cópia idêntica que já ocupa o lugar planejado para o principal fica onde está; quem sobra é o principal
    for d in itens:
        p = por_caminho.get(os.path.normcase(d.duplicata_de)) if d.duplicata_de else None
        if (p and p.destino and p.sha1 == d.sha1 and p not in redundantes
                and os.path.normcase(os.path.join(destino, p.destino)) == os.path.normcase(d.caminho)):
            p.destino = ""
            redundantes.append(p)
            if d in redundantes:
                redundantes.remove(d)
                d.destino = ""  # sem destino: não é tocado
    for it in redundantes:
        it.destino = _livre(os.path.join(PASTA_DUPLICATAS, os.path.basename(it.caminho)), it, destino, usados, estrito=True)


def _redundante(it, por_caminho):
    """Duplicata a separar? Cópias com anotações próprias (conteúdo diferente) nunca são."""
    if not it.duplicata_de:
        return False
    p = por_caminho.get(os.path.normcase(it.duplicata_de))
    return not (it.anotacoes and p and p.sha1 != it.sha1)


def _livre(rel, it, destino, usados, estrito=False):
    """`rel` ou "rel (2)", "rel (3)"… o primeiro que não colida. `estrito`: qualquer arquivo existente colide."""
    base, n = os.path.splitext(rel)[0], 2
    proprio = os.path.normcase(os.path.abspath(it.caminho))
    while rel.lower() in usados or (os.path.normcase(os.path.abspath(os.path.join(destino, rel))) != proprio
                                    and _ocupado(os.path.join(destino, rel), None if estrito else it.sha1)):
        rel = f"{base} ({n}){it.ext}"
        n += 1
    usados.add(rel.lower())
    return rel


def _ocupado(caminho, h):
    """Há outro arquivo (conteúdo diferente) nesse caminho?"""
    if not os.path.exists(longo(caminho)):
        return False
    return not h or sha1(caminho) != h


def aplicar(itens, destino, operacao="copiar", progresso=None):
    """Copia ou move os itens selecionados. Devolve (caminho do log, feitos, mensagens de pulados)."""
    pasta_logs = os.path.join(destino, PASTA_LOGS)
    os.makedirs(longo(pasta_logs), exist_ok=True)
    log_path = os.path.join(pasta_logs, f"{operacao}_{dt.datetime.now():%Y%m%d_%H%M%S}.csv")
    alvo = [i for i in itens if i.incluir and i.destino]
    feitos, pulados, usados = 0, [], {i.destino.lower() for i in alvo}
    with open(longo(log_path), "w", newline="", encoding="utf-8-sig") as f:
        log = csv.writer(f, delimiter=";")
        log.writerow(["operacao", "origem", "destino", "sha1", "quando"])
        for n, it in enumerate(alvo, 1):
            if progresso:
                progresso(n, len(alvo), it.caminho)
            final = os.path.join(destino, it.destino)
            if os.path.normcase(os.path.abspath(final)) == os.path.normcase(os.path.abspath(it.caminho)):
                continue  # já está no lugar e com o nome certo
            if operacao != "mover" and it.destino.startswith(PASTA_DUPLICATAS + os.sep):
                pulados.append(f"duplicata (só é separada no modo mover): {it.caminho}"); continue
            if not os.path.exists(longo(it.caminho)):
                pulados.append(f"não existe mais: {it.caminho}"); continue
            if it.sha1 and sha1(it.caminho) != it.sha1:
                pulados.append(f"mudou desde a análise (analise de novo): {it.caminho}"); continue
            if os.path.exists(longo(final)):
                if sha1(final) != it.sha1:
                    pulados.append(f"destino ocupado por outro arquivo: {it.destino}"); continue
                if operacao != "mover":
                    pulados.append(f"já está no destino: {it.destino}"); continue
                # o idêntico já está no lugar: este é redundante
                final = os.path.join(destino, _livre(os.path.join(PASTA_DUPLICATAS, os.path.basename(it.caminho)),
                                                     it, destino, usados, estrito=True))
            os.makedirs(longo(os.path.dirname(final)), exist_ok=True)
            if operacao == "mover":
                shutil.move(longo(it.caminho), longo(final))
                registrar_movido(it.caminho, final, it.sha1)
            else:
                shutil.copy2(longo(it.caminho), longo(final))
                if it.sha1 and sha1(final) != it.sha1:
                    os.remove(longo(final))
                    pulados.append(f"falha na verificação da cópia: {it.caminho}"); continue
            feitos += 1
            log.writerow([operacao, it.caminho, final, it.sha1, dt.datetime.now().isoformat(timespec="seconds")])
            f.flush()
    salvar_hashes()
    return log_path, feitos, pulados


def desfazer(log_path):
    """Cópias: apaga só as que continuam idênticas ao original. Movidos: voltam para a origem.
    Pastas que ficarem vazias no destino são removidas. Devolve (desfeitos, mensagens)."""
    with open(longo(log_path), encoding="utf-8-sig") as f:
        regs = list(csv.DictReader(f, delimiter=";"))
    feitos, avisos, pastas = 0, [], set()
    for r in reversed(regs):
        d, o = r["destino"], r["origem"]
        if not os.path.exists(longo(d)):
            avisos.append(f"não encontrado: {d}"); continue
        if r["operacao"] == "mover":
            if os.path.exists(longo(o)):
                avisos.append(f"a origem já existe, mantido: {d}"); continue
            os.makedirs(longo(os.path.dirname(o)), exist_ok=True)
            shutil.move(longo(d), longo(o))
        else:
            if r["sha1"] and sha1(d) != r["sha1"]:
                avisos.append(f"cópia alterada depois (talvez anotada), mantida: {d}"); continue
            os.remove(longo(d))
        feitos += 1
        pastas.add(os.path.dirname(d))
    for p in sorted(pastas, key=len, reverse=True):
        try:
            os.rmdir(longo(p))
        except OSError:
            pass
    if not avisos:
        os.replace(longo(log_path), longo(log_path[:-4] + "_desfeito.csv"))
    return feitos, avisos


# ------------------------------------------------------------- CSV (edição em planilha)
def exportar_csv(itens, caminho):
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_CSV, delimiter=";")
        w.writeheader()
        for it in itens:
            w.writerow({c: ("S" if it.incluir else "N") if c == "incluir" else getattr(it, c) for c in COLUNAS_CSV})


def importar_csv(itens, caminho):
    """Atualiza os itens (casados pelo caminho) com as colunas editáveis do CSV. Devolve quantos mudaram."""
    por_caminho = {os.path.normcase(i.caminho): i for i in itens}
    mudou = 0
    with open(caminho, encoding="utf-8-sig") as f:
        amostra = f.read(2048); f.seek(0)
        sep = ";" if amostra.count(";") >= amostra.count(",") else ","
        for l in csv.DictReader(f, delimiter=sep):
            it = por_caminho.get(os.path.normcase(l.get("caminho", "")))
            if not it:
                continue
            novo = {"incluir": l.get("incluir", "").strip().upper() in ("S", "SIM", "X", "1", "TRUE"),
                    **{k: l.get(k, getattr(it, k)) for k in ("sobrenome", "titulo", "ano", "tema")}}
            if any(getattr(it, k) != v for k, v in novo.items()):
                for k, v in novo.items():
                    setattr(it, k, v)
                mudou += 1
    return mudou
