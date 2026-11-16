# Persistência e leitura da migração

## Arquivos

- `storage.py`: caminho absoluto do banco novo, conexões com chaves estrangeiras habilitadas e esquemas versionados.
- `init_db.py`: inicializador explícito; não executa ao importar e exige confirmação.
- `migrate_db.py`: migração explícita e confirmada de v1 para v2, com backup SQLite verificado.
- `queries.py`: consultas somente leitura com validação da versão e estrutura do esquema.
- `tests/`: testes `unittest` isolados em bancos SQLite temporários.

O banco futuro será `migracao/data/pelada_migracao.sqlite3`. Importar os módulos não cria nem inicializa o banco. O inicializador cria um banco v2 somente quando executado com `--confirm-create`; encontrar v1 exige a migração explícita. Não reutiliza `pelada.db` e não insere partidas, reservas, pessoas ou votos de exemplo.

## Esquema e decisões

O esquema versão 2 contém `matches`, `bookings` e `votes`, com chaves estrangeiras, índices, estados limitados por `CHECK` e compatibilidade entre a partida de um voto e a partida da reserva votada. `matches.max_players` aceita exatamente 10, 12 ou 14. O número da reserva é independente do nome da pessoa. Valores são armazenados em `price_per_slot_cents` como inteiros; o preço padrão de 1500 centavos corresponde a R$ 15,00. A versão é registrada em `PRAGMA user_version`.

Estados de reserva: `disponivel`, `bloqueada`, `confirmada` e `expirada`. Uma confirmação exige pagamento `aprovado`; uma reserva bloqueada requer jogador e prazo. Estados de pagamento são separados: `nao_iniciado`, `cobranca_pendente`, `aprovado`, `recusado`, `cancelado` e `expirado`. Criar uma cobrança só pode registrar `cobranca_pendente`; `aprovado` deve depender de confirmação verificável do provedor. Transições futuras previstas: reserva disponível → bloqueada → confirmada após aprovação; bloqueada → expirada → disponível após liberação controlada. Pagamento não iniciado → cobrança pendente → resultado verificado. O serviço interno de bloqueio usa `nao_iniciado`, contabiliza vencimentos sem apagar registros e serializa a verificação de capacidade e inserção com `BEGIN IMMEDIATE`. Não há transição automática do status nem integração com provedor.

Ao encontrar versão ou estrutura incompatível, a inicialização falha sem adaptar nem apagar o banco. A repetição em um esquema v2 compatível valida a estrutura e preserva os dados. Um banco v1 não é migrado pelo inicializador.

## Migração explícita v1 → v2

Antes de migrar o banco real, encerre somente o servidor de migração na porta 8001 com `Ctrl+C` no terminal em que ele está rodando. Não prossiga enquanto houver um processo usando o banco.

A partir de `Pelada sob Demanda/migracao/`, execute explicitamente:

```powershell
py .\migrate_db.py --confirm-v1-to-v2
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

## API v2

`GET /api/v2/partidas` retorna todas as partidas ordenadas por data, horário e ID, em uma lista JSON. Cada objeto contém `id` (inteiro), `title` (texto), `match_date` (`YYYY-MM-DD`), `match_time` (`HH:MM`), `location` (texto), `duration_minutes`, `max_players`, `price_per_slot_cents`, `vagas_ocupadas` e `vagas_livres` (inteiros), e `status` (texto). `vagas_livres` não fica abaixo de zero. O preço permanece em centavos; o JavaScript divide por 100 somente para apresentá-lo como reais.

`GET /api/v2/partidas/{partida_id}/participantes` retorna `{"partida_id": <inteiro>, "participantes": [<nomes>]}`. Uma partida existente sem confirmados retorna uma lista vazia. Uma partida inexistente retorna 404; banco ausente, incompatível ou erro de leitura retorna 503 genérico, sem caminho interno.

Disponibilidade conta todas as reservas `confirmada` e as `bloqueada` cujo `locked_until` seja posterior ao instante atual. `locked_until` deve ser ISO 8601 UTC terminado em `Z`, com segundos e fração opcional de até seis dígitos. Valores inválidos resultam em erro controlado. Estados `disponivel` e `expirada` não ocupam vaga; a quantidade livre é limitada a zero se o total de ocupadas ultrapassar o limite. A consulta continua somente leitura; o serviço interno usa a mesma regra dentro da transação de criação para evitar exceder a capacidade.

O esquema não tem status de reserva `cancelada`. O suporte a cancelamento de reservas foi adiado; não se interpreta `payment_status='cancelado'` como cancelamento da reserva. Portanto, uma reserva ainda `bloqueada` ocupa vaga enquanto seu prazo for válido, independentemente do status de pagamento.

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

A suíte foi executada com CPython 3.15.2 no Windows. Nenhuma outra versão do Python foi testada ou declarada compatível.

As dependências externas diretas, com as versões usadas, estão em `requirements.txt`. `tzdata` fornece a base IANA necessária para `America/Sao_Paulo` no Windows. `uvicorn`, Streamlit e Mercado Pago não fazem parte deste ambiente de testes; a suíte não inicia servidor nem usa integração de pagamento.

A partir de `Pelada sob Demanda/migracao/`, prepare o ambiente isolado e execute os testes:

```powershell
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

## Comandos

No PowerShell, a partir de `Pelada sob Demanda/migracao/`:

```powershell
py .\init_db.py --confirm-create
py .\migrate_db.py --confirm-v1-to-v2
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

## Ainda não implementado

As rotas proxy antigas continuam disponíveis apenas para comparação; a interface usa exclusivamente a API v2, sem fallback. Cadastro e edição de partidas são as únicas escritas administrativas expostas. Há um serviço interno de bloqueio temporário, mas ainda não existe rota de criação de reserva nem integração com a interface. Ainda não existem exclusão de partidas, votos, suporte a cancelamento de reservas, autenticação definitiva, cobrança ou confirmação de pagamento.

### Pendências antes da exposição pública do bloqueio

O serviço interno de bloqueio temporário ainda não está exposto por rota nem conectado à interface. Antes de qualquer exposição pública, implementar e revisar:

- idempotência e prevenção de bloqueios duplicados;
- recuperação segura de uma reserva sem revelar dados pessoais;
- autorização para acessar os dados de uma reserva;
- proteção contra abuso de solicitações de bloqueio.
