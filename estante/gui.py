"""Interface desktop (Tkinter): escolher pastas, analisar, revisar/editar na tabela, aplicar e desfazer."""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__, config, metadados, organizar, temas
from .analise import analisar_pasta

COLUNAS = [  # (id, título, largura, editável)
    ("incluir", "✓", 34, False),
    ("confianca", "Confiança", 80, False),
    ("sobrenome", "Sobrenome", 130, True),
    ("titulo", "Título", 330, True),
    ("ano", "Ano", 55, True),
    ("tema", "Tema", 190, True),
    ("anotacoes", "Anot.", 50, False),
    ("destino", "Novo nome", 330, False),
    ("arquivo", "Arquivo atual", 260, False),
]
CORES = {"alta": "#e3f4e1", "media": "#fbf3d5", "baixa": "#fde3cf", "nenhuma": "#f9d6d5",
         "manual": "#e1ecfb", "duplicata": "#e6e6e6"}
FILTROS = ["Todos", "A revisar (baixa/nenhuma)", "Selecionados", "Não selecionados", "Duplicatas", "Com anotações"]
MODOS = {"Pastas por tema": organizar.POR_TEMA, "Pastas por autor": organizar.POR_AUTOR,
         "Sem subpastas": organizar.SEM_PASTAS}


