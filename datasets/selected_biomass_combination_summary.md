# Resumo da selecao final de arquivos BIOMASS

## Objetivo

Selecionar, dentro de `H:/biomass_data/`, um subconjunto de arquivos `.zip` capaz de cobrir o conjunto de pares unicos `track-frame` de interesse, usando datas o mais proximas possivel entre si.

## Fonte analisada

- Pasta analisada: `H:/biomass_data/`
- Produtos considerados: arquivos com prefixo `BIO_` e extensao `.zip`
- Arquivos ignorados para a analise de cobertura: `.zip.part` e arquivos que nao seguem o padrao esperado do nome

## Estrutura usada no parse do nome

Para cada arquivo, o nome foi dividido por `_`, ignorando tokens vazios. A partir disso:

- `date`: token de data de inicio da aquisicao, no formato `YYYYMMDD`
- `track`: bloco como `T020`
- `frame`: bloco como `F297`

Exemplo:

`BIO_S1_SCS__1S_20251122T084445_20251122T084505_T_G01_M01_C01_T020_F297_01_DNHP1M.zip`

gera:

- `date = 20251122`
- `track = T020`
- `frame = F297`

## Exclusoes solicitadas

Os seguintes pares `track-frame` foram removidos do universo de interesse antes da otimizacao:

- `T013_F174`
- `T013_F172`
- `T020_F294`
- `T020_F292`

## Universo final de interesse

Depois das exclusoes, o universo analisado ficou com `100` pares unicos `track-frame`.

Distribuicao por track:

- `T006`: 21 frames, de `F289` a `F309`
- `T014`: 2 frames, `F157` e `F158`
- `T020`: 16 frames, `F293`, `F295` a `F309`
- `T028`: 20 frames, de `F157` a `F176`
- `T035`: 21 frames, de `F289` a `F309`
- `T043`: 20 frames, de `F157` a `F176`

## Busca da combinacao minima de datas

Foi feita uma busca combinatoria sobre as datas presentes na pasta para encontrar a menor combinacao cuja uniao cobrisse os `100` pares de interesse.

Resultado:

- Numero minimo de datas necessario: `4`
- Numero de solucoes minimas encontradas: `16`

Todas as solucoes minimas exigem uma data adicional em `2026-02-17` para completar os pares faltantes de `T020`, que nao aparecem integralmente em nenhuma unica data de 2025.

## Criterio de desempate adotado

Entre as combinacoes minimas de 4 datas, foi escolhida a combinacao com as datas mais proximas entre si.

Combinacao escolhida:

1. `2025-11-24`
2. `2025-11-25`
3. `2025-11-26`
4. `2026-02-17`

Motivo da escolha:

- forma o bloco temporal mais compacto em 2025, com tres dias consecutivos
- preserva cobertura total dos pares `track-frame` de interesse

## Cobertura da combinacao escolhida

- `2025-11-24`: cobre `T006` e `T014`
- `2025-11-25`: cobre `T020` e `T028`
- `2025-11-26`: cobre `T035` e `T043`
- `2026-02-17`: complementa os pares faltantes de `T020`

## Regra final de retencao de arquivos

Um arquivo foi mantido se, e somente se, atendesse simultaneamente aos criterios abaixo:

1. estar em uma das datas selecionadas
2. pertencer a um par `track-frame` nao excluido
3. ser um arquivo `BIO_*.zip`

Arquivos `.zip.part` foram removidos.

## Resultado aplicado na pasta

- Arquivos mantidos: `242`
- Arquivos removidos: `646`

## Quantidade de arquivos mantidos por data e track

| Data | Track | Arquivos mantidos |
| --- | --- | ---: |
| 20251124 | T006 | 63 |
| 20251124 | T014 | 6 |
| 20251124 | Total da data | 69 |
| 20251125 | T020 | 29 |
| 20251125 | T028 | 39 |
| 20251125 | Total da data | 68 |
| 20251126 | T035 | 42 |
| 20251126 | T043 | 40 |
| 20251126 | Total da data | 82 |
| 20260217 | T020 | 10 |
| 20260217 | T028 | 13 |
| 20260217 | Total da data | 23 |

O detalhamento completo dos arquivos mantidos foi salvo em:

- `datasets/selected_biomass_combination_20251124_20251125_20251126_20260217.csv`

Esse CSV contem, para cada arquivo mantido:

- `file_name`
- `date`
- `track`
- `frame`

## Observacao importante

O processo nao escolheu apenas um arquivo por par `track-frame`. Todos os arquivos da pasta que pertenciam a uma data selecionada e a um par de interesse foram mantidos. Por isso existem multiplos arquivos para um mesmo `track-frame`, quando havia repeticoes do produto na pasta original.

## Quantidade de arquivos mantidos por data, track e frame

