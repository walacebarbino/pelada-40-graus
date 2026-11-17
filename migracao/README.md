# Persistência e leitura da migração

## Arquivos

- `storage.py`: caminho absoluto do banco novo, conexões com chaves estrangeiras habilitadas e esquemas versionados.
- `init_db.py`: inicializador explícito; não executa ao importar e exige confirmação.
- `migrate_db.py`: migrações explícitas e confirmadas v1→v2 e v2→v3, cada uma com backup SQLite verificado.
- `queries.py`: consultas somente leitura com validação da versão e estrutura do esquema.
- `tests/`: testes `unittest` isolados em bancos SQLite temporários.

O banco futuro será `migracao/data/pelada_migracao.sqlite3`. **O banco real ainda não foi migrado para v3.** Importar os módulos não cria, inicializa nem migra o banco. O inicializador cria um banco v3 somente quando executado com `--confirm-create`; encontrar v1 ou v2 exige a migração explícita correspondente. Não reutiliza `pelada.db` e não insere partidas, reservas, pessoas ou votos de exemplo.

O código atual exige esquema v3 para as operações locais de consulta, administração e reserva; bancos v2 são recusados com erros controlados de incompatibilidade. O banco real ainda não foi migrado, e qualquer atualização dele depende de autorização separada.

## Esquema e decisões

O esquema versão 3 contém `matches`, `bookings`, `votes` e `reservation_operations`, com chaves estrangeiras, índices, estados limitados por `CHECK` e compatibilidade entre a partida de um voto e a partida da reserva votada. `matches.max_players` aceita exatamente 10, 12 ou 14. O número da reserva é independente do nome da pessoa. Valores são armazenados em `price_per_slot_cents` como inteiros; o preço padrão de 1500 centavos corresponde a R$ 15,00. A versão é registrada em `PRAGMA user_version`.

`reservation_operations` mantém uma linha por reserva criada pelo serviço idempotente. `booking_id` é chave primária e chave estrangeira; hashes BLOB de 32 bytes para idempotência e recuperação têm restrições `UNIQUE` e de tipo/tamanho. Um fingerprint HMAC de 32 bytes e o prazo original ficam associados à operação. Um trigger impede atualizar esses metadados. Reservas anteriores à v3 não recebem credenciais retroativas.

Estados de reserva: `disponivel`, `bloqueada`, `confirmada` e `expirada`. Uma confirmação exige pagamento `aprovado`; uma reserva bloqueada requer jogador e prazo. Estados de pagamento são separados: `nao_iniciado`, `cobranca_pendente`, `aprovado`, `recusado`, `cancelado` e `expirado`. Criar uma cobrança só pode registrar `cobranca_pendente`; `aprovado` deve depender de confirmação verificável do provedor. O serviço interno de bloqueio usa `nao_iniciado`, contabiliza vencimentos sem apagar registros e serializa a verificação de capacidade e inserção com `BEGIN IMMEDIATE`. Não há transição automática do pagamento nem integração com provedor.

Ao encontrar versão ou estrutura incompatível, a inicialização falha sem adaptar nem apagar o banco. Bancos v1 e v2 exigem migrações explícitas; o inicializador não os atualiza.

## Migração explícita v1 → v2

Antes de migrar o banco real, encerre somente o servidor de migração na porta 8001 com `Ctrl+C` no terminal em que ele está rodando. Não prossiga enquanto houver um processo usando o banco.

A partir de `Pelada sob Demanda/migracao/`, execute explicitamente:

```powershell
.\.venv\Scripts\python.exe .\migrate_db.py --confirm-v1-to-v2
```

O comando exige que a origem exista e corresponda exatamente ao esquema v1. Antes de alterar tabelas, cria um backup consistente usando a API de backup do SQLite, com nome exclusivo no mesmo diretório do banco: `pelada_migracao.sqlite3.v1-backup-<UTC>-<UUID>.sqlite3`. O backup é validado quanto à versão, integridade, relacionamentos e dados; se a criação ou validação falhar, a transformação não começa. O comando imprime o caminho exato do backup. Não substitui backups anteriores. Executado num banco v2 válido, apenas o valida e não recria tabela nem gera outro backup.

