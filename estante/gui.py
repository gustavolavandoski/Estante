"""Interface desktop (Tkinter): escolher pastas, analisar, revisar/editar na tabela, aplicar e desfazer."""
import glob
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__, arquivos, config, metadados, organizar, temas
from .analise import analisar_pasta, pastas_ignoradas

COLUNAS = [  # (id, título, largura, editável)
    ("incluir", "☐", 36, False),
    ("confianca", "Confiança", 100, False),
    ("sobrenome", "Sobrenome", 130, True),
    ("titulo", "Título", 300, True),
    ("ano", "Ano", 56, True),
    ("tema", "Tema", 190, True),
    ("destino", "Novo nome do arquivo", 340, False),
    ("anotacoes", "Nota", 50, False),
    ("arquivo", "Arquivo atual", 240, False),
]
ROTULO_CONF = {"alta": "● Alta", "media": "● Média", "baixa": "● Baixa", "nenhuma": "● Nenhuma", "manual": "● Manual"}
FILTROS = [  # (id, rótulo do botão)
    ("todos", "Todos"), ("baixa", "Baixa confiança"), ("sem_tema", "Sem tema"),
    ("anotacao", "Com anotação"), ("duplicatas", "Duplicatas"),
]
SEM_TEMA = "__sem_tema__"  # linha "Outros" na revisão de temas
MODOS = {"Pastas por tema": organizar.POR_TEMA, "Pastas por autor": organizar.POR_AUTOR,
         "Sem subpastas": organizar.SEM_PASTAS}
PERFIL_PADRAO = "Padrão"

# cores do layout
FUNDO, CARTAO, BORDA = "#f3f1ec", "#ffffff", "#dcd8cf"
VERDE, VERDE_ESCURO, VERDE_CLARO = "#1f5c55", "#174a44", "#dcebe7"
TEXTO, TEXTO_2, ALERTA = "#22262a", "#6b6f73", "#a86a00"
FONTE = "Segoe UI" if sys.platform == "win32" else "TkDefaultFont"
TINTAS = {"baixa": "#fdf3e7", "nenhuma": "#fbe9e8", "duplicata": "#eeeeec", "par": "#ffffff", "impar": "#faf9f6"}


