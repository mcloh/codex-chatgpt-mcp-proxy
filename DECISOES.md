# Decisões e fontes

Referência: 2026-10-14. Fontes externas podem evoluir; os schemas da instalação são a
referência concreta para o adaptador.

| Decisão | Motivo e evidência |
|---|---|
| Python 3.12, SDK MCP 2.3.0 | Instalação e transporte testados no runtime 3.12.7; dependências fixadas em uv.lock e requirements.lock com hashes |
| SQLite com WAL e BEGIN IMMEDIATE | Aquisição atômica, sem mensageria ou banco externo |
| MCP Streamable HTTP stateless | Ferramentas sem estado de sessão; estado da tarefa está no SQLite |
| App Server por stdio, processo próprio por tarefa | Isola esta ponte da sessão interativa; continuação usa thread/resume |
| OAuth externo | Recurso valida tokens, sem implementar authorization server caseiro |
| Token worker separado | Não pode chamar ferramentas MCP; OAuth MCP não autentica a API worker |
| Reserva expirada em reconciliação | Evita repetição de efeitos locais quando o servidor perde contato |
| Aprovação local, unitária | Nada de aceite remoto, bypass ou regras persistentes adicionadas pela ponte |
| Headers encaminhados ignorados | Evita confiança em X-Forwarded-* enviado por clientes; proxy preserva Host |
| Imagens por digest e hashes Python | Distribuição reproduzível sem atualização automática de runtime |

## Protocolo observado

Comandos executados: `codex --version`, `codex login status`, `codex app-server --help`,
`codex app-server generate-json-schema --out protocol`. A geração foi feita com 0.162.1;
`protocol/` é artefato local regenerável e não entra no repositório.

- Frames JSON separados por newline, sem prefixo Content-Length.
- `initialize`: `clientInfo` (name/version), capabilities; depois `initialized`.
- `thread/start`: cwd, sandbox, approvalPolicy, approvalsReviewer e model opcional.
- `thread/resume`: threadId e os mesmos controles explícitos.
- `turn/start`: threadId e input `[{"type":"text","text":"..."}]`; retorna turn.id.
- `turn/interrupt`: threadId e turnId. A confirmação final depende de turn/completed.
- `item/completed`: agentMessage.text fornece a resposta.
- `turn/completed`: status completed, interrupted ou failed.
- Requests `item/commandExecution/requestApproval` e
  `item/fileChange/requestApproval`: resposta `{"decision":"accept|decline|cancel"}`.
- Métodos de aprovação não implementados recebem erro JSON-RPC -32601 e evento de
  recusa. Não são aprovados automaticamente.
- `model/list` consultado para verificar o modelo do smoke.

A autenticação salva é usada pelo processo local; nenhuma credencial Codex segue à VM.
Retomada de thread entre processos foi testada realmente. Não se promete recuperação de
um turno interrompido por queda do processo nem de rollout removido do perfil Codex.

## Recursos e modelos

Planejamento, implementação e revisão nesta sessão Codex; ferramentas determinísticas
para banco, HTTP, Git e testes. Smoke com gpt-6-luna, adequado às três tarefas mínimas e
confirmado em model/list. Não houve inferência em APIs de fornecedores auxiliares nem
transferência de credenciais/conteúdo privado para elas. Consumo financeiro não medido. A configuração de uso cotidiano escolhe gpt-6.1-sol
para tarefas de código; planejamento complexo pode usar gpt-6-astra por configuração
local deliberada. Disponibilidade foi verificada em model/list; apenas o modelo de
smoke foi submetido aos testes reais registrados nesta entrega.

## Fontes primárias

- [Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [Autenticação de MCP no ChatGPT](https://developers.openai.com/plugins/build/auth)
- [Cadastro de servidor personalizado](https://developers.openai.com/api/docs/guides/custom-mcp-server)
- [SDK Python oficial MCP](https://github.com/modelcontextprotocol/python-sdk)
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [Keycloak e MCP](https://www.keycloak.org/securing-apps/mcp-authz-server)
- [Keycloak OpenID Connect](https://www.keycloak.org/securing-apps/oidc-layers)
- [WHM addzonerecord](https://api.docs.cpanel.net/specifications/whm.openapi/domain-management/dns-addzonerecord)

O guia Keycloak documenta tanto resource-indicators (experimental) quanto o binding por
scope/audience mapper. Para um cliente previamente registrado da ponte, usa-se audiência
fixa associada a bridge:access. Nenhum recurso experimental global do Keycloak existente
é ativado como efeito colateral; CIMD/DCR são evoluções separadas.