A transformação desativa `foreign_keys` fora da transação, inicia `BEGIN IMMEDIATE`, reconstrói somente `matches` com a restrição ampliada e copia todas as colunas explicitamente. `bookings` e `votes` não são excluídas nem recriadas; índices, IDs, `sqlite_sequence` e relacionamentos são verificados. `foreign_key_check`, `integrity_check`, dados e estrutura são conferidos antes de definir `user_version=2` e confirmar a transação. A configuração de chaves estrangeiras da conexão é restaurada ao terminar. Falhas durante a transformação fazem rollback; o banco de origem permanece v1 e o backup verificado é mantido.

Em caso de falha, guarde o caminho do backup impresso e não tente repetir enquanto houver dúvida sobre o estado do servidor ou do banco. A transação foi revertida e a origem deve continuar v1; valide isso antes de qualquer recuperação. Se uma recuperação a partir do backup for realmente necessária, pare o servidor, preserve o arquivo atual com um nome novo e exclusivo e só então copie o backup identificado para `data/pelada_migracao.sqlite3`. Nunca sobrescreva o único exemplar do banco ou do backup.

Exemplo de recuperação manual no PowerShell, somente depois de confirmar que o backup impresso é o arquivo correto e de parar o servidor:

```powershell
$database = Join-Path (Get-Location) 'data\pelada_migracao.sqlite3'
$backup = Read-Host 'Cole o caminho completo do backup verificado'
if (-not (Test-Path -LiteralPath $backup -PathType Leaf)) { throw 'Backup não encontrado.' }
$preserved = "$database.failed-$(Get-Date -Format 'yyyyMMddTHHmmss')"
Move-Item -LiteralPath $database -Destination $preserved
Copy-Item -LiteralPath $backup -Destination $database
```

O comando de restauração mantém o arquivo anterior no nome `.failed-<data>` e não move nem sobrescreve o backup. Não o use como resposta automática a uma falha: primeiro verifique a mensagem, o estado da origem e o backup.

## Migração explícita v2 → v3

Para chegar de v1 a v3, execute primeiro a migração v1→v2 acima e depois esta migração, cada uma com sua confirmação e backup verificado. A migração v2→v3 aceita somente um arquivo existente com versão e esquema v2 exatos; versão ou estrutura incompatível é rejeitada antes de qualquer alteração. Ela verifica `integrity_check` e `foreign_key_check`, cria um backup exclusivo `*.v2-backup-<UTC>-<UUID>.sqlite3` e valida versão, esquema, integridade, relacionamentos e dados desse backup antes de prosseguir.

Com confirmação explícita, a migração usa `BEGIN IMMEDIATE`, cria somente `reservation_operations` e seu trigger de imutabilidade, confere que partidas, reservas, votos, IDs e sequências foram preservados, valida novamente esquema e integridade, define `user_version=3` e confirma tudo em uma transação. Se a alteração falhar, há rollback e o backup verificado é mantido. Backups existentes nunca são sobrescritos. Um banco já v3 válido é apenas validado, sem novo backup.

Com o ambiente virtual ativado por caminho explícito, o comando é:

```powershell
.\.venv\Scripts\python.exe .\migrate_db.py --confirm-v2-to-v3
```

Esse comando ainda **não foi executado contra o banco real**. A migração real continua pendente de autorização específica.

## API v2

`GET /api/v2/partidas` retorna todas as partidas ordenadas por data, horário e ID, em uma lista JSON. Cada objeto contém `id` (inteiro), `title` (texto), `match_date` (`YYYY-MM-DD`), `match_time` (`HH:MM`), `location` (texto), `duration_minutes`, `max_players`, `price_per_slot_cents`, `vagas_ocupadas` e `vagas_livres` (inteiros), e `status` (texto). `vagas_livres` não fica abaixo de zero. O preço permanece em centavos; o JavaScript divide por 100 somente para apresentá-lo como reais.

`GET /api/v2/partidas/{partida_id}/participantes` retorna `{"partida_id": <inteiro>, "participantes": [<nomes>]}`. Uma partida existente sem confirmados retorna uma lista vazia. Uma partida inexistente retorna 404; banco ausente, incompatível ou erro de leitura retorna 503 genérico, sem caminho interno.

Disponibilidade conta todas as reservas `confirmada` e as `bloqueada` cujo `locked_until` original seja posterior ao instante atual. Para operações v3, a data original imutável dos metadados é a fonte de verdade; reservas antigas sem metadados usam `bookings.locked_until`. O valor deve ser ISO 8601 UTC terminado em `Z`, com segundos e fração opcional de até seis dígitos. Valores inválidos resultam em erro controlado. Estados `disponivel` e `expirada` não ocupam vaga; a quantidade livre é limitada a zero se o total de ocupadas ultrapassar o limite. A consulta continua somente leitura; o serviço interno usa a mesma regra dentro da transação de criação para evitar exceder a capacidade.

