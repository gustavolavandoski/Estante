"""Normalização de texto e montagem de nomes de arquivo válidos ("Sobrenome - Título - Ano.ext")."""
import re
import unicodedata

INVALIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
LIMITE_TITULO = 100


def norm(s):
    """Minúsculas, sem acentos, só letras/números separados por espaço (para comparar textos)."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def similaridade(titulo, texto):
    """Fração das palavras significativas do título que aparecem no texto."""
    palavras = [w for w in norm(titulo).split() if len(w) > 3]
    if not palavras:
        return 0.0
    alvo = set(norm(texto).split())
    return sum(w in alvo for w in palavras) / len(palavras)


def limpar(s, limite=None):
    """Remove caracteres inválidos em nomes de arquivo; corta em `limite` sem partir palavras."""
    # ":" é inválido no Windows; vira ". " com maiúscula em seguida (subtítulo)
    s = re.sub(r": (\w)", lambda m: ". " + m.group(1).upper(), s or "").replace(":", " ")
    s = INVALIDOS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if limite and len(s) > limite:
        corte = s[:limite]
        s = corte.rsplit(" ", 1)[0] if " " in corte[limite // 2:] else corte
    s = s.rstrip(" .")
    return f"_{s}" if s.upper() in RESERVADOS else s


def nome_arquivo(sobrenome, titulo, ano, ext=".pdf", limite=LIMITE_TITULO):
    partes = [limpar(sobrenome) or "Sem autor", limpar(titulo, limite) or "Sem título"]
    if str(ano or "").strip():
        partes.append(limpar(str(ano)))
    return " - ".join(partes) + ext.lower()


def capitalizar(nome):
    """'SILVA' -> 'Silva'; nomes já com caixa mista ficam como estão."""
    return nome.title() if nome and nome.isupper() and len(nome) > 3 else nome


def suavizar_titulo(t):
    """Títulos inteiros em CAIXA ALTA viram 'Frase comum'."""
    t = re.sub(r"<[^>]+>", "", t or "").strip()
    letras = [c for c in t if c.isalpha()]
    if letras and sum(c.isupper() for c in letras) / len(letras) > 0.8:
        t = t.lower()
        t = re.sub(r"(^|[.:?!]\s+)(\w)", lambda m: m.group(1) + m.group(2).upper(), t)
    return t
