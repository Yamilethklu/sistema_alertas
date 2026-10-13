import unittest

from alertas import matcher, seeds

RULES = matcher.build_rules(seeds.INTENCION, seeds.PRODUCTO, seeds.EXCLUIR)


class MatcherTests(unittest.TestCase):
    def ok(self, t, **kw):
        return matcher.evaluate(t, RULES, **kw).ok

    def test_compradores(self):
        for t in ["¿Alguien sabe dónde compro una laptop barata?",
                  "Busco un taladro bueno, recomiendan alguno?",
                  "¿Qué audífonos recomiendan para correr?",
                  "Cuánto cuesta un iPhone 13 en oferta"]:
            self.assertTrue(self.ok(t), t)

    def test_vendedores_excluidos(self):
        self.assertFalse(self.ok("Vendo laptop HP, busco ofertas de cambio"))
        self.assertFalse(self.ok("Se vende televisor, recomiendo mucho"))

    def test_sin_senal(self):
        for t in ["Buenos días a todos", "Me compré un iPhone ayer", "ok", "Busco trabajo"]:
            self.assertFalse(self.ok(t), t)

    def test_modo_amplio(self):
        self.assertFalse(self.ok("Busco algo para regalar", require_product=True))
        self.assertTrue(self.ok("Busco algo para regalar", require_product=False))

    def test_plurales_y_acentos(self):
        self.assertTrue(self.ok("Necesito unos audifonos"))
        self.assertTrue(self.ok("NECESITO UNAS LAPTOPS"))

    def test_palabra_completa(self):
        self.assertFalse(self.ok("Busco un tvdinner raro"))  # 'tv' dentro de otra palabra

    def test_minimo(self):
        self.assertFalse(self.ok("¿Qué tal el iPhone?", min_score=5))


if __name__ == "__main__":
    unittest.main()