O esquema não tem status de reserva `cancelada`. O suporte a cancelamento de reservas foi adiado; não se interpreta `payment_status='cancelado'` como cancelamento da reserva. Portanto, uma reserva ainda `bloqueada` ocupa vaga enquanto seu prazo for válido, independentemente do status de pagamento.

## Serviço interno idempotente e recuperação

`create_temporary_reservation` é o único caminho de criação de bloqueios. Ele requer uma chave de idempotência e um segredo de recuperação distintos. `generate_reservation_credentials()` gera cada um com `secrets.token_urlsafe(32)`: 32 bytes aleatórios (256 bits), codificados como 43 caracteres Base64 URL-safe sem padding. O chamador precisa guardar ambos com segurança antes de enviar a solicitação e reutilizá-los em uma repetição; não os coloque em URL, logs ou dados de telemetria.

O serviço normaliza o nome removendo espaços externos e aplicando Unicode NFC; normaliza o telefone para dígitos nacionais, removendo um prefixo `55` reconhecido. Um JSON canônico com ID da partida e esses dois valores alimenta um HMAC-SHA-256, usando a chave de idempotência decodificada como chave HMAC. O banco persiste somente esse fingerprint e hashes SHA-256 com separação de domínio das duas credenciais; não duplica nome ou telefone na tabela de metadados. O fingerprint permite comparar retries sem depender dos campos mutáveis de `bookings`; se a chave de idempotência for comprometida, ele também permite testar hipóteses sobre os dados originais.

O banco garante unicidade dos hashes e uma associação um-a-um entre metadados e reserva. Um trigger impede atualizar fingerprint, hashes ou prazo original. A criação do bloqueio e dos metadados ocorre na mesma transação `BEGIN IMMEDIATE`. Uma repetição válida devolve a mesma reserva e o prazo originalmente registrado; chave reutilizada com dados ou segredo diferentes gera conflito genérico. Após o prazo, o resultado informa estado efetivo `expirada`, sem renovar o prazo, alterar reserva/pagamento ou ocupar vaga. Reservas anteriores a v3 não têm metadados e não podem ser recuperadas por este serviço.

`recover_temporary_reservation` autoriza a consulta exclusivamente pelo segredo de recuperação. Retorna somente ID e ID da partida, estado efetivo, prazo original e preço; não retorna nome, telefone nem credenciais. O ID sequencial não autoriza acesso. A comparação das credenciais usa `hmac.compare_digest`.

Este é um serviço interno: as rotas HTTP abaixo e o envio pela interface estão desabilitados por padrão; esta entrega não autoriza exposição pública. Há somente limitação provisória em memória, insuficiente contra abuso distribuído. A interface integrada guarda credenciais apenas em `sessionStorage` durante a sessão e não implementa pagamentos. Idempotência evita duplicar uma mesma operação, mas não impede alguém de criar muitas operações com chaves distintas.

## Contrato HTTP de reserva (desabilitado por padrão)

`main.py` registra as rotas somente quando `PELADA_RESERVATION_HTTP_ENABLED=1` está presente no ambiente do processo. O valor não é definido por este projeto em `.env` nem na configuração real da máquina. Sem a configuração explícita, as rotas não fazem parte da aplicação. Habilitá-las localmente não autoriza exposição pública; a implantação permanece bloqueada até revisão dos controles de borda e da configuração operacional.

As rotas chamam o serviço interno existente em uma thread de trabalho, injetando caminho do banco e relógio. Não abrem banco em import ou startup e não executam migrações.

### Criar ou repetir

`POST /api/v2/reservas` exige `Content-Type: application/json`, `Idempotency-Key: <chave>` e `Authorization: Bearer <segredo-de-recuperacao>`. O corpo aceita exatamente:

```json
{"match_id": 123, "player_name": "Nome", "player_phone": "11999999999"}
```

