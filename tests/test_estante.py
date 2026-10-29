import os
import shutil
import tempfile
import unittest
from unittest import mock

import pymupdf

from estante import analise, arquivos, config, metadados, nomes, organizar, temas


def criar_pdf(caminho, titulo, corpo="", autor="", anotar=False):
    doc = pymupdf.open()
    pg = doc.new_page()
    pg.insert_text((72, 100), titulo, fontsize=22)
    pg.insert_textbox(pymupdf.Rect(72, 140, 520, 780), corpo or ("Texto de exemplo. " * 60), fontsize=10)
    if anotar:
        pg.add_highlight_annot(pymupdf.Rect(72, 80, 300, 110))
    doc.set_metadata({"title": titulo, "author": autor})
    doc.save(caminho)
    doc.close()


class TestNomes(unittest.TestCase):
    def test_limpar_troca_dois_pontos_e_invalidos(self):
        self.assertEqual(nomes.limpar('Guerra: a arte / do "conflito"?'), "Guerra. A arte do conflito")

    def test_limite_sem_cortar_palavra(self):
        t = nomes.limpar("palavra " * 30, 100)
        self.assertLessEqual(len(t), 100)
        self.assertTrue(t.endswith("palavra"))

    def test_nome_arquivo(self):
        self.assertEqual(nomes.nome_arquivo("Hoffman", "Conflict in the 21st Century: the rise", 2007),
                         "Hoffman - Conflict in the 21st Century. The rise - 2007.pdf")
        self.assertEqual(nomes.nome_arquivo("", "", "", ".epub"), "Sem autor - Sem título.epub")

    def test_reservado(self):
        self.assertEqual(nomes.limpar("CON"), "_CON")

    def test_suavizar_caixa_alta(self):
        self.assertEqual(nomes.suavizar_titulo("A GUERRA HÍBRIDA: UM ESTUDO"), "A guerra híbrida: Um estudo")


class TestBuscaPorTitulo(unittest.TestCase):
    INFO = {"texto": "Poder militar: entre o autoritarismo e a democracia\nJorge Zaverucha\n" + "Texto do livro. " * 50,
            "titulo_fonte": "Poder militar: entre o autoritarismo e a democracia", "meta": {}}

    def achar(self, respostas):
        with mock.patch.object(metadados, "buscar_titulo", return_value=respostas):
            return metadados.por_titulo("poder militar.pdf", self.INFO, self.INFO["texto"])

    def test_aceita_titulo_e_autor_que_conferem(self):
        r = self.achar([({"sobrenome": "Zaverucha", "titulo": "Poder militar: entre o autoritarismo e a democracia",
                          "ano": "2001"}, "OpenAlex (título)", "x")])
        self.assertEqual((r["sobrenome"], r["ano"], r["confianca"]), ("Zaverucha", "2001", "media"))

    def test_recusa_resenha_obra_parecida_e_autor_ausente(self):
        self.assertIsNone(self.achar([
            ({"sobrenome": "Little", "titulo": "Zaverucha's Poder militar: entre o autoritarismo e a democracia, a review",
              "ano": "2003"}, "OpenAlex (título)", "x"),
            ({"sobrenome": "Zaverucha", "titulo": "Poder militar na democracia brasileira", "ano": "2010"}, "CrossRef (título)", "y"),
            ({"sobrenome": "戦略研究学会", "titulo": "Poder militar: entre o autoritarismo e a democracia", "ano": "2001"},
             "OpenAlex (título)", "z"),
        ]))


