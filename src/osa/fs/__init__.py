"""As únicas operações de filesystem do produto (SPEC §10).

Nenhum módulo deste pacote apaga, sobrescreve, trunca ou move por API que substitua o
destino; ``tests/unit/test_fs_static.py`` garante a higiene e os testes de colisão, de
corrida e a suite native provam a ausência de overwrite.
"""