Uma criação e um replay válido retornam HTTP 200 com somente `status`, `locked_until` e `price_per_slot_cents`. Não retornam IDs, nome, telefone ou credenciais. O prazo é sempre o original; um retry não o renova. Se a chave válida já estiver associada a uma operação, o serviço a procura antes de aplicar as condições exclusivas de uma nova reserva. Dados ou segredo diferentes resultam em conflito genérico, inclusive quando o payload não é semanticamente válido.

O cliente gera e guarda as duas credenciais independentes e o corpo normalizado antes do primeiro envio. Em timeout ou resposta incerta, permite repetir o mesmo corpo com as mesmas credenciais, sem retry automático nem geração de outra operação. A rota não aceita credenciais em URL.

A quota é verificada primeiro; em seguida, a chave de idempotência precisa ter formato válido. Para uma chave válida, erros de tipo de conteúdo, JSON, estrutura, campos ou segredo são encaminhados ao serviço, que procura a operação antes da validação de uma nova reserva. Assim, uma chave já usada recebe 409 genérico mesmo com esses outros dados inválidos. Para uma chave ainda não usada, tipo de conteúdo incorreto recebe 415 e dados inválidos recebem 422.

### Recuperar

`GET /api/v2/reservas/recuperacao` recebe exclusivamente `Authorization: Bearer <segredo-de-recuperacao>`. Responde HTTP 200 com os mesmos três campos mínimos do POST. Segredo ausente, inválido, incorreto ou não associado produz a mesma resposta genérica HTTP 404. A chave de idempotência e o ID sequencial não autorizam recuperação.

### Validação e erros públicos

O corpo tem limite de 4096 bytes, mesmo sem `Content-Length` ou se esse cabeçalho estiver incorreto. O limite de cabeçalhos é **agregado**: a soma dos nomes e valores não pode ultrapassar 8192 bytes. Além desse teto, `Idempotency-Key` aceita no máximo 43 caracteres e `Authorization` no máximo 50. O serviço valida `match_id`, nome (até 120 caracteres) e telefone (até 24 caracteres). Campos extras e chaves JSON duplicadas são rejeitados.

Respostas das rotas, inclusive erros, usam `Cache-Control: no-store`; entradas recusadas no limite de cabeçalhos retornam 431. Os códigos públicos são estáveis e não incluem mensagens SQLite, valores recebidos ou detalhes internos:

| HTTP | `error.code` | Situação |
| --- | --- | --- |
| 200 | — | Reserva criada, repetida ou recuperada |
| 404 | `match_not_found` / `reservation_not_found` | Partida inexistente / recuperação não autorizada |
| 409 | `idempotency_conflict` / `no_vacancy` / `match_unavailable` | Conflito, lotação ou partida indisponível |
| 415 | `invalid_request` | Tipo de conteúdo diferente de JSON |
| 422 | `invalid_request` | Campos, credenciais ou JSON inválidos |
| 429 | `rate_limited` | Quota temporária excedida; inclui `Retry-After` |
| 431 | `headers_too_large` | Cabeçalhos excedem o limite |
| 503 | `service_unavailable` | Banco ocupado, indisponível ou esquema incompatível |
| 500 | `internal_error` | Falha inesperada, sem detalhes internos |

O banco ocupado retorna `Retry-After: 1`; esquema v2 ou incompatível retorna 503 sem sugerir retry, pois uma repetição não migra o banco. Nenhum corpo, cabeçalho de autorização, chave, nome ou telefone é escrito em logs; falhas inesperadas registram somente o tipo da exceção.

### Origem, CORS e abuso

Por padrão não há origens de navegador autorizadas. Uma implantação que precise de navegador deve configurar `PELADA_RESERVATION_ALLOWED_ORIGINS` com origens exatas separadas por vírgula; curingas e a origem `null` não são aceitos. Um pedido sem `Origin` é aceito para clientes não navegador; a aplicação não consegue provar o tipo do cliente pela ausência desse cabeçalho, que também não autentica a requisição. Origin não autorizado ou `null` é recusado. O preflight só permite métodos e cabeçalhos definidos para estas rotas; não são habilitadas credenciais CORS amplas.

O limitador token-bucket é **por processo e por endereço de cliente observado pelo ASGI**, com contadores separados para criação e recuperação. Os padrões provisórios são 5 criações/minuto e 10 recuperações/minuto. Não há quota global nem quota por partida. Os limites podem ser configurados por `PELADA_RESERVATION_CREATE_LIMIT`, `PELADA_RESERVATION_RECOVERY_LIMIT`, `PELADA_RESERVATION_RATE_WINDOW_SECONDS` e `PELADA_RESERVATION_LIMITER_MAX_ENTRIES`; o último define a capacidade máxima de entradas.

