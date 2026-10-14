# Validação — 2026-10-14

## Executado

- 18 testes pytest passaram no Python 3.12.7, incluindo MCP HTTP real (sem Codex),
  descoberta de sete ferramentas, schemas, OAuth, autenticações isoladas,
  idempotência/conflito, reserva concorrente, renovação/expiração, continuação,
  cancelamento, timeout, desconexão Codex, outbox após perda de rede, reinício sem
  replay, journal/approvals, workspace/symlinks, persistência, paginação e reconciliação.
- Ruff check e format executados.
- Codex CLI 0.162.1 autenticado: handshake stdio e model/list realmente executados.
- Smoke **real** em processos separados: cliente MCP -> servidor -> worker -> Codex.
  Resposta “Ponte operacional”, thread retomada em novo App Server, recordação correta
  e arquivo inofensivo criado e conferido.
- Build Docker na VM com dependências hash-locked executado com sucesso. Corrigidos
  ownership do código e diretórios temporários Nginx durante o teste de containers.
- HTTPS público: health mínimo 200, metadata e discovery 200, issuer correto e S256.
  MCP e API worker sem credenciais: 401.
- OAuth **real** Keycloak: autorização por código com PKCE S256, audiência, scope e
  subject verificados; inicialização MCP autenticada passou.
- Smoke **real pela cloud**: MCP HTTPS/OAuth -> VM -> worker residencial -> Codex,
  com resposta, thread retomada e conteúdo de arquivo confirmado. Worker como serviço
  systemd de usuário ativo; unit e segredos fora do repositório público.
- Cliente OAuth temporário de smoke removido e sessões de teste revogadas ao concluir.
  Conta proprietária exige troca da senha inicial no primeiro login.

## Pendente de validação externa

- Cadastro/consentimento e chamada originada no ChatGPT. Smoke local não comprova isso.
- URI de callback deve corresponder à exibida na interface ChatGPT; configuração
  inclui a URI estável documentada, sem wildcards.

## Limitações dos testes

Falhas e reinícios são simulados deterministicamente com adaptador fake, mais reabertura
dos bancos. Isso não simula todos os efeitos de power loss/OS/children executando operações
externas. Teste real demonstrou continuidade entre processos, não recuperação automática
de turno ativo após queda. Não foram feitos load test, pentest ou certificado de segurança.
Evidências com IDs/arquivos reais ficam no registro privado de implantação, fora do Git.
