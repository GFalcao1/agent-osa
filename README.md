# Agent O.S.A.

**Organizator Sender Archiver** — arquivamento e consulta de documentos de uma
empresa por Telegram, com IA escrevendo o envelope e código fazendo a entrega.

Um operador envia um documento pelo Telegram dizendo de quem ele é. O agente
lê o conteúdo com um modelo local, classifica o departamento dentro da
estrutura padrão que a empresa cadastrou e arquiva o arquivo no share de rede.
Quando não há certeza, o caso vai para um revisor com botões. Depois, qualquer
operador pede "manda o contrato da Alfa de 2025" e recebe o arquivo. Uma pasta
monitorada recebe o mesmo tratamento para arquivos largados direto no servidor.

## Estado atual

Spec v1 aprovada em 2026-10-15 após revisão de direção. Nenhuma task v1
implementada. O código presente é a fundação de configuração aceita no plano
v0, que a TASK-101 adapta.

`docs/` e `tasks/` estão no `.gitignore` nesta fase; existem localmente.

- Especificação: `docs/SPEC.md`
- Vocabulário: `CONTEXT.md`
- Decisões: `docs/adr/`
- Tasks e ordem: `tasks/README.md`
- Plano v0 arquivado: `docs/archive/v0-plan/`

## Princípios

- **Carta e correio.** O modelo só produz JSON validado por schema. Efeitos
  (arquivar, criar pasta, enviar, desfazer) são código determinístico que
  revalida dono, template e raiz antes de agir.
- **Conteúdo fica local.** Modelo servido por Ollama na própria máquina do
  agente. Texto ou imagem de documento não sai dela.
- **Arquivamento automático só com evidência.** Dono declarado pelo operador ou
  CNPJ/alias cadastrado encontrado no texto, e departamento sem reserva do
  modelo. Caso contrário, revisão humana.
- **Sem delete, sem overwrite.** Rename dentro da mesma raiz, sufixo em
  colisão, desfazer por operação.
- **Classe sensível.** Certificados digitais e chaves nunca são lidos, nunca
  vão ao modelo, nunca trafegam pelo Telegram.
- **Auditoria imutável** de cada pedido, decisão, arquivamento e envio.

## Arquitetura

```text
Telegram ──▶ osa-bot ──▶ correio ──▶ índice (PostgreSQL)
                          │  ▲          │
Pasta monitorada ──▶ osa-worker         │
                          │  │          ▼
                          ▼  └── envelope (Ollama, JSON por schema)
                   filesystem (raízes no share SMB)

Painel local (127.0.0.1): raízes, template, donos, usuários, modelo
```

Dois processos sobre o mesmo banco, numa instalação por empresa. O piloto roda
num Mac mini M4 de 24 GB com o share SMB de um servidor Windows montado por
conta de serviço.

## Stack

Python 3.12, python-telegram-bot, FastAPI (painel local), PostgreSQL com
SQLAlchemy e Alembic, Ollama com Gemma 3 12B atrás de uma interface
`LLMProvider`, PyMuPDF, python-docx, openpyxl, OCR local, structlog, pytest,
Ruff e mypy.

## Entregas

1. **Guardar** — upload pelo Telegram com dono declarado, classificação,
   arquivamento automático ou revisão. Tasks 101–108.
2. **Achar** — scan do acervo existente, consulta em linguagem natural, envio.
   Tasks 109–110.
3. **Vigiar** — pasta monitorada com inferência de dono. Task 111.
4. **Homologação** no piloto: segurança, operação, backup. Task 112.

## Desenvolvimento

```sh
uv sync --group dev
uv run pytest -q
```

Trabalho em uma task por vez, conforme `AGENTS.md`. Suites `integration`,
`native` e `live` dependem de PostgreSQL descartável, share SMB de teste e
Ollama, respectivamente, e só rodam quando configuradas.