Cada tentativa que chega ao handler de criação consome quota, incluindo replay e tentativa inválida. A quota também é consumida por tentativas de recuperação, inclusive quando a credencial não autoriza. Requisições rejeitadas pelo middleware antes do handler — por exemplo, Origin recusado, limite de cabeçalhos ou corpo excedido — não consomem quota. Um retry legítimo também pode receber 429; após `Retry-After`, repetir com os mesmos cabeçalhos e payload é seguro: a operação original não é recriada nem tem o prazo renovado.

Entradas inativas expiram após a janela correspondente, com limpeza preguiçosa no próximo consumo. O mapa é protegido por lock de thread e limitado a 10.000 entradas por padrão. Quando atinge a capacidade, o limitador expulsa a entrada menos recentemente usada e aceita a nova chave; isso pode permitir evasão das quotas anteriores.

Esse limitador não é distribuído: workers têm contadores independentes e reinícios os apagam. Usuários atrás de NAT podem compartilhar a quota; atrás de proxy, o endereço observado pode representar todos os clientes. O código não confia em `X-Forwarded-For`. Idempotência, o limitador processual e controles do cliente não bastam para prevenir abuso com chaves diferentes. **Não é proteção para exposição pública**: ela continua bloqueada até revisão dos limites de borda, configuração de proxies confiáveis e implantação.

### Integração da interface

A interface estática usa as rotas relativas da mesma origem. Antes de habilitar o formulário, consulta `GET /api/ui-capabilities`, que retorna somente `reservation_http_enabled` (`true` ou `false`) com `Cache-Control: no-store`; não expõe valores de ambiente nem credenciais. O sinal e o registro das rotas usam a mesma configuração do servidor. Sem `PELADA_RESERVATION_HTTP_ENABLED=1`, o sinal é falso e a interface não chama as rotas de reserva.

O navegador gera as credenciais com Web Crypto e guarda o registro versionado em `sessionStorage` antes do POST. Se Web Crypto ou o armazenamento de sessão não estiver disponível, o envio fica desabilitado. Em resposta incerta, a repetição é manual e reutiliza exatamente os mesmos cabeçalhos e payload. Após sucesso, nome, telefone e chave de idempotência são removidos; o segredo de recuperação permanece oculto em `sessionStorage` até o fim da sessão. Por decisão de escopo, não há exibição nem cópia do segredo; portanto, não há garantia de recuperação depois que a sessão da aba termina. `sessionStorage` não é um cofre contra scripts comprometidos da mesma origem.

O navegador usa Web Locks para impedir requisições simultâneas entre abas quando a API está disponível. Sem ela, o bloqueio de duplicidade vale apenas dentro da aba atual; segredos nunca são compartilhados entre abas. Timeout de 10 segundos não dispara retry automático. `429` e `503` permitem retry manual após `Retry-After`; `503` sem esse cabeçalho não oferece retry da criação.

A aplicação não habilita CORS no frontend. Configure no servidor a origem exata usada pelo navegador em `PELADA_RESERVATION_ALLOWED_ORIGINS`; origem ausente é aceita pelo middleware, mas não autentica, e `null` ou origem não autorizada são recusadas. O formulário e a recuperação não colocam credenciais em URL, não as imprimem no console e não mostram respostas internas.

Não há harness de testes JavaScript/browser no repositório. A suíte Python testa o sinal ASGI, mas não executa o código do navegador; testes de UI (fluxos, recarga, erros, Web Locks e acessibilidade) continuam pendentes, sem instalar framework nesta entrega.

## Cadastro administrativo local

`POST /api/v2/admin/partidas` recebe `title`, `match_date` (`YYYY-MM-DD`), `match_time` (`HH:MM`), `location`, `duration_minutes`, `max_players` (10, 12 ou 14) e `price_per_slot_cents` como inteiro. A resposta 201 contém a partida criada, incluindo status `agendada` e disponibilidade inicial; não contém informações pessoais. Tipos inteiros são estritos, campos extras são rejeitados, e o status é definido pelo servidor. Banco ausente ou incompatível retorna 503 genérico; a escrita usa transação e não cria nem inicializa o banco.

