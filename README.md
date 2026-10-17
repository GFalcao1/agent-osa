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

## CI

`.github/workflows/ci.yml` roda o gate comum de `tasks/GATES.md` em todo pull
request e em push para `main`, com Python 3.12 (o mínimo de `requires-python`;
uma matriz só valeria o custo com mais de uma versão suportada). Jobs, em
paralelo, e como reproduzir cada um localmente:

| Job | Prova | Localmente |
|---|---|---|
| `lint` | Estilo e imports | `uv run ruff check . && uv run ruff format --check .` |
| `typecheck` | mypy estrito em `src/osa` | `uv run mypy src/osa` |
| `hygiene` | `uv.lock` coerente com `pyproject.toml`; nenhum `.env`, chave ou string com cara de segredo versionado | `uv lock --check` e os `git grep` do próprio workflow |
| `tests-portable` | Suite portável sem banco e sem variável `OSA_*`; falha com zero testes coletados ou com os testes do tema (Node.js) pulados | `uv run pytest -q` (com `node` no PATH) |
| `tests-integration` | Suite de integração contra PostgreSQL 17 descartável, com papel sem SUPERUSER (só CREATEDB) e banco `osa_test` | `OSA_TEST_DATABASE_URL=postgresql://<papel>:<senha>@127.0.0.1:5432/osa_test uv run pytest -m integration -q` |

As suites `native` (share SMB de teste) e `live` (Ollama) **não** rodam no CI:
dependem de ambiente real e cada skip fica no registro da task. A suite de
integração precisa de um PostgreSQL descartável cujo papel tenha `CREATEDB`
(ela cria e apaga bancos `osa_test_mig_*`) e aplica `alembic upgrade head`
sozinha. O CI não usa segredos: as credenciais do container são fictícias e
descartáveis. `.github/dependabot.yml` propõe atualizações semanais agrupadas
das actions (fixadas por SHA) e das dependências Python.
