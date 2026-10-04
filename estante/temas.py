"""Classificação por tema com palavras-chave (expressões regulares) editáveis pelo usuário.
Os temas ficam em temas.json, na pasta de dados; o arquivo é criado com os temas abaixo como exemplo."""
import re

from . import config

ARQUIVO = "temas.json"
OUTROS = "Outros"

PADRAO = {
    "Inteligência": r"intelig[eê]ncia|intelligence|espionag|counterintel|contraintel|\bosint|\bhumint|\bsigint",
    "Guerra e Defesa Cibernética": r"cyber ?war|ciberguerra|guerra ciber|cyber ?power|ciberdefesa|defesa ciber|"
                                   r"cyber ?defen|cyberspace|ciberespa[cç]o|cyber operations|cyber ?security|"
                                   r"ciberseguran|seguran[cç]a ciber",
    "Crimes Cibernéticos e Segurança da Informação": r"cyber ?crime|cibercrim|crimes? (cibern|digita|inform)|hacker|"
                                                     r"malware|ransomware|seguran[cç]a da informa|information security",
    "Guerra Híbrida e Zona Cinzenta": r"hybrid war|guerra h[ií]brida|guerre hybride|gr[ae]y zone|zona cinzenta",
    "Operações Psicológicas e Guerra de Informação": r"psycholog\w* operation|opera[cç][oõ]es psicol|psyop|"
                                                     r"disinformation|desinforma|information war|propagand",
    "Terrorismo": r"terroris|insurg|counterinsurg|contrainsurg",
    "Geopolítica": r"geopol[ií]tic|mackinder|spykman|heartland",
    "Segurança Internacional e Teoria de RI": r"international security|seguran[cç]a internacional|"
                                              r"international relations|rela[cç][oõ]es internacionais|realism|"
                                              r"realismo|securitiza|balance of power",
    "Defesa e Estratégia": r"defesa nacional|national defen|pol[ií]tica de defesa|estrat[eé]gia nacional|"
                           r"for[cç]as armadas|armed forces|military strategy|estrat[eé]gia militar",
    "Inteligência Artificial": r"intelig[eê]ncia artificial|artificial intelligence|machine learning|"
                               r"aprendizado de m[aá]quina|\bllm",
    "Tecnologia e Sociedade": r"actor.network|ator.rede|platform capitalism|capitalismo de plataforma|"
                              r"soberania digital|digital sovereignty|sociedade da informa",
    "Metodologia": r"metodolog|methodolog|process tracing|projeto de pesquisa|research design|"
                   r"qualitative research|pesquisa qualitativa",
}


def carregar():
    temas = config.carregar(ARQUIVO, None)
    if not isinstance(temas, dict) or not temas:
        temas = dict(PADRAO)
        config.salvar(ARQUIVO, temas)
    return temas


def caminho():
    carregar()
    return config.pasta_dados() / ARQUIVO


def sugerir(texto, temas):
    """Tema com mais ocorrências das palavras-chave; '' se nenhum casar."""
    t = (texto or "").lower()
    melhor, pontos = "", 0
    for tema, rx in temas.items():
        try:
            n = len(re.findall(rx, t))
        except re.error:
            continue
        if n > pontos:
            melhor, pontos = tema, n
    return melhor
