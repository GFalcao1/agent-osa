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

Spec v1 aprovada em 2026-10-15 após revisão de direção. TASK-101 (fundação:
configuração, logs e CLI), TASK-102 (banco, modelo de dados e auditoria) e
TASK-103 (cadastro, painel local e bootstrap) estão implementadas em `main`; o
aceite de cada gate fica no registro de execução da própria task. A próxima da
ordem é a TASK-104 (filesystem seguro e primitiva de arquivamento).

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
`LLMProvider`, pypdfium2, python-docx, openpyxl, RapidOCR (OCR local), structlog, pytest,
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

## Ícones, fontes e animações do painel

O painel não carrega nada de fora: ícones, fontes, animações e scripts são
arquivos do próprio pacote, servidos pelo painel com os mesmos filtros e
cabeçalhos de segurança do restante (a CSP não muda). Licenças em
`THIRD_PARTY_NOTICES.md`.

- **Ícones**: SVGs do Phosphor Icons (`@phosphor-icons/core`, MIT), só os que
  as telas usam, em `src/osa/panel/static/icons/`. Os templates chamam
  `{{ icon("house") }}` ou `{{ icon("lock", "duotone") }}`; o nome vem do
  template, nunca da requisição, e um nome fora do conjunto levanta erro.
- **Marca**: o escudo do cabeçalho e o `favicon.svg` são desenho próprio (não Phosphor);
  sem JavaScript, a marca faz um "pop" curto, em CSS, ao passar o mouse, focar ou
  clicar; com JavaScript, `/motion.js` troca o pop por uma mola que segue o
  ponteiro só enquanto ele está sobre a marca (e afunda de leve no clique). Com
  `prefers-reduced-motion: reduce` nada se mexe.
- **Animações**: três arquivos Lottie pequenos (check de sucesso, spinner do botão e
  sol/lua do tema), gerados por
  `tools/lottie/generate.py` (`uv run python tools/lottie/generate.py`), tocados
  pelo `lottie-web` na versão leve (sem motor de expressões) e orquestrados por
  `/motion.js`. Ao abrir uma página, os blocos entram com um "pop" curto e
  escalonado (menos de 0,7 s, só CSS) e o ícone da seção atual dá um pop; os ícones
  do menu também dão um pop (só escala, sem giro nem brilho) ao passar o mouse, ao
  focar e no clique. O fundo é uma
  retícula fina com lavagens suaves de cor, uma camada por tema. Cartões e faixas têm sombra
  suave em camadas e um filete dourado no alto, que se alarga quando um cartão com link sobe
  de leve sob o mouse; botões principais e os quadrados numerados do manual têm um anel
  dourado fino por dentro. Cantos quase retos: 2 px em controles, selos e etiquetas, 3 px
  em alertas e caixas pequenas, 4 px em cartões; só a bolinha da chave de tema é redonda. São melhoria progressiva: sem
  JavaScript, ou se um arquivo não carregar, a tela mostra o ícone estático e o
  texto. Com `prefers-reduced-motion: reduce` nada toca nem repete; a preferência
  é seguida na hora, sem recarregar. O mesmo `/motion.js` destaca o botão
  "Salvar" de uma linha de tabela só enquanto algum campo dela mudou.
- **Manifesto**: `src/osa/panel/static/MANIFEST.json` guarda versão, hash de
  integridade do registro npm e o sha256 de cada arquivo entregue. O painel
  confere tudo (inclusive cada ícone e cada fonte) ao iniciar e recusa subir se um
  arquivo falta ou não bate com o manifesto.

### Fontes e tipografia

Texto, controles e interface usam **IBM Plex Sans** (pesos 400, 500 e 600, um arquivo
estático por peso); títulos de página, títulos de cartão, legendas de formulário e os
números da página inicial usam **IBM Plex Serif** (peso 600); pastas e códigos usam
**IBM Plex Mono** (peso 400). Os arquivos WOFF2 são os que a própria IBM, detentora do
copyright, gera e publica nos pacotes oficiais `@ibm/plex-sans` 1.1.0, `@ibm/plex-serif`
2.0.0 e `@ibm/plex-mono` 2.5.0 (recortes `Latin1` e `Latin2` de `fonts/split/woff2`),
copiados sem alteração, com o `LICENSE.txt` de cada pacote ao lado em
`src/osa/panel/static/fonts/`. As três têm licença SIL OFL 1.1 com Reserved Font Name
"Plex" (texto completo e linhas de copyright em `THIRD_PARTY_NOTICES.md`); como não há
versão modificada, o nome interno das fontes fica como a IBM gravou. O CSS do painel
declara as famílias com apelidos neutros ("Panel Sans", "Panel Serif", "Panel Mono" e as
faces de reserva "... Fallback"), que não colidem com uma fonte instalada; o crédito à
IBM fica nos avisos.

