# Contrato API worker — versão 1

Base `${BRIDGE_SERVER_URL}/api/worker`. Todas as chamadas exigem
`Authorization: Bearer <BRIDGE_WORKER_TOKEN>`. Não use o token OAuth aqui.
Um único worker é configurado no servidor; tentativa e tarefa identificam a propriedade
por reserva. HTTP 400: payload inválido; 401/403: autenticação; 404: desconhecida;
409: conflito/ownership; 413: corpo muito grande; 422: schema; 429: frequência.

| Método / rota relativa | JSON de entrada | Saída |
|---|---|---|
| POST /register | worker_id, protocol="1", version, workspaces=[IDs] | protocol, lease_seconds |
| POST /heartbeat | {} | ok |
| POST /claim | {} | task (inclui attempt) ou null |
| GET /tasks/{id}?attempt=... | — | tarefa pertencente à tentativa |
| POST /tasks/{id}/renew | attempt | state atual |
| POST /tasks/{id}/progress | attempt, state, thread_id?, turn_id? | ok |
| POST /tasks/{id}/events | attempt, event_id, payload={} | ok; dropped quando limite atingido |
| POST /tasks/{id}/finish | attempt, state, result?, error? | ok |
| POST /tasks/{id}/reconcile | attempt, state terminal, confirm_no_active_turn=true | ok |

O último endpoint é uma decisão administrativa **local**, depois de inspecionar a
execução. Não há ferramenta MCP para essa operação.

Eventos usam IDs aleatórios duráveis no outbox. Repetir ID com payload diferente é
conflito. Finalização é idempotente para o mesmo resultado; um resultado conflitante
não pode substituir o anterior. A sequência dos eventos é um cursor crescente global;
consultas filtram a tarefa antes de retornar a página. Listagens são crescentes por
criação; next_cursor deve ser enviado como after. Páginas vazias conservam o cursor.

Limites atuais: instrução 32.000 caracteres, timeout 1..3.600 segundos, corpo 128 KiB,
evento 16 KiB, até 2.000 eventos por tarefa, resposta 64.000 caracteres, erro 4.000,
página de 1..100 itens, 240 requests/minuto por principal autenticado, 10.000 tarefas.
Heartbeat/renew a cada ~1 segundo durante execução; polling padrão 2 segundos com
jitter, backoff até 60 segundos. Intervalo configurável por BRIDGE_POLL_SECONDS.
Status e IDs têm limites adicionais no schema. Reservas de 45 segundos.

Health /healthz é público e só retorna ok. Detalhes operacionais estão em bridge_status
ou na API autenticada. Metadata OAuth pública não contém tokens, usuário ou banco.