def identidade_na_barra(janela, icone):
    """Windows: grava na janela a identidade do Estante e o ícone que a barra de tarefas deve usar.
    Sem isso, com o Python da Microsoft Store, a barra mostra o ícone do Python."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD), ("r3", wintypes.WORD),
                    ("valor", ctypes.c_void_p), ("extra", ctypes.c_void_p)]

    def guid(texto):
        g = GUID()
        ctypes.oledll.ole32.CLSIDFromString(texto, ctypes.byref(g))
        return g

    chave = guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}")  # PKEY_AppUserModel_*
    store = ctypes.c_void_p()
    hwnd = int(janela.wm_frame(), 16)
    ctypes.oledll.shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")),
                                                      ctypes.byref(store))
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    set_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                   ctypes.POINTER(PROPVARIANT))(vtbl[6])
    release = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    textos = []  # mantém as strings vivas até o fim das chamadas
    for pid, texto in ((5, "Estante.Organizador"), (3, f"{icone},0"), (4, "Estante"),
                       (2, f'"{pythonw}" -m estante')):
        textos.append(ctypes.create_unicode_buffer(texto))
        pv = PROPVARIANT(vt=31, valor=ctypes.cast(textos[-1], ctypes.c_void_p))  # VT_LPWSTR
        set_value(store, ctypes.byref(PROPERTYKEY(chave, pid)), ctypes.byref(pv))
    release(store)


def abrir_no_sistema(caminho):
    if sys.platform == "win32":
        os.startfile(caminho)  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", caminho])


class Segmentado(tk.Frame):
    """Botões lado a lado em que só um fica ativo (Copiar | Mover)."""

    def __init__(self, mestre, var, opcoes):
        super().__init__(mestre, bg=BORDA, padx=1, pady=1)
        self.var, self.rotulos = var, {}
        for valor, texto in opcoes:
            r = tk.Label(self, text=texto, font=(FONTE, 10), padx=16, pady=6, cursor="hand2")
            r.pack(side="left", padx=(0, 1))
            r.bind("<Button-1>", lambda e, v=valor: var.set(v))
            self.rotulos[valor] = r
        var.trace_add("write", lambda *_: self.pintar())
        self.pintar()

    def pintar(self):
        for valor, r in self.rotulos.items():
            ativo = valor == self.var.get()
            r.configure(bg=VERDE if ativo else CARTAO, fg="white" if ativo else TEXTO,
                        font=(FONTE, 10, "bold" if ativo else "normal"))


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Estante {__version__} — organizador de livros, artigos e textos")
        pasta = os.path.dirname(os.path.abspath(__file__))
        try:
            self._icone = tk.PhotoImage(file=os.path.join(pasta, "icone.png"))
            self.iconphoto(True, self._icone)  # vale também para as janelas de diálogo
            if sys.platform == "win32":
                self.iconbitmap(default=os.path.join(pasta, "icone.ico"))
        except tk.TclError:
            pass
        if sys.platform == "win32":
            try:
                self.update_idletasks()  # a janela precisa existir para receber a identidade
                identidade_na_barra(self, os.path.join(pasta, "icone.ico"))
            except OSError:
                pass
        self.geometry("1400x900")
        self.minsize(1000, 600)
        self.configure(bg=FUNDO)
        self.itens = []
        self.fila = queue.Queue()
        self.ocupado = False
        self.cancelar = False
        self.editor = None
        self._inicio = 0.0
        cfg = config.carregar("config.json", {})
        self.v_origem = tk.StringVar(value=cfg.get("origem", ""))
        self.v_destino = tk.StringVar(value=cfg.get("destino", ""))
        self.v_modo = tk.StringVar(value=cfg.get("modo", "Pastas por tema"))
        self.v_operacao = tk.StringVar(value=cfg.get("operacao", "copiar"))
        # perfis: cada um guarda as bases abertas, o e-mail, a chave e a lista de temas
        self.perfis = cfg.get("perfis") or {PERFIL_PADRAO: {
            "online": cfg.get("online", True), "email": cfg.get("email", ""),
            "chave_openalex": cfg.get("chave_openalex", ""), "temas": temas.ARQUIVO_PADRAO}}
        self.perfil = cfg.get("perfil") if cfg.get("perfil") in self.perfis else next(iter(self.perfis))
        self.v_perfil = tk.StringVar(value=self.perfil)
        p = self.perfis[self.perfil]
        temas.ARQUIVO = p.get("temas", temas.ARQUIVO_PADRAO)
        self.v_online = tk.BooleanVar(value=p.get("online", True))
        self.v_email = tk.StringVar(value=p.get("email", ""))
        self.v_chave = tk.StringVar(value=p.get("chave_openalex", ""))
        self.v_filtro = tk.StringVar(value="todos")
        self.v_busca = tk.StringVar()
        self.v_status = tk.StringVar(value="Escolha a pasta a analisar e clique em Analisar.")
        self.v_tempo = tk.StringVar()
        self._estilos()
        self._menus()
        self._montar()
        self.v_modo.trace_add("write", lambda *_: self.replanejar())
        self.v_destino.trace_add("write", lambda *_: self.replanejar())
        self.v_filtro.trace_add("write", lambda *_: self.preencher())
        self.v_busca.trace_add("write", lambda *_: self.preencher())
        self.v_online.trace_add("write", lambda *_: self._opcoes_resumo())
        self.v_operacao.trace_add("write", lambda *_: self._dica_operacao())
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.after(100, self._ler_fila)

    # ------------------------------------------------------------------ layout
    def _estilos(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=(FONTE, 10), background=FUNDO, foreground=TEXTO)
        s.configure("TCombobox", fieldbackground=CARTAO, background=CARTAO, bordercolor=BORDA, arrowcolor=TEXTO,
                    padding=5)
        s.map("TCombobox", fieldbackground=[("readonly", CARTAO)], selectbackground=[("readonly", CARTAO)],
              selectforeground=[("readonly", TEXTO)])
        s.configure("TEntry", fieldbackground=CARTAO, bordercolor=BORDA, padding=6)
        s.configure("Treeview", background=CARTAO, fieldbackground=CARTAO, foreground=TEXTO, rowheight=32,
                    bordercolor=BORDA, borderwidth=0)
        s.configure("Treeview.Heading", background="#f7f6f2", foreground=TEXTO_2, relief="flat",
                    font=(FONTE, 10), padding=(6, 8))
        s.map("Treeview", background=[("selected", VERDE_CLARO)], foreground=[("selected", TEXTO)])
        s.map("Treeview.Heading", background=[("active", "#efede7")])
        s.configure("Horizontal.TProgressbar", troughcolor="#e4e1da", background=VERDE, bordercolor=FUNDO,
                    lightcolor=VERDE, darkcolor=VERDE, thickness=10)
        s.configure("Vertical.TScrollbar", background="#e4e1da", troughcolor=CARTAO, bordercolor=CARTAO, arrowcolor=TEXTO_2)
        s.configure("Horizontal.TScrollbar", background="#e4e1da", troughcolor=CARTAO, bordercolor=CARTAO, arrowcolor=TEXTO_2)

    def _menus(self):
        barra = tk.Menu(self)
        m = tk.Menu(barra, tearoff=False)
        m.add_command(label="Importar CSV…", command=self.importar)
        m.add_command(label="Exportar CSV…", command=self.exportar)
        m.add_separator()
        m.add_command(label="Sair", command=self.fechar)
        barra.add_cascade(label="Arquivo", menu=m)

        m = tk.Menu(barra, tearoff=False)
        m.add_command(label="Aplicar marcados", command=self.aplicar)
        m.add_command(label="Desfazer última aplicação", command=self.desfazer_ultima)
        m.add_command(label="Desfazer uma aplicação anterior…", command=self.desfazer)
        m.add_separator()
        m.add_command(label="Revisar temas…", command=self.revisar_temas)
        m.add_command(label="Editar temas…", command=self.editar_temas)
        barra.add_cascade(label="Ferramentas", menu=m)

        self.m_perfis = tk.Menu(barra, tearoff=False)
        barra.add_cascade(label="Perfis", menu=self.m_perfis)
        self._menu_perfis()

        m = tk.Menu(barra, tearoff=False)
        m.add_command(label="Como usar", command=self.como_usar)
        m.add_command(label="Ver log da última operação", command=self.ver_log)
        m.add_command(label="Como obter a chave do OpenAlex", command=self.ajuda_chave)
        m.add_separator()
        m.add_command(label=f"Sobre o Estante (versão {__version__})", command=self.sobre)
        barra.add_cascade(label="Ajuda", menu=m)
        self.configure(menu=barra)

    def _menu_perfis(self):
        m = self.m_perfis
        m.delete(0, "end")
        for nome in self.perfis:
            m.add_radiobutton(label=nome, value=nome, variable=self.v_perfil, command=lambda n=nome: self.trocar_perfil(n))
        m.add_separator()
        m.add_checkbutton(label="Consultar bases abertas (CrossRef, OpenAlex…)", variable=self.v_online)
        m.add_command(label="E-mail do CrossRef…", command=self.pedir_email)
        m.add_command(label="Chave do OpenAlex…", command=self.pedir_chave)
        m.add_command(label="Lista de temas…", command=self.editar_temas)
        m.add_separator()
        m.add_command(label="Novo perfil…", command=self.novo_perfil)
        m.add_command(label="Excluir este perfil", command=self.excluir_perfil)

    def _cartao(self, mestre, **pack):
        c = tk.Frame(mestre, bg=CARTAO, highlightthickness=1, highlightbackground=BORDA, padx=16, pady=12)
        c.pack(fill="x", **pack)
        return c

    def _link(self, mestre, texto, comando, cor=VERDE, negrito=True):
        r = tk.Label(mestre, text=texto, fg=cor, bg=mestre["bg"], cursor="hand2",
                     font=(FONTE, 10, "bold" if negrito else "normal"))
        r.bind("<Button-1>", lambda e: comando())
        return r

    def _montar(self):
        corpo = tk.Frame(self, bg=FUNDO, padx=20, pady=14)
        corpo.pack(fill="both", expand=True)

        # pastas
        pastas = self._cartao(corpo)
        for linha, (rotulo, var) in enumerate([("Pasta a analisar", self.v_origem), ("Pasta de destino", self.v_destino)]):
            tk.Label(pastas, text=rotulo, bg=CARTAO, fg=TEXTO, font=(FONTE, 10)).grid(
                row=linha, column=0, sticky="w", padx=(0, 16), pady=5)
            ttk.Entry(pastas, textvariable=var).grid(row=linha, column=1, sticky="ew", pady=5)
            tk.Button(pastas, text="🗀  Escolher…", command=lambda v=var: self.escolher_pasta(v), bg=CARTAO, fg=TEXTO,
                      relief="solid", bd=1, highlightthickness=0, activebackground="#f2f1ed", font=(FONTE, 10),
                      padx=12, pady=3, cursor="hand2").grid(row=linha, column=2, padx=(12, 0), pady=5)
        pastas.columnconfigure(1, weight=1)

        # organizar / copiar ou mover / opções avançadas
        opcoes = tk.Frame(corpo, bg=FUNDO)
        opcoes.pack(fill="x", pady=(14, 0))
        tk.Label(opcoes, text="Organizar", bg=FUNDO, font=(FONTE, 10)).pack(side="left", padx=(0, 8))
        ttk.Combobox(opcoes, textvariable=self.v_modo, values=list(MODOS), state="readonly", width=17).pack(side="left")
        Segmentado(opcoes, self.v_operacao, [("copiar", "Copiar"), ("mover", "Mover")]).pack(side="left", padx=(20, 10))
        self.l_dica = tk.Label(opcoes, bg=FUNDO, fg=TEXTO_2, font=(FONTE, 9))
        self.l_dica.pack(side="left")
        self.l_opcoes = tk.Label(opcoes, bg=FUNDO, fg=TEXTO_2, font=(FONTE, 9))
        self.l_opcoes.pack(side="right")
        self._link(opcoes, "›  Opções avançadas", self.opcoes_avancadas).pack(side="right")

        # passos
        passos = tk.Frame(corpo, bg=FUNDO)
        passos.pack(fill="x", pady=(14, 12))
        self.b_analisar = tk.Button(passos, text="①  Analisar", command=self.analisar, bg=CARTAO, fg=VERDE,
                                    activebackground="#eef5f3", activeforeground=VERDE, relief="solid", bd=1,
                                    highlightthickness=0, font=(FONTE, 11, "bold"), padx=18, pady=7, cursor="hand2")
        self.b_analisar.pack(side="left")
        tk.Label(passos, text="›", bg=FUNDO, fg=TEXTO_2, font=(FONTE, 14)).pack(side="left", padx=12)
        self.b_aplicar = tk.Button(passos, text="②  Aplicar", command=self.aplicar, bg=VERDE, fg="white",
                                   activebackground=VERDE_ESCURO, activeforeground="white", relief="flat", bd=0,
                                   highlightthickness=0, font=(FONTE, 11, "bold"), padx=20, pady=8, cursor="hand2",
                                   disabledforeground="#cfe0dc")
        self.b_aplicar.pack(side="left")
        self._link(passos, "↶  Desfazer última aplicação", self.desfazer_ultima, cor=TEXTO, negrito=False).pack(
            side="left", padx=24)
        tk.Frame(corpo, bg=BORDA, height=1).pack(fill="x", pady=(0, 12))

        # filtros, marcar todos, busca
        barra = tk.Frame(corpo, bg=FUNDO)
        barra.pack(fill="x", pady=(0, 10))
        self.chips = {}
        for fid, rotulo in FILTROS:
            c = tk.Label(barra, text=rotulo, font=(FONTE, 10, "bold"), padx=12, pady=5, cursor="hand2",
                         highlightthickness=1)
            c.pack(side="left", padx=(0, 8))
            c.bind("<Button-1>", lambda e, f=fid: self.v_filtro.set(f))
            self.chips[fid] = (c, rotulo)
        self.v_filtro.trace_add("write", lambda *_: self._pintar_chips())
        busca = tk.Frame(barra, bg=CARTAO, highlightthickness=1, highlightbackground=BORDA)
        busca.pack(side="right")
        tk.Label(busca, text="⌕", bg=CARTAO, fg=TEXTO_2, font=(FONTE, 11)).pack(side="left", padx=(8, 0))
        e = tk.Entry(busca, textvariable=self.v_busca, relief="flat", bd=0, bg=CARTAO, font=(FONTE, 10), width=32)
        e.pack(side="left", padx=6, pady=6)
        dica = tk.Label(busca, text="Buscar por autor, título ou tema", bg=CARTAO, fg="#9a9ea2", font=(FONTE, 10))
        dica.place(in_=e, x=0, y=0)
        dica.bind("<Button-1>", lambda _: e.focus_set())
        self.v_busca.trace_add("write", lambda *_: dica.place_forget() if self.v_busca.get() else dica.place(in_=e, x=0, y=0))
        self._link(barra, "Desmarcar todos", lambda: self.marcar_todos(False)).pack(side="right", padx=(0, 18))
        self._link(barra, "Marcar todos", lambda: self.marcar_todos(True)).pack(side="right", padx=(0, 18))

        # tabela
        meio = tk.Frame(corpo, bg=CARTAO, highlightthickness=1, highlightbackground=BORDA)
        meio.pack(fill="both", expand=True)
        self.tabela = ttk.Treeview(meio, columns=[c[0] for c in COLUNAS], show="headings", selectmode="extended")
        for cid, titulo, largura, _ in COLUNAS:
            comando = (lambda: self.marcar_todos(not self._todos_marcados())) if cid == "incluir" else \
                (lambda c=cid: self.ordenar(c))
            self.tabela.heading(cid, text=titulo, anchor="center" if cid in ("incluir", "ano", "anotacoes") else "w",
                                command=comando)
            self.tabela.column(cid, width=largura, minwidth=30, stretch=cid in ("titulo", "destino", "arquivo"),
                               anchor="center" if cid in ("incluir", "ano", "anotacoes") else "w")
        for tag, cor in TINTAS.items():
            self.tabela.tag_configure(tag, background=cor)
        sy = ttk.Scrollbar(meio, orient="vertical", command=self.tabela.yview)
        sx = ttk.Scrollbar(meio, orient="horizontal", command=self.tabela.xview)
        self.tabela.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.tabela.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        meio.rowconfigure(0, weight=1)
        meio.columnconfigure(0, weight=1)
        self.tabela.bind("<Button-1>", self._clique)
        self.tabela.bind("<Double-1>", self._duplo_clique)
        self.tabela.bind("<space>", lambda e: self.marcar(None))
        self.tabela.bind("<<TreeviewSelect>>", lambda e: self.mostrar_detalhes())
        self.tabela.bind("<Button-3>", self._menu)

        # detalhes do arquivo selecionado
        det = self._cartao(corpo, pady=(12, 0))
        self.l_det = {}
        for linha, rotulo in enumerate(("Original", "Destino", "Fonte")):
            tk.Label(det, text=rotulo, bg=CARTAO, fg=TEXTO_2, font=(FONTE, 9)).grid(row=linha, column=0, sticky="w",
                                                                                   padx=(0, 30))
            v = tk.Label(det, bg=CARTAO, anchor="w", font=("Consolas", 10, "bold" if rotulo == "Destino" else "normal"),
                         fg=VERDE if rotulo == "Destino" else TEXTO)
            v.grid(row=linha, column=1, sticky="ew")
            self.l_det[rotulo] = v
        det.columnconfigure(1, weight=1)
        bts = tk.Frame(det, bg=CARTAO)
        bts.grid(row=0, column=2, rowspan=3, sticky="e")
        for texto, comando in (("Editar nome", self.editar_nome), ("Abrir arquivo", self.abrir_arquivo)):
            tk.Button(bts, text=texto, command=comando, bg=CARTAO, fg=TEXTO, relief="solid", bd=1, highlightthickness=0,
                      activebackground="#f2f1ed", font=(FONTE, 10), padx=14, pady=5, cursor="hand2").pack(
                side="left", padx=(8, 0))

        # rodapé
        rodape = tk.Frame(self, bg="#ebe8e1", padx=20, pady=8)
        rodape.pack(fill="x", side="bottom")
        tk.Label(rodape, textvariable=self.v_status, bg="#ebe8e1", fg=TEXTO_2, font=(FONTE, 9)).pack(side="left")
        self.barra = ttk.Progressbar(rodape, length=200)
        self.barra.pack(side="right")
        tk.Label(rodape, textvariable=self.v_tempo, bg="#ebe8e1", fg=TEXTO_2, font=(FONTE, 9)).pack(side="right", padx=12)

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Abrir arquivo", command=self.abrir_arquivo)
        self.menu.add_command(label="Abrir pasta do arquivo", command=self.abrir_pasta)
        self.menu.add_separator()
        self.menu.add_command(label="Editar nome…", command=self.editar_nome)
        self.menu.add_command(label="Selecionar para organizar", command=lambda: self.marcar(True))
        self.menu.add_command(label="Tirar da seleção", command=lambda: self.marcar(False))
        self.menu.add_command(label="Definir tema dos selecionados…", command=self.tema_em_lote)

        self._dica_operacao()
        self._opcoes_resumo()
        self._pintar_chips()
        self._botao_aplicar()

    def _dica_operacao(self):
        self.l_dica.configure(text="Originais ficam intactos" if self.v_operacao.get() == "copiar"
                              else "Os arquivos saem da pasta original (dá para desfazer)")

    def _opcoes_resumo(self):
        self.l_opcoes.configure(text=f" · perfil {self.perfil} · bases abertas "
                                     f"{'ligadas' if self.v_online.get() else 'desligadas'}")

    def _pintar_chips(self):
        contagem = self._contagens()
        for fid, (c, rotulo) in self.chips.items():
            ativo = fid == self.v_filtro.get()
            n = contagem.get(fid)
            c.configure(text=f"{rotulo}  {n}" if n is not None else rotulo,
                        bg=TEXTO if ativo else CARTAO, fg="white" if ativo else TEXTO,
                        highlightbackground=TEXTO if ativo else BORDA)

    def _contagens(self):
        if not self.itens:
            return {}
        pdfs = [i for i in self.itens if not i.duplicata_de and i.ext in arquivos.EXTENSOES]
        return {"todos": len(self.itens),
                "baixa": sum(i.confianca in organizar.NAO_CONFIRMADOS for i in pdfs),
                "sem_tema": sum(not i.tema for i in pdfs),
                "anotacao": sum(bool(i.anotacoes) for i in self.itens),
                "duplicatas": sum(bool(i.duplicata_de) for i in self.itens)}

    def _botao_aplicar(self):
        n = sum(1 for i in self.itens if i.incluir and i.destino)
        self.b_aplicar.configure(text=f"②  Aplicar a {n} arquivo{'s' if n != 1 else ''} marcado{'s' if n != 1 else ''}"
                                 if self.itens else "②  Aplicar")

    # ------------------------------------------------------------- tabela
    def visiveis(self):
        f, busca = self.v_filtro.get(), self.v_busca.get().strip().lower()
        for k, it in enumerate(self.itens):
            if f == "baixa" and (it.confianca not in organizar.NAO_CONFIRMADOS or it.duplicata_de):
                continue
            if f == "sem_tema" and (it.tema or it.duplicata_de or it.ext not in arquivos.EXTENSOES):
                continue
            if f == "anotacao" and not it.anotacoes:
                continue
            if f == "duplicatas" and not it.duplicata_de:
                continue
            if busca and busca not in f"{it.caminho} {it.sobrenome} {it.titulo} {it.tema}".lower():
                continue
            yield k, it

    def valores(self, it):
        return ("☑" if it.incluir else "☐", ROTULO_CONF.get(it.confianca, it.confianca), it.sobrenome, it.titulo,
                it.ano, it.tema, os.path.basename(it.destino), f"✎ {it.anotacoes}" if it.anotacoes else "",
                os.path.basename(it.caminho))

    def tags(self, it, posicao):
        fundo = ("duplicata" if it.duplicata_de and not it.incluir else
                 it.confianca if it.confianca in ("baixa", "nenhuma") else "impar" if posicao % 2 else "par")
        return (fundo,)

    def preencher(self):
        self.tabela.delete(*self.tabela.get_children())
        n = 0
        for k, it in self.visiveis():
            self.tabela.insert("", "end", iid=str(k), values=self.valores(it), tags=self.tags(it, n))
            n += 1
        self._resumo(n)

    def atualizar_linhas(self, ks=None):
        for k in (ks if ks is not None else range(len(self.itens))):
            if self.tabela.exists(str(k)):
                it = self.itens[k]
                self.tabela.item(str(k), values=self.valores(it), tags=self.tags(it, self.tabela.index(str(k))))
        self._resumo()

    def _resumo(self, mostrados=None):
        self._pintar_chips()
        self._botao_aplicar()
        if not self.itens or self.ocupado:
            return
        c = self._contagens()
        sel = sum(i.incluir for i in self.itens)
        extra = f" · mostrando {mostrados}" if mostrados is not None and mostrados != len(self.itens) else ""
        self.v_status.set(f"{len(self.itens)} arquivos analisados · {sel} marcados · {c['baixa']} com baixa confiança · "
                          f"{c['sem_tema']} sem tema · {c['duplicatas']} duplicatas{extra}")

    def replanejar(self):
        if self.itens and not self.ocupado:
            organizar.planejar(self.itens, self.v_destino.get().strip(), MODOS.get(self.v_modo.get(), organizar.POR_TEMA))
            self.atualizar_linhas()

    def ordenar(self, col):
        chave = {"arquivo": lambda i: i.caminho.lower(), "anotacoes": lambda i: i.anotacoes,
                 "confianca": lambda i: ["alta", "media", "manual", "baixa", "nenhuma"].index(i.confianca)
                 if i.confianca in ("alta", "media", "manual", "baixa", "nenhuma") else 9}.get(
            col, lambda i: str(getattr(i, col, "")).lower())
        inverter = getattr(self, "_ordem", None) == col
        self._ordem = None if inverter else col
        self.itens.sort(key=chave, reverse=inverter)
        self.preencher()

    def selecionados(self):
        return [int(i) for i in self.tabela.selection()]

    def mostrar_detalhes(self):
        sel = self.selecionados()
        textos = {"Original": "", "Destino": "", "Fonte": ""}
        cor = TEXTO_2
        if len(sel) == 1:
            it = self.itens[sel[0]]
            destino = os.path.join(self.v_destino.get().strip(), it.destino) if it.destino else "— (não marcado)"
            nota = {"alta": "confiança alta", "media": "confiança média: confira o autor antes de aplicar",
                    "manual": "editado por você", "baixa": "confiança baixa: vai para a pasta de confirmação",
                    "nenhuma": "não identificado: vai para a pasta de confirmação"}.get(it.confianca, it.confianca)
            textos = {"Original": it.caminho, "Destino": destino,
                      "Fonte": " · ".join(x for x in (it.fonte, it.identificador, nota, it.observacao) if x)}
            cor = ALERTA if it.confianca in ("media", "baixa", "nenhuma") else TEXTO_2
        elif sel:
            textos["Original"] = f"{len(sel)} linhas selecionadas (botão direito para ações em lote)"
        for rotulo, v in self.l_det.items():
            v.configure(text=textos[rotulo][:160])
        self.l_det["Fonte"].configure(fg=cor, font=(FONTE, 9))

    # -------------------------------------------------------------- edição
    def _clique(self, e):
        self._fechar_editor(salvar=True)
        if self.tabela.identify_region(e.x, e.y) == "cell" and self.tabela.identify_column(e.x) == "#1":
            k = self.tabela.identify_row(e.y)
            if k:
                it = self.itens[int(k)]
                it.incluir = not it.incluir
                self.replanejar()
                return "break"
        return None

    def _duplo_clique(self, e):
        if self.tabela.identify_region(e.x, e.y) != "cell":
            return
        k, col = self.tabela.identify_row(e.y), int(self.tabela.identify_column(e.x)[1:]) - 1
        cid, _, _, editavel = COLUNAS[col]
        if cid == "arquivo":
            abrir_no_sistema(self.itens[int(k)].caminho)
        elif editavel:
            self._abrir_editor(int(k), cid)

    def _abrir_editor(self, k, cid):
        x, y, w, h = self.tabela.bbox(str(k), cid)
        it = self.itens[k]
        var = tk.StringVar(value=getattr(it, cid))
        if cid == "tema":
            opcoes = sorted(set(temas.carregar()) | {i.tema for i in self.itens if i.tema} | {temas.OUTROS})
            ed = ttk.Combobox(self.tabela, textvariable=var, values=opcoes)
        else:
            ed = ttk.Entry(self.tabela, textvariable=var)
        ed.place(x=x, y=y, width=max(w, 160), height=h)
        ed.focus_set()
        ed.select_range(0, "end")
        ed.bind("<Return>", lambda e: self._fechar_editor(True))
        ed.bind("<Escape>", lambda e: self._fechar_editor(False))
        ed.bind("<Tab>", lambda e: self._fechar_editor(True, proximo=True))
        self.editor = (ed, var, k, cid)

    def _fechar_editor(self, salvar, proximo=False):
        if not self.editor:
            return "break"
        ed, var, k, cid = self.editor
        self.editor = None
        ed.destroy()
        it = self.itens[k]
        novo = var.get().strip()
        if salvar and novo != getattr(it, cid):
            setattr(it, cid, novo)
            it.confianca, it.incluir = "manual", True  # revisado por você
            self.replanejar()
        if proximo:
            ids = [c[0] for c in COLUNAS if c[3]]
            seguinte = ids[(ids.index(cid) + 1) % len(ids)]
            self.after(10, lambda: self._abrir_editor(k, seguinte))
        self.tabela.focus_set()
        return "break"

    def editar_nome(self):
        """Sobrenome, título, ano e tema do arquivo selecionado, numa janela só."""
        ks = self.selecionados()
        if len(ks) != 1:
            messagebox.showinfo("Estante", "Selecione um arquivo na tabela.")
            return
        it = self.itens[ks[0]]
        janela = tk.Toplevel(self, bg=CARTAO, padx=16, pady=14)
        janela.title("Editar nome")
        janela.transient(self)
        tk.Label(janela, text=os.path.basename(it.caminho), bg=CARTAO, fg=TEXTO_2, font=(FONTE, 9)).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        vars_ = {}
        for linha, (cid, rotulo) in enumerate((("sobrenome", "Sobrenome"), ("titulo", "Título"), ("ano", "Ano"),
                                               ("tema", "Tema")), 1):
            tk.Label(janela, text=rotulo, bg=CARTAO).grid(row=linha, column=0, sticky="w", padx=(0, 12), pady=4)
            vars_[cid] = tk.StringVar(value=getattr(it, cid))
            if cid == "tema":
                opcoes = sorted(set(temas.carregar()) | {i.tema for i in self.itens if i.tema} | {temas.OUTROS})
                w = ttk.Combobox(janela, textvariable=vars_[cid], values=opcoes, width=60)
            else:
                w = ttk.Entry(janela, textvariable=vars_[cid], width=62)
            w.grid(row=linha, column=1, sticky="ew", pady=4)

        def ok(*_):
            novos = {c: v.get().strip() for c, v in vars_.items()}
            if any(getattr(it, c) != v for c, v in novos.items()):
                for c, v in novos.items():
                    setattr(it, c, v)
                it.confianca, it.incluir = "manual", True
            janela.destroy()
            self.replanejar()
            self.mostrar_detalhes()

        tk.Button(janela, text="Salvar", command=ok, bg=VERDE, fg="white", activebackground=VERDE_ESCURO,
                  activeforeground="white", relief="flat", padx=16, pady=4, font=(FONTE, 10, "bold")).grid(
            row=6, column=1, sticky="e", pady=(10, 0))
        janela.bind("<Return>", ok)
        janela.grab_set()

    def marcar(self, valor):
        ks = self.selecionados()
        for k in ks:
            self.itens[k].incluir = (not self.itens[k].incluir) if valor is None else valor
        self.replanejar()
        return "break"

    def _todos_marcados(self):
        return all(it.incluir for _, it in self.visiveis())

    def marcar_todos(self, valor):
        """Marca/desmarca as linhas mostradas (respeita filtro e busca)."""
        for _, it in self.visiveis():
            it.incluir = valor
        self.replanejar()
        self.tabela.heading("incluir", text="☑" if valor and self.itens else "☐")
        if valor:
            self.v_status.set(self.v_status.get() + self._especiais(" · {n} irão para '{pasta}'"))

    def _especiais(self, molde):
        """Texto com quantos itens planejados vão para as pastas padrão (confirmação, duplicatas, formatos)."""
        partes = []
        for pasta in (organizar.PASTA_CONFIRMAR, organizar.PASTA_DUPLICATAS, organizar.PASTA_FORMATOS):
            n = sum(1 for i in self.itens if i.incluir and i.destino.startswith(pasta + os.sep))
            if n:
                partes.append(molde.format(n=n, pasta=pasta))
        return "".join(partes)

    def tema_em_lote(self):
        ks = self.selecionados()
        if not ks:
            return
        janela = tk.Toplevel(self)
        janela.title("Tema dos selecionados")
        janela.transient(self)
        var = tk.StringVar(value=self.itens[ks[0]].tema)
        ttk.Label(janela, text=f"Tema para {len(ks)} arquivos:").pack(padx=10, pady=(10, 4), anchor="w")
        opcoes = sorted(set(temas.carregar()) | {i.tema for i in self.itens if i.tema} | {temas.OUTROS})
        cb = ttk.Combobox(janela, textvariable=var, values=opcoes, width=45)
        cb.pack(padx=10)
        cb.focus_set()

        def ok(*_):
            for k in ks:
                self.itens[k].tema = var.get().strip()
            janela.destroy()
            self.replanejar()

        ttk.Button(janela, text="OK", command=ok).pack(pady=10)
        cb.bind("<Return>", ok)

    def _menu(self, e):
        linha = self.tabela.identify_row(e.y)
        if linha and linha not in self.tabela.selection():
            self.tabela.selection_set(linha)
        self.menu.tk_popup(e.x_root, e.y_root)

    def abrir_arquivo(self):
        ks = self.selecionados()
        if not ks:
            messagebox.showinfo("Estante", "Selecione um arquivo na tabela.")
        for k in ks[:5]:
            abrir_no_sistema(self.itens[k].caminho)

    def abrir_pasta(self):
        ks = self.selecionados()
        if not ks:
            return
        caminho = self.itens[ks[0]].caminho
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(caminho)])
        else:
            abrir_no_sistema(os.path.dirname(caminho))

    # -------------------------------------------------------------- perfis
    def _guardar_perfil(self):
        self.perfis[self.perfil] = {"online": self.v_online.get(), "email": self.v_email.get().strip(),
                                    "chave_openalex": self.v_chave.get().strip(), "temas": temas.ARQUIVO}

    def trocar_perfil(self, nome):
        self._guardar_perfil()
        self.perfil = nome
        self.v_perfil.set(nome)
        p = self.perfis[nome]
        self.v_online.set(p.get("online", True))
        self.v_email.set(p.get("email", ""))
        self.v_chave.set(p.get("chave_openalex", ""))
        temas.ARQUIVO = p.get("temas", temas.ARQUIVO_PADRAO)
        # os temas do perfil valem já para a tabela, sem analisar de novo
        lista = temas.carregar()
        for it in self.itens:
            if it.extras.get("texto_tema") and it.confianca != "manual":
                it.tema = temas.sugerir(it.extras["texto_tema"], lista)
        self._opcoes_resumo()
        self.salvar_config()
        self.replanejar()

    def novo_perfil(self):
        nome = (simpledialog.askstring("Novo perfil", "Nome do perfil (ex.: Pesquisa, Pessoal):", parent=self) or "").strip()
        if not nome or nome in self.perfis:
            return
        self._guardar_perfil()
        arq = "temas_" + (re.sub(r"\W+", "_", nome.lower()).strip("_") or "perfil") + ".json"
        config.salvar(arq, temas.carregar())  # começa com uma cópia dos temas atuais
        self.perfis[nome] = {**self.perfis[self.perfil], "temas": arq}
        self._menu_perfis()
        self.trocar_perfil(nome)

    def excluir_perfil(self):
        if len(self.perfis) == 1:
            messagebox.showinfo("Perfis", "Este é o único perfil.")
            return
        if not messagebox.askyesno("Perfis", f"Excluir o perfil '{self.perfil}'? (o arquivo de temas dele é mantido)"):
            return
        del self.perfis[self.perfil]
        self.perfil = next(iter(self.perfis))
        self._menu_perfis()
        self.trocar_perfil(self.perfil)

    def pedir_email(self):
        v = simpledialog.askstring("E-mail do CrossRef", "A CrossRef pede um e-mail de contato (opcional):",
                                   initialvalue=self.v_email.get(), parent=self)
        if v is not None:
            self.v_email.set(v.strip())
            self.salvar_config()

    def pedir_chave(self):
        v = simpledialog.askstring("Chave do OpenAlex", "Cole a sua chave do OpenAlex (Ajuda › Como obter a chave):",
                                   initialvalue=self.v_chave.get(), show="•", parent=self)
        if v is not None:
            self.v_chave.set(v.strip())
            self.salvar_config()

    def opcoes_avancadas(self):
        janela = tk.Toplevel(self, bg=CARTAO, padx=18, pady=14)
        janela.title(f"Opções avançadas — perfil {self.perfil}")
        janela.transient(self)
        tk.Checkbutton(janela, text="Consultar bases abertas (CrossRef, OpenAlex, Open Library, Google Books)",
                       variable=self.v_online, bg=CARTAO, activebackground=CARTAO).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        for linha, (rotulo, var, mostrar) in enumerate((("E-mail do CrossRef", self.v_email, ""),
                                                        ("Chave do OpenAlex", self.v_chave, "•")), 1):
            tk.Label(janela, text=rotulo, bg=CARTAO).grid(row=linha, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Entry(janela, textvariable=var, width=44, show=mostrar).grid(row=linha, column=1, sticky="ew", pady=4)
        self._link(janela, "Como obter?", self.ajuda_chave, negrito=False).grid(row=2, column=2, padx=(8, 0))
        tk.Label(janela, text=f"Temas deste perfil: {temas.ARQUIVO}", bg=CARTAO, fg=TEXTO_2).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self._link(janela, "Editar lista de temas…", self.editar_temas, negrito=False).grid(row=3, column=2, pady=(10, 0))

        def ok():
            self.salvar_config()
            janela.destroy()

        tk.Button(janela, text="OK", command=ok, bg=VERDE, fg="white", activebackground=VERDE_ESCURO,
                  activeforeground="white", relief="flat", padx=18, pady=4, font=(FONTE, 10, "bold")).grid(
            row=4, column=2, sticky="e", pady=(14, 0))
        janela.protocol("WM_DELETE_WINDOW", ok)
        janela.grab_set()

    # -------------------------------------------------------------- ações
    def escolher_pasta(self, var):
        p = filedialog.askdirectory(initialdir=var.get() or None, mustexist=False)
        if p:
            var.set(os.path.normpath(p))

    def _validar(self, precisa_destino):
        origem, destino = self.v_origem.get().strip(), self.v_destino.get().strip()
        if not origem or not os.path.isdir(origem):
            messagebox.showwarning("Estante", "Escolha uma pasta a analisar que exista.")
            return None
        if precisa_destino and not destino:
            messagebox.showwarning("Estante", "Escolha a pasta de destino.")
            return None
        return origem, destino

    def analisar(self):
        if self.ocupado:
            self.cancelar = True
            return
        pastas = self._validar(precisa_destino=False)
        if not pastas:
            return
        origem, destino = pastas
        if self.itens and not messagebox.askyesno("Estante", "Analisar de novo? As edições feitas na tabela serão perdidas "
                                                  "(exporte um CSV antes, se quiser guardá-las)."):
            return
        self.salvar_config()
        metadados.configurar(self.v_email.get().strip(), self.v_chave.get())
        metadados.AVISOS.clear()
        ignorar = pastas_ignoradas(origem, destino)
        online = self.v_online.get()
        self._iniciar("Analisando…", "■  Cancelar")

        def tarefa():
            itens = analisar_pasta(origem, online=online, ignorar=ignorar,
                                   progresso=lambda n, t, c: self.fila.put(("progresso", n, t, c)),
                                   cancelar=lambda: self.cancelar)
            self.fila.put(("analisado", itens))

        threading.Thread(target=tarefa, daemon=True).start()

    def aplicar(self):
        if self.ocupado or not self.itens:
            return
        pastas = self._validar(precisa_destino=True)
        if not pastas:
            return
        destino = pastas[1]
        self.replanejar()
        n = sum(1 for i in self.itens if i.incluir and i.destino)
        if not n:
            messagebox.showinfo("Estante", "Nenhum arquivo marcado.")
            return
        op = self.v_operacao.get()
        aviso = ("Os originais ficam onde estão." if op == "copiar" else
                 "Os arquivos SAIRÃO da pasta original (o log permite desfazer).")
        aviso += self._especiais("\n\n{n} vão para '{pasta}' com o nome original.")
        if op == "mover":
            aviso += (f"\nArquivos cujo idêntico já está no destino vão para '{organizar.PASTA_DUPLICATAS}'.")
        if not messagebox.askyesno("Confirmar", f"{op.capitalize()} {n} arquivos para\n{destino}?\n\n{aviso}"):
            return
        self.salvar_config()
        self._iniciar("Organizando…", None)

        def tarefa():
            try:
                r = organizar.aplicar(self.itens, destino, op, lambda a, t, c: self.fila.put(("progresso", a, t, c)))
                self.fila.put(("aplicado", op, *r))
            except Exception as e:  # noqa: BLE001
                self.fila.put(("erro", str(e)))

        threading.Thread(target=tarefa, daemon=True).start()

    def _logs(self):
        """Logs de aplicações ainda não desfeitas, do mais novo para o mais antigo."""
        pasta = os.path.join(self.v_destino.get().strip(), organizar.PASTA_LOGS)
        return sorted((p for p in glob.glob(os.path.join(pasta, "*.csv")) if not p.endswith("_desfeito.csv")),
                      key=os.path.getmtime, reverse=True)

    def desfazer_ultima(self):
        if self.ocupado:
            return
        logs = self._logs()
        if not logs:
            messagebox.showinfo("Desfazer", "Não há aplicação para desfazer nesta pasta de destino.")
            return
        self._desfazer_log(logs[0])

    def desfazer(self):
        if self.ocupado:
            return
        inicio = os.path.join(self.v_destino.get().strip(), organizar.PASTA_LOGS)
        log = filedialog.askopenfilename(title="Escolha o log da operação a desfazer",
                                         initialdir=inicio if os.path.isdir(inicio) else None,
                                         filetypes=[("Log da Estante", "*.csv")])
        if log:
            self._desfazer_log(log)

    def _desfazer_log(self, log):
        if not messagebox.askyesno("Desfazer", f"Desfazer a operação registrada em\n{os.path.basename(log)}?"):
            return
        try:
            feitos, avisos = organizar.desfazer(log)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Estante", f"Não foi possível desfazer:\n{e}")
            return
        msg = f"{feitos} arquivos desfeitos."
        if avisos:
            msg += f"\n\n{len(avisos)} avisos:\n" + "\n".join(avisos[:15])
        messagebox.showinfo("Desfazer", msg)

    def ver_log(self):
        logs = sorted(glob.glob(os.path.join(self.v_destino.get().strip(), organizar.PASTA_LOGS, "*.csv")),
                      key=os.path.getmtime, reverse=True)
        if not logs:
            messagebox.showinfo("Log", "Ainda não há operações registradas nesta pasta de destino.")
            return
        abrir_no_sistema(logs[0])

    def exportar(self):
        if not self.itens:
            messagebox.showinfo("Estante", "Analise uma pasta antes de exportar.")
            return
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="estante.csv", filetypes=[("CSV", "*.csv")])
        if p:
            organizar.exportar_csv(self.itens, p)
            self.v_status.set(f"CSV salvo em {p} (separador ';', abre no Excel/LibreOffice)")

    def importar(self):
        if not self.itens:
            messagebox.showinfo("Estante", "Analise a pasta antes de importar as edições de um CSV.")
            return
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv")])
        if p:
            n = organizar.importar_csv(self.itens, p)
            self.replanejar()
            self.preencher()
            messagebox.showinfo("Estante", f"{n} linhas atualizadas a partir do CSV.")

    def revisar_temas(self):
        """Depois da análise: os temas encontrados, para confirmar, renomear, juntar, desligar ou criar."""
        if not self.itens or self.ocupado:
            return
        validos = [i for i in self.itens if not i.duplicata_de and i.ext in arquivos.EXTENSOES]
        nomes = {}  # tema sugerido -> [usar, nome escolhido]
        novos = {}  # temas criados aqui -> expressão
        originais = [(it, it.tema) for it in validos]  # "Cancelar" desfaz o que "Novo tema…" já aplicou
        janela = tk.Toplevel(self)
        janela.title("Revisar temas")
        janela.transient(self)
        janela.geometry("980x520")
        ttk.Label(janela, wraplength=940, justify="left", text=(
            "Estes são os temas que a análise encontrou. Confira antes de aplicar:\n"
            "•  duplo clique no nome renomeia (dar o nome de outro tema junta os dois);\n"
            "•  clique no ✓ desliga o tema (os arquivos dele vão para 'Outros');\n"
            "•  'Novo tema…' cria um tema por palavras-chave e já o aplica aos arquivos que estão em 'Outros'.\n"
            f"Pastas padrão: '{temas.OUTROS}', '{organizar.PASTA_CONFIRMAR}', '{organizar.PASTA_FORMATOS}' e "
            f"'{organizar.PASTA_DUPLICATAS}'.")).pack(padx=10, pady=(10, 6), anchor="w")
        arvore = ttk.Treeview(janela, columns=("usar", "tema", "n", "ex"), show="headings", height=14)
        for c, t, w in (("usar", "✓", 34), ("tema", "Tema", 320), ("n", "Arquivos", 70), ("ex", "Exemplos", 520)):
            arvore.heading(c, text=t)
            arvore.column(c, width=w, anchor="center" if c in ("usar", "n") else "w", stretch=c == "ex")
        arvore.pack(fill="both", expand=True, padx=10)

        def mostrar():
            arvore.delete(*arvore.get_children())
            grupos = {}
            for it in validos:
                grupos.setdefault(it.tema, []).append(it)
            for tema, its in sorted(grupos.items(), key=lambda g: (g[0] == "", -len(g[1]))):
                if tema:
                    usar, atual = nomes.setdefault(tema, [True, tema])
                    rotulo = atual if atual == tema else f"{atual}   (era: {tema})"
                else:
                    usar, rotulo = None, f"{temas.OUTROS}   (nenhum tema reconhecido)"
                ex = "  |  ".join((i.titulo or os.path.basename(i.caminho))[:70] for i in its[:3])
                marca = "" if usar is None else "☑" if usar else "☐"
                arvore.insert("", "end", iid=tema or SEM_TEMA, values=(marca, rotulo, len(its), ex))

        def clique(e):
            iid = arvore.identify_row(e.y)
            if iid and iid != SEM_TEMA and arvore.identify_column(e.x) == "#1":
                nomes[iid][0] = not nomes[iid][0]
                mostrar()

        def duplo(e):
            iid = arvore.identify_row(e.y)
            if not iid or iid == SEM_TEMA or arvore.identify_column(e.x) != "#2":
                return
            x, y, w, h = arvore.bbox(iid, "tema")
            var = tk.StringVar(value=nomes[iid][1])
            opcoes = sorted({n for _, n in nomes.values()} | set(temas.carregar()) | set(novos) | {temas.OUTROS})
            ed = ttk.Combobox(arvore, textvariable=var, values=opcoes)
            ed.place(x=x, y=y, width=w, height=h)
            ed.focus_set()

            def fechar(salvar):
                if ed.winfo_exists():
                    if salvar and var.get().strip():
                        nomes[iid][1] = var.get().strip()
                    ed.destroy()
                    mostrar()
            ed.bind("<Return>", lambda _: fechar(True))
            ed.bind("<Escape>", lambda _: fechar(False))
            ed.bind("<FocusOut>", lambda _: fechar(True))

        arvore.bind("<Button-1>", clique)
        arvore.bind("<Double-1>", duplo)

        def novo_tema():
            nome = (simpledialog.askstring("Novo tema", "Nome do tema (vira o nome da pasta):", parent=janela) or "").strip()
            if not nome:
                return
            rx = temas.palavras_chave(simpledialog.askstring(
                "Novo tema", f"Palavras-chave de '{nome}', separadas por vírgula (ex.: drone, sarp, vant):", parent=janela))
            if not rx:
                return
            novos[nome] = rx
            n = 0
            for it in validos:
                if not it.tema and re.search(rx, it.extras.get("texto_tema", "").lower()):
                    it.tema, n = nome, n + 1
            mostrar()
            messagebox.showinfo("Novo tema", f"{n} arquivos que estavam em '{temas.OUTROS}' passaram para '{nome}'.",
                                parent=janela)

        v_guardar = tk.BooleanVar(value=True)

        def confirmar():
            for it in self.itens:
                if it.tema in nomes:
                    usar, atual = nomes[it.tema]
                    it.tema = atual if usar and atual != temas.OUTROS else ""
            if v_guardar.get():
                temas.aplicar_revisao({a: n for a, (_, n) in nomes.items() if n != a}, novos)
            janela.destroy()
            self.replanejar()
            self.preencher()

        def cancelar():
            for it, tema in originais:
                it.tema = tema
            janela.destroy()

        rodape = ttk.Frame(janela, padding=10)
        rodape.pack(fill="x")
        ttk.Checkbutton(rodape, text="Guardar nomes e temas novos para as próximas análises",
                        variable=v_guardar).pack(side="left")
        ttk.Button(rodape, text="Confirmar", command=confirmar).pack(side="right")
        ttk.Button(rodape, text="Cancelar", command=cancelar).pack(side="right", padx=6)
        ttk.Button(rodape, text="Novo tema…", command=novo_tema).pack(side="right", padx=6)
        janela.protocol("WM_DELETE_WINDOW", cancelar)
        mostrar()
        janela.grab_set()

    def editar_temas(self):
        messagebox.showinfo("Temas", f"Os temas do perfil '{self.perfil}' abrem num editor de texto: cada tema tem "
                            "palavras-chave (expressões regulares, separadas por |). Salve e clique em Analisar de novo; "
                            "a nova análise é rápida porque usa o cache.")
        abrir_no_sistema(str(temas.caminho()))

    def como_usar(self):
        messagebox.showinfo("Como usar", (
            "1. Analisar\n"
            "Escolha a pasta a analisar e a de destino e clique em Analisar. A Estante lê cada PDF/EPUB, "
            "identifica autor, título e ano, sugere um tema e acha duplicatas. No fim, abre a revisão de temas.\n\n"
            "Revise a tabela: duplo clique edita; o ✓ marca o que será organizado; os filtros mostram o que "
            "precisa de atenção (baixa confiança, sem tema, duplicatas).\n\n"
            "2. Aplicar\n"
            "Copiar mantém os originais; Mover tira os arquivos da pasta original. Os não identificados vão para "
            f"'{organizar.PASTA_CONFIRMAR}', os formatos não lidos para '{organizar.PASTA_FORMATOS}' e, no modo "
            f"Mover, as cópias redundantes para '{organizar.PASTA_DUPLICATAS}'.\n\n"
            "Tudo fica num log: Ferramentas › Desfazer última aplicação reverte a operação inteira."))

    def ajuda_chave(self):
        url = "https://help.openalex.org/api/authentication/"
        if messagebox.askyesno("Chave do OpenAlex",
                               "O OpenAlex identifica artigos e livros pelo título ou DOI. Sem chave, há só uma cota "
                               "diária pequena, dividida com todos da sua rede, que uma biblioteca grande esgota.\n\n"
                               "A chave é gratuita:\n"
                               f"1. Abra {url}\n"
                               "2. Siga as instruções para criar a sua chave (pedem só o e-mail; chega um link "
                               "de confirmação).\n"
                               "3. Cole a chave em Perfis › Chave do OpenAlex e clique em Analisar.\n\n"
                               "A chave fica salva só neste computador, na configuração da Estante.\n\n"
                               "Abrir a página agora?"):
            abrir_no_sistema(url)

    def sobre(self):
        messagebox.showinfo("Sobre a Estante", f"Estante {__version__}\n"
                            "Copyright (C) 2026 Luiz Gustavo Lavandoski da Silva\n\n"
                            "Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob os termos "
                            "da GNU Affero General Public License, versão 3 (arquivo LICENSE).\n\n"
                            "É distribuído SEM NENHUMA GARANTIA, nem mesmo a de COMERCIABILIDADE ou ADEQUAÇÃO "
                            "A UM PROPÓSITO ESPECÍFICO.")

    # ----------------------------------------------------------- execução em segundo plano
    def _iniciar(self, status, rotulo_botao):
        self.ocupado, self.cancelar = True, False
        self._inicio = time.time()
        self.v_status.set(status)
        self.v_tempo.set("")
        self.barra.configure(value=0)
        self.b_aplicar.configure(state="disabled")
        if rotulo_botao:
            self.b_analisar.configure(text=rotulo_botao)
        else:
            self.b_analisar.configure(state="disabled")

    def _terminar(self, o_que):
        self.ocupado = False
        self.v_tempo.set(f"{o_que} em {time.time() - self._inicio:.1f} s".replace(".", ","))
        self.b_analisar.configure(text="①  Analisar", state="normal")
        self.b_aplicar.configure(state="normal")

    def _ler_fila(self):
        try:
            while True:
                msg = self.fila.get_nowait()
                tipo = msg[0]
                if tipo == "progresso":
                    _, n, total, caminho = msg
                    self.barra.configure(maximum=max(total, 1), value=n)
                    self.v_status.set(f"{n}/{total}  {os.path.basename(caminho)}")
                elif tipo == "analisado":
                    self._terminar("Análise concluída")
                    self.itens = msg[1]
                    self.replanejar()
                    self.preencher()
                    if self.cancelar:
                        self.v_status.set(self.v_status.get() + "  (análise cancelada: lista parcial)")
                    if metadados.AVISOS:
                        messagebox.showwarning("Estante", "Algumas bases não responderam; os arquivos afetados serão "
                                               "consultados de novo na próxima análise.\n\n" + "\n".join(sorted(metadados.AVISOS)))
                    if self.itens and not self.cancelar:
                        self.after(200, self.revisar_temas)  # antes de aplicar, você confirma os temas
                elif tipo == "aplicado":
                    _, op, log, feitos, pulados = msg
                    self._terminar("Aplicação concluída")
                    self._resumo()
                    texto = f"{feitos} arquivos {'copiados' if op == 'copiar' else 'movidos'}.\n\nLog para desfazer:\n{log}"
                    if pulados:
                        texto += f"\n\n{len(pulados)} pulados:\n" + "\n".join(pulados[:12])
                    if messagebox.askyesno("Pronto", texto + "\n\nAbrir a pasta de destino?"):
                        abrir_no_sistema(self.v_destino.get().strip())
                    if op == "mover":  # os caminhos mudaram: a tabela antiga não vale mais
                        self.itens = []
                        self.preencher()
                        self._pintar_chips()
                        self._botao_aplicar()
                        self.v_status.set("Arquivos movidos. Analise de novo para continuar.")
                elif tipo == "erro":
                    self._terminar("Interrompido")
                    messagebox.showerror("Estante", f"Erro:\n{msg[1]}\n\nO que já foi feito está no log.")
        except queue.Empty:
            pass
        self.after(100, self._ler_fila)

    def salvar_config(self):
        self._guardar_perfil()
        self._opcoes_resumo()
        config.salvar("config.json", {"origem": self.v_origem.get(), "destino": self.v_destino.get(),
                                      "modo": self.v_modo.get(), "operacao": self.v_operacao.get(),
                                      "perfil": self.perfil, "perfis": self.perfis})

    def fechar(self):
        if self.ocupado and not messagebox.askyesno("Estante", "Há uma tarefa em andamento. Sair mesmo assim?"):
            return
        self.salvar_config()
        self.destroy()


def main():
    if sys.platform == "win32":  # texto nítido em telas com escala (125%, 150%…)
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # identidade própria na barra de tarefas (senão aparece o ícone do Python)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Estante.Organizador")
        except (AttributeError, OSError):
            pass
    App().mainloop()
