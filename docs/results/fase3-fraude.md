# Modelo de fraude e decisão cruzada (candidato, não ativado)

Gerado em 2026-11-12 16:56. Features `f2`, só as de velocidade e transação (35): o modelo nunca vê mouse, ritmo nem ambiente. 3876 eventos, 2263 rotulados como fraude.

## Limiares escolhidos por custo

Custos da política: fraude aprovada 20, cliente honesto negado 5, desafio 0.5; um desafio barra 80% das fraudes. Os custos são calculados como se 2% do tráfego fosse fraude (uma suposição da política): o laboratório tem muito mais ataques do que clientes, e sem essa correção desafiar todo mundo pareceria barato.

- Desafiar (confirmação fora da sessão) a partir de **0.7924**.
- Negar a partir de **0.9734**.
- Custo esperado na validação: 194.6 por mil eventos.
- Para comparação, o limiar de 1% de falso positivo seria 0.9291.

## Período de teste

PR-AUC: **0.9501**.

Sinalizado = desafiado ou negado.

| Grupo | Sessões | Eventos | Sinalizados pelo modelo de fraude | IC 95 % |
|---|---|---|---|---|
| Fraude humana: conta_roubada | 12 | 40 | 62.5% | 40.5% a 80.5% |
| Fraude humana: cupom_multicontas | 16 | 99 | 99.0% | 96.8% a 100.0% |
| Fraude humana: teste_cartao_manual | 13 | 108 | 94.4% | 87.5% a 100.0% |
| Atacante nível 1 | 12 | 39 | 74.4% | 60.6% a 87.8% |
| Atacante nível 2 | 28 | 94 | 73.4% | 66.7% a 79.5% |
| Atacante nível 3 | 71 | 189 | 51.3% | 44.2% a 59.5% |
| Pessoas honestas, simuladas | 79 | 173 | 6.9% | 3.4% a 11.2% |
| Passos não fraudulentos de fraudadores | 29 | 61 | 36.1% | 26.3% a 44.6% |

Cruzando com o modelo de automação que está no ar, como o motor faz:

| Grupo | Sessões | Eventos | Sinalizados pela decisão final | IC 95 % |
|---|---|---|---|---|
| Fraude humana: conta_roubada | 12 | 40 | 62.5% | 41.0% a 80.0% |
| Fraude humana: cupom_multicontas | 16 | 99 | 99.0% | 96.9% a 100.0% |
| Fraude humana: teste_cartao_manual | 13 | 108 | 94.4% | 87.0% a 100.0% |
| Atacante nível 1 | 12 | 39 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 | 28 | 94 | 100.0% | 100.0% a 100.0% |
| Atacante nível 3 | 71 | 189 | 100.0% | 100.0% a 100.0% |
| Pessoas honestas, simuladas | 79 | 173 | 10.4% | 5.4% a 16.8% |
| Passos não fraudulentos de fraudadores | 29 | 61 | 36.1% | 25.5% a 44.9% |

## Custo esperado por mil eventos no teste

| Política | Custo |
|---|---|
| Aprovar tudo | 400 |
| Só o modelo de automação | 237 |
| Só o modelo de fraude | 209 |
| Os dois cruzados (o motor) | 153 |

## O que o modelo usa (ganho)

| Feature | Parte do ganho |
|---|---|
| `valor` | 39.5% |
| `ev_disp_24h` | 11.8% |
| `ev_ip_24h` | 6.7% |
| `cartoes_por_disp_1h` | 5.4% |
| `cadastros_ip_1h` | 5.4% |
| `ev_disp_10m` | 3.7% |
| `ev_conta_10m` | 3.2% |
| `disps_por_conta_24h` | 3.0% |
| `ev_conta_24h` | 2.8% |
| `evento_login` | 2.7% |
