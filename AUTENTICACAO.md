# Autenticação

## ChatGPT / MCP

Produção usa um provedor OAuth estabelecido. A aplicação é somente resource server:
valida assinatura RS256, kid/JWKS, iss, aud, exp, iat, sub do proprietário e o escopo
bridge:access. O JWKS é obtido de uma URL HTTPS configurada pelo operador, sem seguir
redirects, e fica em cache por até 5 minutos. Uma rotação de kid pode levar até esse
intervalo para ser reconhecida. Tokens com audiência errada ou outro proprietário são
recusados. Não use token do worker no cadastro MCP do ChatGPT.

Configuração: BRIDGE_OAUTH_ISSUER, BRIDGE_OAUTH_JWKS_URL, BRIDGE_OAUTH_AUDIENCE,
BRIDGE_OWNER_SUBJECT. A metadata em /.well-known/oauth-protected-resource indica
issuer, resource/audience e scopes. O 401 contém WWW-Authenticate com resource_metadata.
Ferramentas declaram securitySchemes OAuth e annotations de leitura/escrita.

O cliente ChatGPT usa autorização por código e PKCE. Configure o cliente OAuth
previamente registrado, ou CIMD/DCR se seu provedor suportar e você validar esse fluxo.
Não implemente uma nova emissão de tokens na ponte. Não pressuponha que o cadastro
aceita uma chave Bearer arbitrária.

## Exemplo Keycloak existente

Crie um realm isolado, cadastro público desabilitado, proteção de brute force e somente
a conta proprietária. Crie um cliente confidencial com standard flow e PKCE S256,
implicit/direct grant/service accounts desabilitados. Cadastre a **URI exata** apresentada
na tela de gerenciamento da integração ChatGPT, sem wildcard. Guarde o client secret
fora do Git, e informe-o somente no formulário OAuth do ChatGPT.

No Keycloak 25+, conserve o scope padrão `basic`: ele adiciona o subject `sub` ao access
token, exigido pela ponte. Conserve profile/email conforme a política local.

Crie client scope opcional bridge:access, incluindo-o em scope no access token. Adicione
Audience mapper com Included Custom Audience igual a BRIDGE_OAUTH_AUDIENCE; associe ao
cliente como scope opcional. Esse binding por scope e audiência é documentado pelo
Keycloak quando resource-indicators não está habilitado. Para esta ponte de um único
recurso, a audiência é fixa e verificada pelo resource server. Não se afirma suporte
genérico ao RFC8707/seleção dinâmica de resource por essa configuração.

Discovery: `${ISSUER}/.well-known/openid-configuration`. JWKS:
`${ISSUER}/protocol/openid-connect/certs`. Verifique URLs públicas, PKCE S256, resource
metadata e a audiência do token efetivamente emitido. Um HTTP 200 de discovery não
comprova a autorização completa.

Se a rota do Keycloak existente expuser somente outro realm, o override
`deploy/keycloak.compose.yaml` adiciona uma rota isolada, sem reiniciar Keycloak ou
alterar Traefik. Defina BRIDGE_KEYCLOAK_UPSTREAM e BRIDGE_NGINX_IMAGE (digest).
O realm deve emitir issuer correspondente à URL pública, por configuração frontendUrl
verificada no seu Keycloak. Nginx não expõe /admin e não registra tráfego de login.

## Worker

Token aleatório independente de pelo menos 32 caracteres. Use geração criptográfica,
arquivo protegido, transmissão HTTPS e BRIDGE_WORKER_ID idêntico no servidor/worker.
A ponte nunca lê/copia credenciais do perfil Codex. O processo local usa o login salvo.
Os tokens BRIDGE_* são removidos do ambiente herdado pelo App Server.

## Desenvolvimento

BRIDGE_MODE=development exige origin HTTP de loopback, bind de loopback e peer de
loopback. Credenciais MCP e worker são diferentes, geradas em private/local.env.
Esse modo não é um provedor OAuth nem serve para cadastrar uma integração pública.
Não use proxy/túnel para contornar a limitação de desenvolvimento.

## Rotação e revogação

Troque token worker em ambos os arquivos e reinicie os dois componentes. Faça isso
quando não houver tarefa ativa. Para revogar o proprietário, desabilite sua conta no
IdP; access tokens JWT já emitidos podem permanecer válidos até expiração (5 minutos
na configuração de exemplo). O servidor não implementa introspecção/revogação instantânea.
Client secret OAuth fica no IdP/ChatGPT, não no servidor da ponte. Revogue/reset o secret
no provedor e atualize o cadastro do ChatGPT quando necessário.
