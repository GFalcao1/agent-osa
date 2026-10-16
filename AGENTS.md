# Agent O.S.A. — fluxo de trabalho

Ler `docs/SPEC.md` (produto e regras), `CONTEXT.md` (vocabulário),
`tasks/README.md` (ordem) e `tasks/GATES.md` (verificação) antes de mudar
código. Implementar somente a task atual, uma por vez, dentro dos arquivos que
ela declara, com dados fictícios.

Carta e correio: o modelo devolve envelopes validados por schema; arquivar,
criar pasta, enviar e desfazer existem só em código determinístico. Um efeito
novo entra no correio, nunca como tool do modelo.

Defaults a preservar: efeitos de filesystem desligados, provedor externo
desligado, classe sensível nunca lida nem enviada. Conteúdo de documento fica
na máquina.

Suites native (share SMB) e live (Ollama) rodam quando configuradas; cada skip
aparece no registro da task. Terminar a task preenchendo o "Registro de
execução" com data, comandos, exit codes e limitações; o gate fecha com
revisão humana. Decisões difíceis de reverter vão para `docs/adr/`.
