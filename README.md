# Portal de Insights

Aplicacao Streamlit com acesso por usuario, autorizacao por cliente e dashboards cadastrados no servidor.

## Configuracao local

1. Gere um hash para a senha do usuario da aplicacao:

   ```powershell
   python scripts/hash_password.py
   ```

2. Use `.streamlit/secrets.example.toml` como modelo para completar `.streamlit/secrets.toml`.
3. Execute:

   ```powershell
   streamlit run app.py
   ```

As chaves legadas `EVO_USER` e `EVO_PASS` continuam aceitas para o cliente `bemove`, mas devem permanecer apenas no arquivo de segredos. O painel utiliza `GET /api/v2/members?showMemberships=true`.

## Supabase

Configure a secao `[supabase]` em `.streamlit/secrets.toml` com a URL do projeto, a publishable key e a connection string Postgres. Depois instale as dependencias e aplique o schema:

```powershell
pip install -r requirements.txt
python scripts/setup_supabase.py
```

O script cria as tabelas `app_users`, `app_user_clients`, `evo_members` e `evo_sync_runs`, e copia os usuarios definidos em `auth.users` para o banco.

Se a connection string direta `db.<projeto>.supabase.co:5432` nao resolver DNS ou falhar em redes IPv4, use a string do `Connection pooler` do Supabase no campo `database_url`.

Quando o Supabase estiver configurado, o dashboard usa `Banco Supabase` como fonte principal. Use o botao `Sincronizar EVO` no menu lateral para buscar os clientes da EVO e salvar cada pagina no banco. Depois disso, o painel monta os indicadores a partir dos dados salvos, sem depender de uma carga completa da EVO a cada acesso.

As paginas da API sao consultadas com intervalo controlado para respeitar o limite oficial de 40 requisicoes por minuto por IP. Respostas `429` recebem uma nova tentativa apos a pausa recomendada pela EVO.

O carregamento direto pela API exibe progresso por pagina e e interrompido caso exceda o limite configurado. Com Supabase, a sincronizacao usa um limite maior por padrao e persiste os clientes no banco para reutilizacao.

O processo do Streamlit precisa ter acesso de saida HTTPS. Se o painel informar falta de permissao para acessar a internet, execute `streamlit run app.py` em um terminal comum e revise firewall, antivirus, VPN ou proxy do servidor.

## Protecao dos dados

- Credenciais da EVO nunca sao solicitadas ou exibidas no navegador.
- Cada usuario recebe somente os clientes cadastrados em `auth.users[].clients`.
- A resposta individual da EVO e transformada no servidor. O dashboard recebe apenas totais mensais e categorias agregadas.
- Quando Supabase esta ativo, os registros brutos da EVO ficam armazenados em `evo_members.payload` para permitir recalculo dos indicadores sem nova carga completa da API.
- CPF, nome, contatos, enderecos, fotos, URLs de contrato e identificadores individuais nao sao renderizados pelo painel.

Para publicacao externa, use HTTPS e uma camada de identidade corporativa ou proxy autenticado na frente do Streamlit.
