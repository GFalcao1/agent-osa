"""Erros finos da camada de filesystem, sobre ``osa.domain.errors``."""

from __future__ import annotations

from osa.domain.errors import Conflict, DomainError, NotFound, UnsafePath


class CrossRoot(UnsafePath):
    """Caminho fora da raiz, em outra raiz, ou destino dentro da quarentena."""


class CrossDevice(UnsafePath):
    """Origem e destino estão em volumes diferentes (``EXDEV`` ou ``st_dev`` diferente)."""


class NameTooLong(UnsafePath):
    """O nome (com o sufixo `` (n)``) ou o caminho passa do limite do share.

    ``suffix`` é o ``n`` que estourou o limite (1 = o nome sozinho), para o correio saber se
    o problema foi o nome ou o sufixo. O nome nunca é truncado.
    """

    def __init__(self, message: str, *, suffix: int = 1) -> None:
        super().__init__(message)
        self.suffix = suffix


class DestinationMissing(NotFound):
    """A pasta de destino não existe; a camada de filesystem nunca cria pasta no arquivamento."""


class IdentityChanged(Conflict):
    """A origem foi trocada ou alterada durante a operação.

    A conferência de identidade é **posterior** ao rename: ela detecta, não impede, a
    movimentação de um arquivo trocado. ``destino`` informa onde o arquivo foi parar (relativo
    à raiz), para auditoria e desfazer manual; não há rollback automático.
    """

    def __init__(self, message: str, *, destino: str | None = None) -> None:
        super().__init__(message)
        self.destino = destino


class CollisionExhausted(Conflict):
    """Todos os sufixos até ``MAX_SUFFIX`` estão ocupados."""


class NoReplaceUnavailable(DomainError):
    """Sem rename exclusivo comprovado no volume; nenhum efeito aconteceu.

    Não existe alternativa com reserva de nome, ``link`` ou remoção: o caso vai para revisão.
    """