| Data | Track | Frame | Arquivos mantidos |
| --- | --- | --- | ---: |
| 20251124 | T006 | F289 | 3 |
| 20251124 | T006 | F290 | 3 |
| 20251124 | T006 | F291 | 3 |
| 20251124 | T006 | F292 | 3 |
| 20251124 | T006 | F293 | 3 |
| 20251124 | T006 | F294 | 3 |
| 20251124 | T006 | F295 | 3 |
| 20251124 | T006 | F296 | 3 |
| 20251124 | T006 | F297 | 3 |
| 20251124 | T006 | F298 | 3 |
| 20251124 | T006 | F299 | 3 |
| 20251124 | T006 | F300 | 3 |
| 20251124 | T006 | F301 | 3 |
| 20251124 | T006 | F302 | 3 |
| 20251124 | T006 | F303 | 3 |
| 20251124 | T006 | F304 | 3 |
| 20251124 | T006 | F305 | 3 |
| 20251124 | T006 | F306 | 3 |
| 20251124 | T006 | F307 | 3 |
| 20251124 | T006 | F308 | 3 |
| 20251124 | T006 | F309 | 3 |
| 20251124 | T014 | F157 | 3 |
| 20251124 | T014 | F158 | 3 |
| 20251125 | T020 | F295 | 2 |
| 20251125 | T020 | F296 | 2 |
| 20251125 | T020 | F297 | 2 |
| 20251125 | T020 | F298 | 2 |
| 20251125 | T020 | F299 | 2 |
| 20251125 | T020 | F300 | 2 |
| 20251125 | T020 | F301 | 2 |
| 20251125 | T020 | F302 | 2 |
| 20251125 | T020 | F303 | 2 |
| 20251125 | T020 | F304 | 2 |
| 20251125 | T020 | F305 | 2 |
| 20251125 | T020 | F306 | 2 |
| 20251125 | T020 | F307 | 2 |
| 20251125 | T020 | F308 | 2 |
| 20251125 | T020 | F309 | 1 |
| 20251125 | T028 | F157 | 2 |
| 20251125 | T028 | F158 | 2 |
| 20251125 | T028 | F159 | 2 |
| 20251125 | T028 | F160 | 2 |
| 20251125 | T028 | F161 | 2 |
| 20251125 | T028 | F162 | 1 |
| 20251125 | T028 | F163 | 2 |
| 20251125 | T028 | F164 | 2 |
| 20251125 | T028 | F165 | 2 |
| 20251125 | T028 | F166 | 2 |
| 20251125 | T028 | F167 | 2 |
| 20251125 | T028 | F168 | 2 |
| 20251125 | T028 | F169 | 2 |
| 20251125 | T028 | F170 | 2 |
| 20251125 | T028 | F171 | 2 |
| 20251125 | T028 | F172 | 2 |
| 20251125 | T028 | F173 | 2 |
| 20251125 | T028 | F174 | 2 |
| 20251125 | T028 | F175 | 2 |
| 20251125 | T028 | F176 | 2 |
| 20251126 | T035 | F289 | 2 |
| 20251126 | T035 | F290 | 2 |
| 20251126 | T035 | F291 | 1 |
| 20251126 | T035 | F292 | 2 |
| 20251126 | T035 | F293 | 2 |
| 20251126 | T035 | F294 | 2 |
| 20251126 | T035 | F295 | 2 |
| 20251126 | T035 | F296 | 3 |
| 20251126 | T035 | F297 | 2 |
| 20251126 | T035 | F298 | 2 |
| 20251126 | T035 | F299 | 2 |
| 20251126 | T035 | F300 | 2 |
| 20251126 | T035 | F301 | 2 |
| 20251126 | T035 | F302 | 2 |
| 20251126 | T035 | F303 | 2 |
| 20251126 | T035 | F304 | 2 |
| 20251126 | T035 | F305 | 2 |
| 20251126 | T035 | F306 | 2 |
| 20251126 | T035 | F307 | 2 |
| 20251126 | T035 | F308 | 2 |
| 20251126 | T035 | F309 | 2 |
| 20251126 | T043 | F157 | 2 |
| 20251126 | T043 | F158 | 2 |
| 20251126 | T043 | F159 | 2 |
| 20251126 | T043 | F160 | 2 |
| 20251126 | T043 | F161 | 2 |
| 20251126 | T043 | F162 | 2 |
| 20251126 | T043 | F163 | 2 |
| 20251126 | T043 | F164 | 2 |
| 20251126 | T043 | F165 | 2 |
| 20251126 | T043 | F166 | 2 |
| 20251126 | T043 | F167 | 2 |
| 20251126 | T043 | F168 | 2 |
| 20251126 | T043 | F169 | 2 |
| 20251126 | T043 | F170 | 2 |
| 20251126 | T043 | F171 | 2 |
| 20251126 | T043 | F172 | 2 |
| 20251126 | T043 | F173 | 2 |
| 20251126 | T043 | F174 | 2 |
| 20251126 | T043 | F175 | 2 |
| 20251126 | T043 | F176 | 2 |
| 20260217 | T020 | F293 | 1 |
| 20260217 | T020 | F295 | 1 |
| 20260217 | T020 | F297 | 1 |
| 20260217 | T020 | F298 | 1 |
| 20260217 | T020 | F299 | 1 |
| 20260217 | T020 | F300 | 1 |
| 20260217 | T020 | F302 | 1 |
| 20260217 | T020 | F303 | 1 |
| 20260217 | T020 | F305 | 1 |
| 20260217 | T020 | F306 | 1 |
| 20260217 | T028 | F157 | 1 |
| 20260217 | T028 | F158 | 1 |
| 20260217 | T028 | F161 | 1 |
| 20260217 | T028 | F162 | 1 |
| 20260217 | T028 | F164 | 1 |
| 20260217 | T028 | F166 | 1 |
| 20260217 | T028 | F168 | 1 |
| 20260217 | T028 | F169 | 1 |
| 20260217 | T028 | F170 | 1 |
| 20260217 | T028 | F172 | 1 |
| 20260217 | T028 | F173 | 1 |
| 20260217 | T028 | F175 | 1 |
| 20260217 | T028 | F176 | 1 |