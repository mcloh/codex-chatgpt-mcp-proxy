# Instalação e operação

## VM

Requer Docker Compose v2, rede interna do proxy existente, DNS e TLS. Não altere o proxy
ou publique a porta da aplicação diretamente. Inspecione recursos, espaço, versões,
rede, entrypoint e resolver antes de usar o exemplo Traefik do Compose.

```bash
cp .env.example .env
chmod 600 .env
# Edite .env fora do histórico do shell; gere segredos criptograficamente.
# BRIDGE_PYTHON_IMAGE: digest em deploy/base-images.json, ou um digest revisado.
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build --wait --wait-timeout 90
docker compose ps
docker compose logs --tail 100 bridge
```

Se precisar rotear um realm de Keycloak existente pela URL pública da ponte:

```bash
docker compose --env-file .env -f compose.yaml -f deploy/keycloak.compose.yaml up -d --build --wait --wait-timeout 90
```

Use os mesmos `--env-file`, `-f` e `-p` em operações posteriores. A implantação de
referência usa o project name codex-chatgpt-mcp-proxy. Não execute compose config sem
--quiet em logs públicos: a configuração expandida inclui segredos.

O container usa UID/GID 10001, root filesystem read-only, /tmp limitado, capacidades
removidas, no-new-privileges e volume bridge-data. Nenhum socket Docker é montado.
Apenas o proxy existente tem acesso ao container na sua rede interna.

DNS: crie apenas A/AAAA do novo hostname; preserve MX/TXT/CNAME e outros registros.
Capture o registro anterior para rollback. Confira zona autoritativa, resolução pública,
TLS e health. Não interprete resposta do painel como propagação global concluída.

## Ubuntu / worker

Instale o projeto em diretório do proprietário, crie venv e use `uv sync --locked`.
Confirme `codex --version`, `codex login status` e os schemas antes de outra versão.
Crie `demo/`, configure somente esse workspace e proteja o arquivo worker.env (0600).
Servidor não pode ampliar esse mapa. Não use configurações reais dentro do Git.

```bash
set -a
source /absolute/path/to/private/worker.env
set +a
.venv/bin/codex-bridge worker
.venv/bin/codex-bridge approvals
.venv/bin/codex-bridge decide APPROVAL_ID decline
# accept e cancel são decisões unitárias locais, sem aceite permanente.
```

Para serviço systemd de usuário, adapte deploy/codex-bridge-worker.service.example.
Se houver uma diretiva que exija configurações em outra pasta, use uma unidade naquela
pasta com `systemctl --user link /absolute/path/to/unit.service`, evitando cópias.

```bash
systemctl --user link /absolute/path/to/codex-bridge-worker.service
systemctl --user daemon-reload
systemctl --user enable --now codex-bridge-worker.service
systemctl --user status codex-bridge-worker.service
journalctl --user -u codex-bridge-worker.service -n 100 --no-pager
systemctl --user stop codex-bridge-worker.service
```

Não configure linger ou privilégios globais automaticamente. Sem linger/sessão de
usuário persistente o serviço pode parar no logout; confirme esse comportamento na sua
máquina. A instalação manual mantém o processo no terminal. Pare com Ctrl+C.

## Backup, restauração e retenção

Backup SQLite online pelo recurso backup (inclui WAL consistentemente):

```bash
.venv/bin/codex-bridge backup /absolute/path/to/backup.sqlite
.venv/bin/codex-bridge cleanup
# No container:
docker compose exec bridge python -m codex_chatgpt_mcp_proxy.cli backup /data/backup.sqlite
docker compose exec bridge python -m codex_chatgpt_mcp_proxy.cli cleanup
```

Agende cleanup com o seu cron/timer quando aprovado. Não há limpeza automática oculta.
BRIDGE_RETENTION_DAYS padrão 30 purga instruções, resultados e eventos de tarefas
terminais antigas. IDs, digests de idempotência, estado, lineage e histórico ficam como
tombstones; máximo de 10.000 tarefas deliberadamente impede crescimento irrestrito.
Para capacidade maior, revisar política/arquivamento em vez de descartar idempotência
silenciosamente. Históricos Codex e journal/approvals locais não são purgados por essa
retenção cloud; inclua-os na sua política local.

Restauração: pare worker e servidor, faça backup do volume atual, substitua o SQLite
pela cópia consistente, remova arquivos WAL/SHM **somente** do banco parado após backup
e preserve ownership 10001. Reinicie servidor e depois worker. Um backup antigo pode
reintroduzir incerteza sobre tarefas; inspecione e reconcilie, sem reexecução automática.
Não é suportado montar SQLite em filesystem compartilhado de rede ou vários servidores.

## Parar/remover

```bash
docker compose stop
# Remove containers/rede do projeto, preservando volume:
docker compose down
systemctl --user disable --now codex-bridge-worker.service
```

Não use `down -v` sem backup e intenção explícita de apagar fila/histórico. Remova apenas
o registro DNS da ponte e o realm/clientes criados para ela, depois de verificar que não
há outro consumidor. Preserve o proxy e provedores existentes.

## Troubleshooting

| Sintoma | Verificação |
|---|---|
| 401 MCP | OAuth ausente/expirado, JWKS, iss/aud; nunca usar token worker |
| 403 MCP | scope/owner incorretos ou Host/Origin inválidos |
| 401 worker | token worker e arquivo de ambiente |
| 409 | chave diferente, ownership ou thread concorrente; não repetir cegamente |
| Worker offline | serviço, HTTPS/443, token, heartbeat e DNS |
| needs_reconciliation | journal local e turno/arquivos; consulte ESTADOS.md |
| awaiting_local_approval | approvals/decide no computador do worker; timeout recusa |
| Falha App Server | login status, versão, políticas locais; não usar bypass |
| TLS/404 | DNS, certificado, network/labels/entrypoint do proxy |
| Falha após restart | volume persistente e journal; não apagar estado para destravar |
| Docker Permission denied no banco | ownership do volume; container UID 10001 |

Uma evolução para long polling/WebSocket deve conservar fencing, journal e a semântica
de reconciliação; muda transporte, não a garantia de efeitos. Não é necessária na v1.
