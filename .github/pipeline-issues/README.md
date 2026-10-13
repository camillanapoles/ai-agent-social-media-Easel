# Templates das issues do pipeline (`[Pipeline #N]`)

Cada arquivo `<N>.md` aqui dentro vira o conteúdo da issue `N` quando
`scripts/issue_chain.py` precisa criá-la (seja via `--init`, seja ao avançar
automaticamente de `N-1` para `N`):

- **1ª linha** do arquivo → vira o sufixo do título da issue:
  `[Pipeline #N] <1ª linha>`
- **Restante do arquivo** → vira o corpo da issue.

Se não existir um template `N.md` para o índice pedido, o script gera uma
issue genérica de placeholder (`Etapa N`) avisando que falta o template —
nada quebra, mas o ideal é sempre adicionar o arquivo correspondente antes de
a etapa N ser alcançada.

Este diretório não é lido pelo GitHub automaticamente — é lido apenas por
`scripts/issue_chain.py` no momento de criar a próxima issue indexada.
