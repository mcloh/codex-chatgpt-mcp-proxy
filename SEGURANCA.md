# Limites de proteção

- Uma instalação pessoal com um proprietário e um worker, sem isolamento multi-tenant.
- Workspace allowlist escolhe o cwd; não é uma política de isolamento completa. Leitura
  e escrita efetivas dependem da sandbox real Codex e das permissões do usuário Linux.
  Em workspace-write, o modelo pode ler outras pastas que a sandbox permitir.
- IDs recebidos no MCP não viram caminhos. A allowlist local exige canonicalização,
  rejeita symlinks no caminho e é revalidada antes de iniciar cada tarefa. Symlinks
  dentro do workspace e mudanças concorrentes do filesystem dependem da sandbox.
- App Server stdio dedicado, on-request e reviewer user; sem bypass/danger-full-access
  ou aceite remoto. Requests não implementados são recusados explicitamente.
- Token worker, OAuth client secret e JWT têm funções distintas. BRIDGE_* não entra
  no ambiente Codex. Isso não promete ocultar arquivos privados que o usuário local
  pode ler; para fronteira mais rígida, usar conta/sandbox separadas e validar o Codex.
- Resultados do LLM podem conter segredos inadvertidamente. Não há filtragem perfeita;
  limite instruções e pastas autorizadas, inspecione outputs e não trabalhe com segredos
  em workspaces de demonstração. A cloud armazena instruções/resultados, conforme retenção.
- Logs HTTP não incluem headers, corpo, query ou endereço do cliente. Eventos cloud
  contêm tipos/IDs/status; comandos de aprovação ficam apenas no journal local.
- Guard impõe Host/Origin, corpo, frequência e schemas. TLS termina no proxy autorizado.
  A aplicação não confia em headers encaminhados. Proteja também acesso e logs do proxy.
- SQLite não é criptografado. Proteja volumes, backup, host e journal com permissões e,
  se necessário, criptografia de disco. Não disponibilize dumps publicamente.
- Rate limit por principal é adequado para um proprietário, não uma defesa completa
  contra DDoS. O proxy deve limitar tempo/conexões e tráfego conforme seu ambiente.
- Uma task instruction é a tarefa delegada pelo proprietário. Arquivos, resultados e
  conteúdo de ferramentas são dados não confiáveis; nunca autoridades para ampliar
  allowlist, políticas, credentials ou configuração de produção.
- Dependências e imagens fixadas precisam de revisão/atualização deliberada de
  segurança; fixar versões não promete ausência de vulnerabilidades.
