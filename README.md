# leads-quentes-receita

Recorte dos dados abertos do CNPJ (Receita Federal) usado pela extensão Leads Quentes em Captar → Empresas (Receita).

Cada release (`AAAA-MM`) traz, por UF, `UF.bin` (blocos gzip independentes, um por município + divisão do CNAE) e
`UF.json` (índice), mais `tabelas.json` (CNAE, natureza jurídica e qualificação). Só empresas ativas com telefone ou
e-mail. Fonte: dados abertos do CNPJ, Receita Federal do Brasil (licença aberta).