class TestComPasta(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.origem = os.path.join(self.tmp, "bagunca")
        self.destino = os.path.join(self.tmp, "Biblioteca")
        os.makedirs(os.path.join(self.origem, "sub"))
        dados = os.path.join(self.tmp, "dados")
        self.patch = mock.patch.dict(os.environ, {"APPDATA": dados, "XDG_CONFIG_HOME": dados})
        self.patch.start()
        criar_pdf(os.path.join(self.origem, "x1.pdf"), "Geopolítica do ciberespaço",
                  "A geopolítica e o poder dos Estados. " * 40, autor="Israel Frois")
        shutil.copy(os.path.join(self.origem, "x1.pdf"), os.path.join(self.origem, "sub", "copia.pdf"))
        criar_pdf(os.path.join(self.origem, "sub", "anotado.pdf"), "Geopolítica do ciberespaço",
                  "A geopolítica e o poder dos Estados. " * 40, autor="Israel Frois", anotar=True)
        criar_pdf(os.path.join(self.origem, "z.pdf"), "Terrorismo contemporâneo", "terrorismo " * 200)

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fluxo_completo_copiar_e_desfazer(self):
        itens = analise.analisar_pasta(self.origem, online=False)
        self.assertEqual(len(itens), 4)
        por_nome = {os.path.basename(i.caminho): i for i in itens}
        self.assertEqual(por_nome["x1.pdf"].sobrenome, "Frois")
        self.assertEqual(por_nome["anotado.pdf"].anotacoes, 1)
        # a cópia anotada é a principal; a cópia idêntica sem anotação sai da seleção
        self.assertFalse(por_nome["anotado.pdf"].duplicata_de)
        self.assertTrue(por_nome["copia.pdf"].duplicata_de)
        self.assertTrue(por_nome["x1.pdf"].duplicata_de)
        self.assertEqual(por_nome["z.pdf"].tema, "Terrorismo")

        for i in itens:
            if not i.duplicata_de:
                i.incluir, i.confianca = True, "manual"  # revisados na tabela
        organizar.planejar(itens, self.destino, organizar.POR_TEMA)
        self.assertEqual(por_nome["anotado.pdf"].destino,
                         os.path.join("Geopolítica", "Frois - Geopolítica do ciberespaço.pdf"))
        log, feitos, pulados = organizar.aplicar(itens, self.destino, "copiar")
        self.assertEqual((feitos, pulados), (2, []))
        self.assertTrue(os.path.exists(os.path.join(self.destino, por_nome["anotado.pdf"].destino)))
        self.assertTrue(os.path.exists(os.path.join(self.origem, "sub", "anotado.pdf")))  # original intacto

        desfeitos, avisos = organizar.desfazer(log)
        self.assertEqual((desfeitos, avisos), (2, []))
        self.assertFalse(os.path.exists(os.path.join(self.destino, "Geopolítica")))

    def test_mover_e_desfazer(self):
        itens = analise.analisar_pasta(self.origem, online=False)
        for i in itens:
            i.incluir = True
        organizar.planejar(itens, self.destino, organizar.POR_AUTOR)
        destinos = sorted(i.destino for i in itens)
        self.assertEqual(len(set(d.lower() for d in destinos)), 4)  # colisões ganham " (2)", " (3)"…
        log, feitos, _ = organizar.aplicar(itens, self.destino, "mover")
        self.assertEqual(feitos, 4)
        self.assertEqual(arquivos.listar(self.origem), [])
        organizar.desfazer(log)
        self.assertEqual(len(arquivos.listar(self.origem)), 4)

    def test_nao_confirmados_vao_para_pasta_de_confirmacao(self):
        itens = analise.analisar_pasta(self.origem, online=False)
        for i in itens:
            i.incluir = True
        alvo = itens[0]
        alvo.confianca = "baixa"
        organizar.planejar(itens, self.destino, organizar.POR_TEMA)
        self.assertEqual(alvo.destino, os.path.join(organizar.PASTA_CONFIRMAR, os.path.basename(alvo.caminho)))
        alvo.confianca = "manual"  # depois de revisado, segue a regra normal
        organizar.planejar(itens, self.destino, organizar.POR_TEMA)
        self.assertFalse(alvo.destino.startswith(organizar.PASTA_CONFIRMAR))

    def test_duplicatas_marcadas_vao_para_pasta_de_exclusao(self):
        itens = analise.analisar_pasta(self.origem, online=False)
        for i in itens:
            i.incluir, i.confianca = True, "manual"
        organizar.planejar(itens, self.destino, organizar.POR_TEMA)
        por_nome = {os.path.basename(i.caminho): i for i in itens}
        self.assertEqual(por_nome["copia.pdf"].destino, os.path.join(organizar.PASTA_DUPLICATAS, "copia.pdf"))
        self.assertTrue(por_nome["anotado.pdf"].destino.startswith("Geopolítica"))
        _, feitos, pulados = organizar.aplicar(itens, self.destino, "copiar")
        self.assertEqual(feitos, 2)  # no modo copiar as duplicatas não são separadas
        self.assertEqual(len(pulados), 2)

    def test_copia_ja_no_lugar_fica_e_original_vai_para_exclusao(self):
        itens = analise.analisar_pasta(self.origem, online=False)
        z = next(i for i in itens if i.caminho.endswith("z.pdf"))
        z.incluir, z.confianca = True, "manual"
        organizar.planejar(itens, self.origem, organizar.POR_TEMA)
        organizar.aplicar(itens, self.origem, "copiar")  # como na primeira rodada: cópia renomeada + original
        copia = os.path.join(self.origem, z.destino)

        itens = analise.analisar_pasta(self.origem, online=False)
        for i in itens:
            i.incluir, i.confianca = True, "manual"
        organizar.planejar(itens, self.origem, organizar.POR_TEMA)
        organizar.aplicar(itens, self.origem, "mover")
        self.assertTrue(os.path.exists(copia))
        self.assertFalse(os.path.exists(os.path.join(self.origem, "z.pdf")))
        self.assertTrue(os.path.exists(os.path.join(self.origem, organizar.PASTA_DUPLICATAS, "z.pdf")))

    def test_mover_com_identico_no_destino_separa_o_redundante(self):
        itens = analise.analisar_pasta(self.origem, online=False)
        z = next(i for i in itens if i.caminho.endswith("z.pdf"))
        z.incluir, z.confianca = True, "manual"
        organizar.planejar(itens, self.destino, organizar.POR_TEMA)
        os.makedirs(os.path.dirname(os.path.join(self.destino, z.destino)))
        shutil.copy(z.caminho, os.path.join(self.destino, z.destino))
        log, feitos, pulados = organizar.aplicar(itens, self.destino, "mover")
        self.assertEqual((feitos, pulados), (1, []))
        self.assertTrue(os.path.exists(os.path.join(self.destino, organizar.PASTA_DUPLICATAS, "z.pdf")))
        organizar.desfazer(log)
        self.assertTrue(os.path.exists(z.caminho))

    def test_offline_titulo_e_autor_da_capa(self):
        caminho = os.path.join(self.origem, "artigo123.pdf")
        doc = pymupdf.open()
        doc.new_page()  # 1ª página em branco (capa em imagem, por exemplo)
        pg = doc.new_page()
        pg.insert_text((72, 100), "Poder militar entre o autoritarismo e a democracia", fontsize=20)
        pg.insert_text((72, 130), "Jorge Zaverucha¹", fontsize=11)
        pg.insert_text((72, 150), "Universidade Federal de Pernambuco", fontsize=9)
        pg.insert_textbox(pymupdf.Rect(72, 180, 520, 780), "Texto do artigo, publicado em 2001. " * 40, fontsize=9)
        doc.save(caminho)
        doc.close()
        r = metadados.identificar(caminho, arquivos.ler(caminho), online=False)
        self.assertEqual((r["sobrenome"], r["ano"], r["confianca"]), ("Zaverucha", "2001", "media"))

    def test_offline_nome_do_arquivo_conferido(self):
        caminho = os.path.join(self.origem, "Frois - Geopolítica do ciberespaço - 2018.pdf")
        criar_pdf(caminho, "Revista de Geopolítica", "Geopolítica do ciberespaço. Israel Frois. " * 30)
        r = metadados.identificar(caminho, arquivos.ler(caminho), online=False)
        self.assertEqual((r["sobrenome"], r["ano"], r["fonte"]), ("Frois", "2018", "nome do arquivo"))

    def test_offline_capa_abnt_com_autor_acima_do_titulo(self):
        caminho = os.path.join(self.origem, "cec1d4d9.pdf")
        doc = pymupdf.open()
        pg = doc.new_page()
        pg.insert_text((72, 100), "FACULDADE DE DIREITO DO SUL DE MINAS", fontsize=16)
        pg.insert_text((72, 200), "HENRIQUE VIEGAS CUNHA", fontsize=16)
        pg.insert_text((72, 400), "A SOBERANIA NA ERA DA INFORMAÇÃO", fontsize=18)
        pg.insert_text((72, 700), "POUSO ALEGRE - MG", fontsize=12)
        pg.insert_text((72, 720), "2018", fontsize=12)
        doc.new_page().insert_textbox(pymupdf.Rect(72, 72, 520, 780), "Texto da monografia. " * 80, fontsize=12)
        doc.save(caminho)
        doc.close()
        r = metadados.identificar(caminho, arquivos.ler(caminho), online=False)
        self.assertEqual((r["sobrenome"], r["ano"], r["fonte"]), ("Cunha", "2018", "capa do arquivo"))

    def test_offline_titulo_em_negrito_do_tamanho_do_corpo(self):
        caminho = os.path.join(self.origem, "A_INFLUENCIA_DA_MIDIA.pdf")
        doc = pymupdf.open()
        pg = doc.new_page()
        pg.insert_text((72, 80), "A influência da mídia no processo penal", fontsize=12, fontname="hebo")
        pg.insert_text((72, 110), "Sarah Mendes Cunha 1", fontsize=12)
        pg.insert_textbox(pymupdf.Rect(72, 140, 520, 780), "Texto do artigo, publicado em 2023. " * 60, fontsize=12)
        doc.save(caminho)
        doc.close()
        r = metadados.identificar(caminho, arquivos.ler(caminho), online=False)
        self.assertEqual((r["sobrenome"], r["titulo"]), ("Cunha", "A influência da mídia no processo penal"))

    def test_nome_de_pessoa_limpa_marcas_e_recusa_instituicoes(self):
        self.assertEqual(arquivos.nome_de_pessoa("Peterson Ferreira da Silva "), "Peterson Ferreira da Silva")
        self.assertEqual(arquivos.nome_de_pessoa("RODRIGO SANTOS COSTA - Maj Art"), "RODRIGO SANTOS COSTA")
        self.assertEqual(arquivos.nome_de_pessoa("1 st Luiz Gustavo Lavandoski da Silva"), "Luiz Gustavo Lavandoski da Silva")
        self.assertEqual(arquivos.nome_de_pessoa("Milton do Nascimento"), "Milton do Nascimento")
        for nao in ("POUSO ALEGRE - MG", "COMANDO DE OPERAÇÕES TERRESTRES", "ESTADO-MAIOR CONJUNTO DAS FORÇAS ARMADAS"):
            self.assertEqual(arquivos.nome_de_pessoa(nao), "")

    def test_hash_guardado_e_refeito_quando_o_arquivo_muda(self):
        p = os.path.join(self.origem, "z.pdf")
        h = arquivos.sha1(p)
        with mock.patch("builtins.open", side_effect=AssertionError("não deveria reler")):
            self.assertEqual(arquivos.sha1(p), h)  # tamanho e data iguais: usa o guardado
        with open(p, "ab") as f:
            f.write(b"%anotado")
        self.assertNotEqual(arquivos.sha1(p), h)

    def test_revisao_de_temas_renomeia_junta_e_cria(self):
        base = temas.carregar()
        self.assertIn("Defesa e Estratégia", base)
        novos = {"Drones": temas.palavras_chave("drone, sarp; vant")}
        self.assertEqual(novos["Drones"], "drone|sarp|vant")
        t = temas.aplicar_revisao({"Defesa e Estratégia": "Defesa e Estratégia Nacional",  # renomeia
                                   "Tecnologia e Sociedade": "Geopolítica",                 # junta
                                   "Metodologia": temas.OUTROS}, novos)                     # desfaz
        self.assertNotIn("Defesa e Estratégia", t)
        self.assertEqual(t["Defesa e Estratégia Nacional"], base["Defesa e Estratégia"])
        self.assertTrue(t["Geopolítica"].endswith(base["Tecnologia e Sociedade"]))
        self.assertNotIn("Metodologia", t)
        self.assertEqual(temas.sugerir("uso de sarp na guerra", t), "Drones")
        self.assertEqual(temas.carregar(), t)  # ficou guardado

    def test_outros_formatos_vao_para_pasta_propria(self):
        with open(os.path.join(self.origem, "anotacoes.docx"), "wb") as f:
            f.write(b"PK docx de exemplo")
        itens = analise.analisar_pasta(self.origem, online=False)
        doc = next(i for i in itens if i.caminho.endswith(".docx"))
        self.assertEqual(doc.fonte, "outro formato")
        doc.incluir = True
        organizar.planejar(itens, self.destino, organizar.POR_TEMA)
        self.assertEqual(doc.destino, os.path.join(organizar.PASTA_FORMATOS, "anotacoes.docx"))

    def test_pasta_analisada_dentro_do_destino_e_lida(self):
        # analisar D:\Biblioteca\_A confirmar com destino D:\Biblioteca
        confirmar = os.path.join(self.destino, organizar.PASTA_CONFIRMAR)
        os.makedirs(confirmar)
        shutil.copy(os.path.join(self.origem, "z.pdf"), confirmar)
        ign = analise.pastas_ignoradas(confirmar, self.destino)
        self.assertEqual(ign, [os.path.join(self.destino, organizar.PASTA_DUPLICATAS)])
        self.assertEqual(len(analise.analisar_pasta(confirmar, online=False, ignorar=ign)), 1)
        # o contrário: destino dentro da pasta analisada continua fora da análise
        dentro = os.path.join(self.origem, "Organizados")
        self.assertIn(dentro, analise.pastas_ignoradas(self.origem, dentro))

    def test_csv_ida_e_volta(self):
        itens = analise.analisar_pasta(self.origem, online=False)
        csv_path = os.path.join(self.tmp, "e.csv")
        organizar.exportar_csv(itens, csv_path)
        with open(csv_path, encoding="utf-8-sig") as f:
            texto = f.read().replace("Frois", "Fróis")
        with open(csv_path, "w", encoding="utf-8-sig") as f:
            f.write(texto)
        self.assertEqual(organizar.importar_csv(itens, csv_path), 3)
        self.assertTrue(all(i.sobrenome == "Fróis" for i in itens if "z.pdf" not in i.caminho))

    def test_cache_reaproveitado(self):
        analise.analisar_pasta(self.origem, online=False)
        with mock.patch.object(arquivos, "ler", side_effect=AssertionError("não deveria reler")):
            self.assertEqual(len(analise.analisar_pasta(self.origem, online=False)), 4)
        self.assertGreater(config.Cache().limpar(), 0)


if __name__ == "__main__":
    unittest.main()