- **Uma chave**: `DISPLAY` em `src/osa/panel/styles.py`. `"serif"` (padrão) põe a
  serifada nos títulos e números; `"sans"` deixa tudo em IBM Plex Sans. A serifada
  nunca vai para texto pequeno de interface nem para campos de formulário.
- **Tamanho**: no primeiro carregamento vão só os arquivos `latin1`: Sans 20 984 B (400),
  21 960 B (500) e 22 260 B (600), Serif 24 016 B e Mono 17 544 B, cerca de 104 KiB
  (81 KiB com `"sans"`); os `latin2` (latino estendido) da Sans (15 212 B, 15 372 B e
  15 624 B) e da Serif (16 144 B) só descem quando uma página usa um desses caracteres
  (`unicode-range`). O navegador só baixa uma face que a página usa.
- **Rota**: `/fonts/<nome>.<hash>.woff2`, uma rota de caminho exato por arquivo, sob
  os mesmos filtros de peer e `Host`. O hash são os dez primeiros dígitos do sha256
  do arquivo, lidos do manifesto; por isso essas nove respostas, e só elas, saem com
  `Cache-Control: private, max-age=31536000, immutable`. Todo o resto continua
  `no-store`.
- **CSS**: nenhuma diretiva `font-src` (cai em `default-src 'self'`); `@font-face`
  com `font-display: swap` e o `unicode-range` exato de cada recorte da IBM,
  `<link rel="preload">` da Sans 400 e (com `"serif"`) da Serif, e uma face de reserva
  por família com as métricas da fonte (Arial, Georgia e Courier New ajustadas; sem
  salto de layout). Família, escala, três pesos, entrelinhas e espaçamento de letras
  são variáveis CSS; um teste recusa qualquer fonte, tamanho ou peso escrito fora delas.

Atualizar uma fonte (precisa de Node/npm e rede): troque a versão e a integridade do
pacote (`npm view @ibm/plex-sans@<versão> dist.integrity`, e o mesmo para
`@ibm/plex-serif` e `@ibm/plex-mono`) em `PINS` de `tools/assets/vendor.py` e rode
`fetch`, `manifest` e `check` como acima. Use só pacotes publicados pela IBM: um recorte
ou conversão feito por terceiros é "Modified Version" na OFL e não pode usar o nome
reservado. O hash no nome do arquivo muda sozinho, o que invalida o cache dos
navegadores. Trocar de família: os nomes lógicos (`sans-*`, `serif-*`, `mono-*`) em
`src/osa/panel/assets.py` não mudam; mudam a tabela de arquivos, os pins e as regras
`@font-face` de `styles.py`.

Atualizar um pacote (precisa de Node/npm e rede): troque a versão e o hash de
integridade (`npm view <pacote>@<versão> dist.integrity`) em
`tools/assets/vendor.py`, depois:

```sh
uv run python tools/assets/vendor.py fetch      # baixa, confere o hash e copia só o que se usa
uv run python tools/assets/vendor.py manifest   # recalcula os sha256
uv run python tools/assets/vendor.py check      # confere manifesto x arquivos x versões fixadas
uv run pytest -q
```

Depois de editar uma animação em `tools/lottie/generate.py`, rode o gerador e o
passo `manifest`.

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
dependem de ambiente real e cada skip fica no registro da task. A `live` roda com
`OSA_TEST_OLLAMA_URL` (só loopback; o túnel SSH para o Mac mini serve), `OSA_TEST_OLLAMA_MODEL`
e, de preferência, `OSA_TEST_OLLAMA_DIGEST` e `OSA_TEST_OLLAMA_REPORT_DIR` (onde guardar os
relatórios): `uv run pytest -m live -q`. A mesma avaliação, à mão e com vários modelos:
`uv run osa eval --corpus tests/fixtures/corpus --model A --digest X --model B --digest Y`. A suite de
integração precisa de um PostgreSQL descartável cujo papel tenha `CREATEDB`
(ela cria e apaga bancos `osa_test_mig_*`) e aplica `alembic upgrade head`
sozinha. O CI não usa segredos: as credenciais do container são fictícias e
descartáveis. `.github/dependabot.yml` propõe atualizações semanais agrupadas
das actions (fixadas por SHA) e das dependências Python.
