"""Planejamento, execução (copiar ou mover), log e desfazer. Nada é apagado: no modo copiar os
originais ficam intactos; no modo mover, o log permite devolver cada arquivo ao lugar de origem."""
import csv
import datetime as dt
import os
import shutil

from .arquivos import longo, sha1
from .nomes import limpar, nome_arquivo

POR_TEMA, POR_AUTOR, SEM_PASTAS = "tema", "autor", "nenhuma"
PASTA_LOGS = "_estante_logs"
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
    e com arquivos diferentes que já existam na pasta de destino."""
    usados = set()
    for it in itens:
        it.destino = ""
        if not it.incluir:
            continue
        nome = nome_arquivo(it.sobrenome, it.titulo, it.ano, it.ext)
        rel = os.path.join(pasta_de(it, modo), nome)
        base, n = rel[: -len(it.ext)], 2
        while rel.lower() in usados or _ocupado(os.path.join(destino, rel), it.sha1):
            rel = f"{base} ({n}){it.ext}"
            n += 1
        usados.add(rel.lower())
        it.destino = rel


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
    feitos, pulados = 0, []
    with open(longo(log_path), "w", newline="", encoding="utf-8-sig") as f:
        log = csv.writer(f, delimiter=";")
        log.writerow(["operacao", "origem", "destino", "sha1", "quando"])
        for n, it in enumerate(alvo, 1):
            if progresso:
                progresso(n, len(alvo), it.caminho)
            final = os.path.join(destino, it.destino)
            if os.path.normcase(os.path.abspath(final)) == os.path.normcase(os.path.abspath(it.caminho)):
                continue  # já está no lugar e com o nome certo
            if not os.path.exists(longo(it.caminho)):
                pulados.append(f"não existe mais: {it.caminho}"); continue
            if it.sha1 and sha1(it.caminho) != it.sha1:
                pulados.append(f"mudou desde a análise (analise de novo): {it.caminho}"); continue
            if os.path.exists(longo(final)):
                if sha1(final) == it.sha1:
                    pulados.append(f"já está no destino: {it.destino}")
                else:
                    pulados.append(f"destino ocupado por outro arquivo: {it.destino}")
                continue
            os.makedirs(longo(os.path.dirname(final)), exist_ok=True)
            if operacao == "mover":
                shutil.move(longo(it.caminho), longo(final))
            else:
                shutil.copy2(longo(it.caminho), longo(final))
                if it.sha1 and sha1(final) != it.sha1:
                    os.remove(longo(final))
                    pulados.append(f"falha na verificação da cópia: {it.caminho}"); continue
            feitos += 1
            log.writerow([operacao, it.caminho, final, it.sha1, dt.datetime.now().isoformat(timespec="seconds")])
            f.flush()
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
