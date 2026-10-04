import os
import shutil
import tempfile
import unittest
from unittest import mock

import pymupdf

from estante import analise, arquivos, config, nomes, organizar


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
                i.incluir = True
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