def abrir_no_sistema(caminho):
    if sys.platform == "win32":
        os.startfile(caminho)  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", caminho])


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Estante {__version__} — organizador de livros, artigos e textos")
        self.geometry("1400x800")
        self.minsize(900, 500)
        self.itens = []
        self.fila = queue.Queue()
        self.ocupado = False
        self.cancelar = False
        self.editor = None
        cfg = config.carregar("config.json", {})
        self.v_origem = tk.StringVar(value=cfg.get("origem", ""))
        self.v_destino = tk.StringVar(value=cfg.get("destino", ""))
        self.v_modo = tk.StringVar(value=cfg.get("modo", "Pastas por tema"))
        self.v_operacao = tk.StringVar(value=cfg.get("operacao", "copiar"))
        self.v_online = tk.BooleanVar(value=cfg.get("online", True))
        self.v_email = tk.StringVar(value=cfg.get("email", ""))
        self.v_filtro = tk.StringVar(value=FILTROS[0])
        self.v_busca = tk.StringVar()
        self.v_status = tk.StringVar(value="Escolha a pasta a analisar e clique em Analisar.")
        self._montar()
        self.v_modo.trace_add("write", lambda *_: self.replanejar())
        self.v_destino.trace_add("write", lambda *_: self.replanejar())
        self.v_filtro.trace_add("write", lambda *_: self.preencher())
        self.v_busca.trace_add("write", lambda *_: self.preencher())
        self.protocol("WM_DELETE_WINDOW", self.fechar)
        self.after(100, self._ler_fila)

    # ------------------------------------------------------------------ layout
    def _montar(self):
        pad = {"padx": 6, "pady": 3}
        topo = ttk.Frame(self, padding=8)
        topo.pack(fill="x")
        for linha, (rotulo, var) in enumerate([("Pasta a analisar:", self.v_origem), ("Pasta de destino:", self.v_destino)]):
            ttk.Label(topo, text=rotulo).grid(row=linha, column=0, sticky="w", **pad)
            ttk.Entry(topo, textvariable=var).grid(row=linha, column=1, sticky="ew", **pad)
            ttk.Button(topo, text="Escolher…", command=lambda v=var: self.escolher_pasta(v)).grid(row=linha, column=2, **pad)
        topo.columnconfigure(1, weight=1)

        opcoes = ttk.Frame(self, padding=(8, 0))
        opcoes.pack(fill="x")
        ttk.Label(opcoes, text="Organizar:").pack(side="left")
        ttk.Combobox(opcoes, textvariable=self.v_modo, values=list(MODOS), state="readonly", width=16).pack(side="left", padx=4)
        ttk.Radiobutton(opcoes, text="Copiar (originais intactos)", value="copiar", variable=self.v_operacao).pack(side="left", padx=(12, 2))
        ttk.Radiobutton(opcoes, text="Mover", value="mover", variable=self.v_operacao).pack(side="left", padx=2)
        ttk.Checkbutton(opcoes, text="Consultar bases abertas (CrossRef, OpenAlex…)", variable=self.v_online).pack(side="left", padx=(16, 4))
        ttk.Label(opcoes, text="E-mail p/ CrossRef (opcional):").pack(side="left", padx=(8, 2))
        ttk.Entry(opcoes, textvariable=self.v_email, width=24).pack(side="left")

        botoes = ttk.Frame(self, padding=8)
        botoes.pack(fill="x")
        self.b_analisar = ttk.Button(botoes, text="1. Analisar", command=self.analisar)
        self.b_analisar.pack(side="left")
        self.b_aplicar = ttk.Button(botoes, text="2. Aplicar", command=self.aplicar)
        self.b_aplicar.pack(side="left", padx=4)
        ttk.Button(botoes, text="Desfazer…", command=self.desfazer).pack(side="left", padx=4)
        ttk.Separator(botoes, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(botoes, text="Exportar CSV", command=self.exportar).pack(side="left")
        ttk.Button(botoes, text="Importar CSV", command=self.importar).pack(side="left", padx=4)
        ttk.Button(botoes, text="Editar temas", command=self.editar_temas).pack(side="left", padx=4)
        ttk.Button(botoes, text="Sobre", command=self.sobre).pack(side="left", padx=4)
        ttk.Entry(botoes, textvariable=self.v_busca, width=28).pack(side="right")
        ttk.Label(botoes, text="Buscar:").pack(side="right", padx=4)
        ttk.Combobox(botoes, textvariable=self.v_filtro, values=FILTROS, state="readonly", width=24).pack(side="right", padx=8)
        ttk.Label(botoes, text="Mostrar:").pack(side="right")

        meio = ttk.Frame(self, padding=(8, 0))
        meio.pack(fill="both", expand=True)
        self.tabela = ttk.Treeview(meio, columns=[c[0] for c in COLUNAS], show="headings", selectmode="extended")
        for cid, titulo, largura, _ in COLUNAS:
            self.tabela.heading(cid, text=titulo, command=lambda c=cid: self.ordenar(c))
            self.tabela.column(cid, width=largura, minwidth=30, stretch=cid in ("titulo", "destino", "arquivo"),
                               anchor="center" if cid in ("incluir", "ano", "anotacoes", "confianca") else "w")
        for tag, cor in CORES.items():
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

        self.detalhes = tk.Text(self, height=4, wrap="word", relief="flat", font="TkDefaultFont",
                                background=self.cget("background"))
        self.detalhes.pack(fill="x", padx=8, pady=(4, 0))
        self.detalhes.configure(state="disabled")

        rodape = ttk.Frame(self, padding=8)
        rodape.pack(fill="x")
        self.barra = ttk.Progressbar(rodape, length=260)
        self.barra.pack(side="right")
        ttk.Label(rodape, textvariable=self.v_status).pack(side="left", fill="x", expand=True)

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Abrir arquivo", command=self.abrir_arquivo)
        self.menu.add_command(label="Abrir pasta do arquivo", command=self.abrir_pasta)
        self.menu.add_separator()
        self.menu.add_command(label="Selecionar para organizar", command=lambda: self.marcar(True))
        self.menu.add_command(label="Tirar da seleção", command=lambda: self.marcar(False))
        self.menu.add_command(label="Definir tema dos selecionados…", command=self.tema_em_lote)

    # ------------------------------------------------------------- tabela
    def visiveis(self):
        f, busca = self.v_filtro.get(), self.v_busca.get().strip().lower()
        for k, it in enumerate(self.itens):
            if f == FILTROS[1] and it.confianca not in ("baixa", "nenhuma"):
                continue
            if f == FILTROS[2] and not it.incluir:
                continue
            if f == FILTROS[3] and it.incluir:
                continue
            if f == FILTROS[4] and not it.duplicata_de:
                continue
            if f == FILTROS[5] and not it.anotacoes:
                continue
            if busca and busca not in f"{it.caminho} {it.sobrenome} {it.titulo} {it.tema}".lower():
                continue
            yield k, it

    def valores(self, it):
        return ("☑" if it.incluir else "☐", it.confianca, it.sobrenome, it.titulo, it.ano, it.tema,
                it.anotacoes or "", it.destino, os.path.basename(it.caminho))

    def tag(self, it):
        return "duplicata" if it.duplicata_de and not it.incluir else it.confianca

    def preencher(self):
        self.tabela.delete(*self.tabela.get_children())
        n = 0
        for k, it in self.visiveis():
            self.tabela.insert("", "end", iid=str(k), values=self.valores(it), tags=(self.tag(it),))
            n += 1
        self._resumo(n)

    def atualizar_linhas(self, ks=None):
        for k in (ks if ks is not None else range(len(self.itens))):
            if self.tabela.exists(str(k)):
                it = self.itens[k]
                self.tabela.item(str(k), values=self.valores(it), tags=(self.tag(it),))
        self._resumo()

    def _resumo(self, mostrados=None):
        if not self.itens or self.ocupado:
            return
        sel = sum(i.incluir for i in self.itens)
        rev = sum(i.confianca in ("baixa", "nenhuma") and not i.duplicata_de for i in self.itens)
        dup = sum(bool(i.duplicata_de) for i in self.itens)
        extra = f" | mostrando {mostrados}" if mostrados is not None and mostrados != len(self.itens) else ""
        self.v_status.set(f"{len(self.itens)} arquivos | {sel} selecionados para organizar | "
                          f"{rev} a revisar | {dup} duplicatas{extra}")

    def replanejar(self):
        if self.itens and not self.ocupado:
            organizar.planejar(self.itens, self.v_destino.get().strip(), MODOS.get(self.v_modo.get(), organizar.POR_TEMA))
            self.atualizar_linhas()

    def ordenar(self, col):
        chave = {"arquivo": lambda i: i.caminho.lower(), "anotacoes": lambda i: i.anotacoes,
                 "incluir": lambda i: i.incluir,
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
        texto = ""
        if len(sel) == 1:
            it = self.itens[sel[0]]
            texto = (f"Arquivo: {it.caminho}\nFonte: {it.fonte or '—'}   {it.identificador}   |   "
                     f"Confiança: {it.confianca}   |   Anotações: {it.anotacoes}\n"
                     f"Observação: {it.observacao or '—'}")
        elif sel:
            texto = f"{len(sel)} linhas selecionadas (botão direito para ações em lote)"
        self.detalhes.configure(state="normal")
        self.detalhes.delete("1.0", "end")
        self.detalhes.insert("1.0", texto)
        self.detalhes.configure(state="disabled")

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

    def marcar(self, valor):
        ks = self.selecionados()
        for k in ks:
            self.itens[k].incluir = (not self.itens[k].incluir) if valor is None else valor
        self.replanejar()
        return "break"

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
        for k in self.selecionados()[:5]:
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
        metadados.configurar(self.v_email.get().strip())
        # a pasta de destino não entra na análise quando fica dentro da pasta analisada
        ignorar = [destino] if destino and os.path.normcase(os.path.abspath(destino)) != os.path.normcase(os.path.abspath(origem)) else []
        online = self.v_online.get()
        self._iniciar("Analisando…", "Cancelar")

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
            messagebox.showinfo("Estante", "Nenhum arquivo selecionado.")
            return
        op = self.v_operacao.get()
        aviso = ("Os originais ficam onde estão." if op == "copiar" else
                 "Os arquivos SAIRÃO da pasta original (o log permite desfazer).")
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

    def desfazer(self):
        if self.ocupado:
            return
        inicio = os.path.join(self.v_destino.get().strip(), organizar.PASTA_LOGS)
        log = filedialog.askopenfilename(title="Escolha o log da operação a desfazer",
                                         initialdir=inicio if os.path.isdir(inicio) else None,
                                         filetypes=[("Log da Estante", "*.csv")])
        if not log or not messagebox.askyesno("Desfazer", f"Desfazer a operação registrada em\n{os.path.basename(log)}?"):
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

    def exportar(self):
        if not self.itens:
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

    def editar_temas(self):
        messagebox.showinfo("Temas", "Os temas abrem num editor de texto: cada tema tem palavras-chave "
                            "(expressões regulares, separadas por |). Salve e clique em Analisar de novo; "
                            "a nova análise é rápida porque usa o cache.")
        abrir_no_sistema(str(temas.caminho()))

    def sobre(self):
        messagebox.showinfo("Sobre a Estante", f"Estante {__version__}\n"
                            "Copyright (C) 2026 Luiz Gustavo Lavandoski\n\n"
                            "Este programa é software livre: você pode redistribuí-lo e/ou modificá-lo sob os termos "
                            "da GNU Affero General Public License, versão 3 (arquivo LICENSE).\n\n"
                            "É distribuído SEM NENHUMA GARANTIA, nem mesmo a de COMERCIABILIDADE ou ADEQUAÇÃO "
                            "A UM PROPÓSITO ESPECÍFICO.")

    # ----------------------------------------------------------- execução em segundo plano
    def _iniciar(self, status, rotulo_botao):
        self.ocupado, self.cancelar = True, False
        self.v_status.set(status)
        self.barra.configure(value=0)
        self.b_aplicar.state(["disabled"])
        if rotulo_botao:
            self.b_analisar.configure(text=rotulo_botao)
        else:
            self.b_analisar.state(["disabled"])

    def _terminar(self):
        self.ocupado = False
        self.b_analisar.configure(text="1. Analisar")
        self.b_analisar.state(["!disabled"])
        self.b_aplicar.state(["!disabled"])

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
                    self._terminar()
                    self.itens = msg[1]
                    self.replanejar()
                    self.preencher()
                    if self.cancelar:
                        self.v_status.set(self.v_status.get() + "  (análise cancelada: lista parcial)")
                elif tipo == "aplicado":
                    _, op, log, feitos, pulados = msg
                    self._terminar()
                    self._resumo()
                    texto = f"{feitos} arquivos {'copiados' if op == 'copiar' else 'movidos'}.\n\nLog para desfazer:\n{log}"
                    if pulados:
                        texto += f"\n\n{len(pulados)} pulados:\n" + "\n".join(pulados[:12])
                    if messagebox.askyesno("Pronto", texto + "\n\nAbrir a pasta de destino?"):
                        abrir_no_sistema(self.v_destino.get().strip())
                    if op == "mover":  # os caminhos mudaram: a tabela antiga não vale mais
                        self.itens = []
                        self.preencher()
                        self.v_status.set("Arquivos movidos. Analise de novo para continuar.")
                elif tipo == "erro":
                    self._terminar()
                    messagebox.showerror("Estante", f"Erro:\n{msg[1]}\n\nO que já foi feito está no log.")
        except queue.Empty:
            pass
        self.after(100, self._ler_fila)

    def salvar_config(self):
        config.salvar("config.json", {"origem": self.v_origem.get(), "destino": self.v_destino.get(),
                                      "modo": self.v_modo.get(), "operacao": self.v_operacao.get(),
                                      "online": self.v_online.get(), "email": self.v_email.get()})

    def fechar(self):
        if self.ocupado and not messagebox.askyesno("Estante", "Há uma tarefa em andamento. Sair mesmo assim?"):
            return
        self.salvar_config()
        self.destroy()


def main():
    App().mainloop()
