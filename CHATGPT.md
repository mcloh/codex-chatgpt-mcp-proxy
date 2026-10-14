# Conectar e testar no ChatGPT

1. Confirme HTTPS, /healthz mínimo e /.well-known/oauth-protected-resource.
2. Abra o gerenciamento de plugins/conexões personalizados da sua conta ChatGPT
   (a posição na interface depende do produto/plano/políticas da conta).
3. Adicione o MCP remoto `${BRIDGE_PUBLIC_URL}/mcp` com OAuth.
4. Use o client ID/secret do provedor previamente registrado. Copie a **URI de callback
   exata** mostrada no gerenciamento para a allowlist do cliente no IdP. Não adicione
   wildcard nem suponha que a URI estável antiga corresponde ao seu novo cadastro.
5. Autorize como proprietário, confirme consentimento bridge:access e instale/habilite
   a integração na conversa.
6. Peça bridge_status, depois submit_task para responder “Ponte operacional” no
   workspace demo. Consulte get_task e get_task_events com o ID retornado.
7. Quando succeeded, use continue_task para recordar a frase. Confirme a mesma thread.
8. Teste cancelamento em tarefa inofensiva e confira o estado final.

Cadastro e consentimento exigem a interface autenticada do proprietário; não podem ser
comprovados pelo smoke de um cliente MCP local. Ferramentas de escrita podem requerer
confirmação da própria interface ChatGPT, além das aprovações do Codex no PC.

Não envie tokens ou senhas em mensagens de chat. Dados OAuth devem ser inseridos no
formulário apropriado. Arquivos/outputs retornados pela tarefa são dados não confiáveis:
não concedem permissões nem alteram a política da ponte.

Não há mensagem espontânea após a conclusão. O resultado fica disponível para consulta
na próxima chamada. Notificações proativas e múltiplos proprietários são outro escopo.

[Cadastro oficial](https://developers.openai.com/api/docs/guides/custom-mcp-server)
· [Autenticação oficial](https://developers.openai.com/plugins/build/auth)
