"""Base da Receita para Captar → Empresas (Receita). Roda todo mês (GitHub Action do repo público).

Entrada: pasta com os CSV dos dados abertos do CNPJ (Estabelecimentos, Empresas, Socios, Simples, Municipios).
Saída, por UF: `<UF>.bin` (blocos gzip independentes, um por município + divisão do CNAE) e `<UF>.json`
(índice: município → divisão → [início, tamanho, linhas]). A extensão baixa o índice e pede só os blocos
do raio e do ramo (HTTP Range). Só empresas ativas (situação 02) com telefone ou e-mail.

Também grava `tabelas.json` (CNAE, natureza e qualificação com nome). O nome do município no índice é o de
`extension/public/cidades.json` quando casa (nome sem acento + UF), para o raio da extensão achar o bloco.

Uso: python etl.py <pasta-csv> <pasta-saida> <AAAA-MM> [--utf8] [--uf BA] [--cidades caminho/cidades.json] [--db base.duckdb]
"""
import gzip
import json
import re
import sys
import unicodedata
from pathlib import Path

import duckdb

args = sys.argv[1:]
ent, sai, ref = Path(args[0]), Path(args[1]), args[2]
enc = 'utf-8' if '--utf8' in args else 'latin-1'
so_uf = args[args.index('--uf') + 1] if '--uf' in args else None
arq_cidades = Path(args[args.index('--cidades') + 1]) if '--cidades' in args else Path(__file__).parent.parent / 'extension/public/cidades.json'
sai.mkdir(parents=True, exist_ok=True)

# --db arquivo: banco em disco (Brasil inteiro não cabe na memória da máquina do GitHub).
db = duckdb.connect(args[args.index('--db') + 1] if '--db' in args else ':memory:')
db.execute("SET preserve_insertion_order = false")
# concat_ws pula NULL: todo campo da linha passa por coalesce/limpa. Campo da Receita às vezes traz tab ou quebra de linha (complemento, nome): vira espaço para não quebrar a linha.
db.execute("CREATE MACRO limpa(x) AS trim(regexp_replace(coalesce(x, ''), '[\\t\\r\\n]+', ' ', 'g'))")


def tabela(nome, padrao, colunas):
    arqs = sorted(str(p) for p in ent.iterdir() if padrao.lower() in p.name.lower())
    if not arqs:
        raise SystemExit(f'faltou {padrao} em {ent}')
    cols = ', '.join(f"'{c}': 'VARCHAR'" for c in colunas)
    db.execute(f"""CREATE TABLE {nome} AS SELECT * FROM read_csv({arqs}, delim=';', quote='"', header=false,
        encoding='{enc}', columns={{{cols}}}, strict_mode=false, ignore_errors=true)""")


ESTAB = ['basico', 'ordem', 'dv', 'matriz', 'fantasia', 'situacao', 'data_sit', 'motivo', 'cidade_ext', 'pais', 'inicio',
         'cnae', 'cnae_sec', 'tipo_log', 'logradouro', 'numero', 'complemento', 'bairro', 'cep', 'uf', 'municipio',
         'ddd1', 'tel1', 'ddd2', 'tel2', 'ddd_fax', 'fax', 'email', 'sit_esp', 'data_esp']
tabela('estab', 'estab', ESTAB)
db.execute(f"""CREATE TABLE e AS SELECT * FROM estab WHERE situacao = '02' AND uf <> 'EX' {f"AND uf = '{so_uf}'" if so_uf else ''}
    AND (trim(coalesce(tel1, '')) <> '' OR trim(coalesce(tel2, '')) <> '' OR trim(coalesce(email, '')) <> '')""")
db.execute("DROP TABLE estab")
tabela('emp', 'empre', ['basico', 'razao', 'natureza', 'qualif_resp', 'capital', 'porte', 'ente'])
tabela('soc', 'socio', ['basico', 'tipo', 'nome', 'doc', 'qualif', 'entrada', 'pais', 'rep_doc', 'rep_nome', 'rep_qualif', 'faixa'])
tabela('simp', 'simples', ['basico', 'simples', 'simples_ini', 'simples_fim', 'mei', 'mei_ini', 'mei_fim'])
tabela('mun', 'munic', ['codigo', 'nome'])
tabela('cnaes', 'cnae', ['codigo', 'nome'])
tabela('nat', 'natureza', ['codigo', 'nome'])
tabela('qual', 'qualifica', ['codigo', 'nome'])
nomes = lambda t: {c: n for c, n in db.execute(f"SELECT codigo, limpa(nome) FROM {t}").fetchall()}
(sai / 'tabelas.json').write_text(json.dumps({'ref': ref, 'cnaes': nomes('cnaes'), 'naturezas': nomes('nat'), 'qualificacoes': nomes('qual')},
                                             ensure_ascii=False, separators=(',', ':')))