O endpoint exige `X-Admin-Key`, comparado no servidor com `PELADA_ADMIN_KEY`. Sem a variável configurada, responde 503; chave ausente ou incorreta responde 401. Configure a variável apenas na sessão PowerShell que executará o servidor:

```powershell
$secureKey = Read-Host -Prompt "Chave administrativa local temporária" -AsSecureString
$env:PELADA_ADMIN_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
Remove-Variable secureKey
```

`PUT /api/v2/admin/partidas/{partida_id}` usa o mesmo cabeçalho e o mesmo corpo do POST, atualizando apenas título, data, horário, local, duração, limite de jogadores e preço. O ID e o status não são editáveis. Retorna 200 com a partida atualizada, 404 se ela não existir, 409 se não estiver `agendada` ou tiver qualquer reserva registrada, 401 para chave ausente/incorreta e 503 se a chave não estiver configurada ou o banco estiver indisponível. A verificação de status e reservas e o UPDATE ocorrem na mesma transação `BEGIN IMMEDIATE`; a partida não é excluída ou recriada e as reservas nunca são alteradas.

`DELETE /api/v2/admin/partidas/{partida_id}` exige a mesma chave e a valida em cada requisição. Retorna 204 sem corpo quando exclui, 404 para partida inexistente, 409 se a partida não estiver `agendada` ou tiver qualquer reserva ou voto vinculado, 401 para chave ausente/incorreta e 503 se a chave não estiver configurada ou o banco estiver indisponível. A existência, status e relacionamentos são verificados sob `BEGIN IMMEDIATE`; a exclusão usa ID parametrizado e não remove nem recria reservas ou votos. A interface exige confirmação explícita, informa que a operação é definitiva e atualiza ambas as listagens após o sucesso.

Essa chave não deve ser colocada em `.env`, no código, no histórico de comandos ou em configurações persistentes. A proteção é provisória para desenvolvimento local e não é autenticação definitiva para produção. O painel mantém a chave somente em memória enquanto aberto e a envia apenas nos cabeçalhos das operações administrativas POST, PUT e DELETE. Uma falha de rede em exclusão pode deixar o resultado incerto; a interface exige atualizar a listagem antes de permitir uma nova tentativa.

## Ambiente Python para migração e testes

A suíte completa foi executada com CPython 3.15.2 no Windows: 107 testes passaram, sem testes ignorados. Os testes HTTP usam `httpx.AsyncClient` com `ASGITransport`, sem servidor real; como o transporte não executa lifespan automaticamente, as fixtures criam o esquema temporário antes das requisições. Todos os testes usam SQLite temporário; nenhum banco real é aberto ou migrado.

As dependências de runtime estão em `requirements.txt`; `requirements-test.txt` inclui esse arquivo e fixa `httpx==0.28.1` para os testes ASGI. `tzdata` fornece a base IANA necessária para `America/Sao_Paulo` no Windows. `uvicorn`, Streamlit e Mercado Pago não fazem parte deste ambiente de testes; a suíte não inicia servidor nem usa integração de pagamento.

A partir de `Pelada sob Demanda/migracao/`, prepare o ambiente isolado e execute os testes:

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

## Comandos

No PowerShell, a partir de `Pelada sob Demanda/migracao/`:

```powershell
.\.venv\Scripts\python.exe .\init_db.py --confirm-create
.\.venv\Scripts\python.exe .\migrate_db.py --confirm-v1-to-v2
.\.venv\Scripts\python.exe .\migrate_db.py --confirm-v2-to-v3
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

## Ainda não implementado

As rotas proxy antigas continuam disponíveis apenas para comparação; a interface usa exclusivamente a API v2, sem fallback. Cadastro e edição de partidas são as únicas escritas administrativas expostas. As rotas HTTP de reserva e o envio pela interface estão implementados, mas desabilitados por padrão; não há integração com pagamento. Ainda não existem exclusão de partidas, votos, suporte a cancelamento de reservas, autenticação definitiva, cobrança ou confirmação de pagamento.

### Pendências antes de qualquer exposição pública

Antes de qualquer exposição pública, revisar e implementar:

- controles de borda distribuídos, limites por implantação e confiança explícita em proxies;
- testes de navegador para criação, repetição incerta, recuperação, expiração, erros, privacidade e acessibilidade;
- revisão de segurança das rotas e da política de expiração antes de conectar a interface.

Esta entrega não migrou nem abriu o banco real e não autoriza exposição pública do bloqueio.
