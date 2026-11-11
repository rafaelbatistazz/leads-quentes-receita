#!/bin/bash
# Baixa a base aberta do CNPJ de um mês e já filtra em fluxo (o disco não aguenta tudo descompactado):
# Estabelecimentos ativos (situação 02) com telefone ou e-mail, de uma UF ou do Brasil; Empresas, Sócios e
# Simples só dessas empresas. Servidor da Receita (Nextcloud) corta com muitas conexões: 3 por vez, com retomada.
# Uso: receita/baixar.sh <pasta> <AAAA-MM> [UF]
set -u
D=$1; REF=$2; UF=${3:-}
U="https://arquivos.receitafederal.gov.br/public.php/dav/files/gn672Ad4CF8N6TK/Dados/Cadastros/CNPJ/$REF"
mkdir -p "$D/zip" "$D/csv"

pega() {
  unzip -tq "$D/zip/$1" >/dev/null 2>&1 && return 0
  for _ in 1 2 3 4 5 6 7 8; do
    curl -sfL -C - --retry 3 --speed-time 120 --speed-limit 1000 -o "$D/zip/$1" "$U/$1"
    unzip -tq "$D/zip/$1" >/dev/null 2>&1 && return 0
    sleep 10
  done
  echo "FALHOU $1"; return 1
}

for f in Cnaes Naturezas Qualificacoes Municipios; do pega $f.zip && unzip -p "$D/zip/$f.zip" > "$D/csv/$f.csv"; done

# Campos separados por ";" com aspas: 6 = situação, 20 = UF, 23/25 = telefones, 28 = e-mail.
estab() {
  pega Estabelecimentos$1.zip || return 1
  unzip -p "$D/zip/Estabelecimentos$1.zip" | LC_ALL=C awk -F'";"' -v uf="$UF" \
    '$6=="02" && $20!="EX" && (uf=="" || $20==uf) && ($23!="" || $25!="" || $28!="")' > "$D/csv/estab$1.csv"
  rm "$D/zip/Estabelecimentos$1.zip"; echo "estab $1 ok"
}
for i in 0 1 2 3 4 5 6 7 8 9; do estab $i & if (( i % 3 == 2 )); then wait; fi; done; wait

LC_ALL=C cat "$D"/csv/estab*.csv | LC_ALL=C cut -c2-9 | LC_ALL=C sort -u > "$D/basicos.txt"; wc -l < "$D/basicos.txt"
filtra() {
  pega $1.zip || return 1
  unzip -p "$D/zip/$1.zip" | LC_ALL=C awk -F'";"' 'NR==FNR{k[$0]=1;next} k[substr($1,2)]' "$D/basicos.txt" - > "$D/csv/$1.csv"
  rm "$D/zip/$1.zip"; echo "$1 ok"
}
n=0; for f in Simples Empresas{0..9} Socios{0..9}; do filtra $f & n=$((n+1)); if (( n % 3 == 0 )); then wait; fi; done; wait
ls "$D"/csv | wc -l; du -sh "$D/csv"
for f in "$D"/csv/*.csv; do [ -s "$f" ] || { echo "vazio: $f"; exit 1; }; done