# Município da Receita (código próprio, nome em maiúsculas) → nome do cidades.json da extensão.
sem_acento = lambda x: re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFD', x).encode('ascii', 'ignore').decode().lower()).strip()
APELIDOS = {}  # (UF, nome Receita sem acento) → nome sem acento no cidades.json, para os que não casam
CIDADES = {(uf, sem_acento(n)): n for n, uf, *_ in json.loads(arq_cidades.read_text())}
sem_par = {}

def cidade(uf, nome):
    k = sem_acento(nome or '')
    n = CIDADES.get((uf, APELIDOS.get((uf, k), k)))
    if n is None:
        sem_par.setdefault(uf, set()).add(nome)
    return n or nome

# Sócios por empresa: "NOME~qualificação~AAAAMMDD", separados por "|" (até 10).
db.execute("""CREATE TABLE s AS SELECT basico, string_agg(replace(nome, '|', ' ') || '~' || coalesce(qualif, '') || '~' || coalesce(entrada, ''), '|') AS socios
    FROM (SELECT *, row_number() OVER (PARTITION BY basico ORDER BY entrada) AS n FROM soc WHERE basico IN (SELECT basico FROM e)) WHERE n <= 10 GROUP BY basico""")

# Telefone: 55 + DDD + número; celular antigo de 8 dígitos (começa com 6–9) ganha o 9.
tel = lambda d, t: f"""CASE WHEN trim(coalesce({t}, '')) = '' OR length(trim(coalesce({d}, ''))) <> 2 THEN ''
  WHEN length(trim({t})) = 8 AND substr(trim({t}), 1, 1) IN ('6', '7', '8', '9') THEN '55' || trim({d}) || '9' || trim({t})
  ELSE '55' || trim({d}) || trim({t}) END"""
linhas = db.execute(f"""
  SELECT e.uf, e.municipio, m.nome AS mun_nome, substr(e.cnae, 1, 2) AS divisao,
    concat_ws(chr(9), e.basico || e.ordem || e.dv, limpa(emp.razao), limpa(e.fantasia), coalesce(e.matriz, ''),
      coalesce(emp.natureza, ''), coalesce(emp.porte, ''), coalesce(emp.capital, ''), coalesce(simp.simples, ''), coalesce(simp.mei, ''),
      coalesce(e.inicio, ''), coalesce(e.cnae, ''), coalesce(e.cnae_sec, ''),
      limpa(concat_ws(' ', e.tipo_log, e.logradouro)) || CASE WHEN coalesce(e.numero, '') <> '' THEN ', ' || limpa(e.numero) ELSE '' END
        || CASE WHEN coalesce(e.complemento, '') <> '' THEN ' - ' || limpa(e.complemento) ELSE '' END,
      limpa(e.bairro), coalesce(e.cep, ''), {tel('e.ddd1', 'e.tel1')}, {tel('e.ddd2', 'e.tel2')}, lower(limpa(e.email)),
      limpa(s.socios)) AS linha
  FROM e LEFT JOIN emp USING (basico) LEFT JOIN simp USING (basico) LEFT JOIN s USING (basico)
  LEFT JOIN mun m ON m.codigo = e.municipio
  ORDER BY e.uf, e.municipio, divisao, e.cnae
""")

CAMPOS = ['cnpj', 'razao', 'fantasia', 'matriz', 'natureza', 'porte', 'capital', 'simples', 'mei', 'inicio', 'cnae',
          'cnaes_sec', 'endereco', 'bairro', 'cep', 'tel1', 'tel2', 'email', 'socios']
uf_atual, f, idx, bloco, chave, total = None, None, None, [], None, 0


def fechar_bloco():
    global bloco
    if not bloco:
        return
    dados = gzip.compress(('\n'.join(bloco) + '\n').encode('utf-8'), 9)
    ini = f.tell()
    f.write(dados)
    mun, div = chave
    idx['municipios'].setdefault(mun[0], {'nome': cidade(uf_atual, mun[1]), 'b': {}})['b'][div] = [ini, len(dados), len(bloco)]
    bloco = []


def fechar_uf():
    if f is None:
        return
    fechar_bloco()
    f.close()
    (sai / f'{uf_atual}.json').write_text(json.dumps(idx, ensure_ascii=False, separators=(',', ':')))
    print(uf_atual, sum(len(m['b']) for m in idx['municipios'].values()), 'blocos', (sai / f'{uf_atual}.bin').stat().st_size // 2**20, 'MB')


while True:
    lote = linhas.fetchmany(50_000)
    if not lote:
        break
    for uf, mun, mun_nome, div, linha in lote:
        if uf != uf_atual:
            fechar_uf()
            uf_atual, f = uf, open(sai / f'{uf}.bin', 'wb')
            idx = {'ref': ref, 'campos': CAMPOS, 'municipios': {}}
            chave = None
        k = ((mun, mun_nome or ''), div or '00')
        if k != chave:
            fechar_bloco()
            chave = k
        bloco.append(linha)
        total += 1
fechar_uf()
print('total', total)
for uf, ns in sorted(sem_par.items()):
    print('sem par no cidades.json', uf, len(ns), sorted(ns))
