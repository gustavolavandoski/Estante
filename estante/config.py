"""Pasta de dados do usuário (configurações, temas e cache), sempre local."""
import json
import os
from pathlib import Path


def pasta_dados():
    base = os.environ.get("APPDATA") if os.name == "nt" else os.environ.get("XDG_CONFIG_HOME")
    p = Path(base or Path.home() / ".config") / "Estante"
    p.mkdir(parents=True, exist_ok=True)
    return p


def carregar(nome, padrao):
    arq = pasta_dados() / nome
    try:
        return json.loads(arq.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return padrao


def salvar(nome, dados):
    (pasta_dados() / nome).write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


class Cache:
    """Resultado da análise por hash do arquivo: reanalisar a mesma pasta é instantâneo."""

    def __init__(self):
        self.pasta = pasta_dados() / "cache"

    def _arq(self, h):
        return self.pasta / h[:2] / f"{h}.json"

    def obter(self, h):
        try:
            return json.loads(self._arq(h).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def guardar(self, h, dados):
        arq = self._arq(h)
        arq.parent.mkdir(parents=True, exist_ok=True)
        arq.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    def limpar(self):
        n = 0
        for arq in self.pasta.glob("*/*.json"):
            arq.unlink(); n += 1
        return n
