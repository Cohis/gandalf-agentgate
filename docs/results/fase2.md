# Fase 2: modelo de automação `20261110-2358-09d0ec`

Gerado em 2026-11-10 23:58. Features `f1`, 50 árvores. Dados: 1720 eventos em 605 sessões, cobrindo 6.0 dias simulados.

Todo o tráfego avaliado aqui passou por um navegador real ou por um cliente HTTP real contra a loja. Os humanos são simulados (trajetórias de mouse reais, ritmo e digitação modelados). Leia `docs/limitations.md` antes de citar estes números.

## Divisão temporal

| Conjunto | Nível | Sessões | Eventos |
|---|---|---|---|
| teste | 0 | 71 | 160 |
| teste | 1 | 18 | 79 |
| teste | 2 | 32 | 106 |
| treino | 0 | 215 | 466 |
| treino | 1 | 64 | 253 |
| treino | 2 | 84 | 298 |
| validacao | 0 | 69 | 160 |
| validacao | 1 | 18 | 67 |
| validacao | 2 | 34 | 131 |

## Resultado no período de teste (o mais recente)

### Modelo LightGBM (sem features de ritmo absoluto)

- PR-AUC: **1.0000** · ROC-AUC: 1.0000
- Recall com FPR fixa de 1% no próprio teste: **100.0%**
- Com o limiar escolhido na validação (0.2616): recall 100.0%, clientes bons barrados 0.0%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 18 | 79 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 32 | 106 | 100.0% | 100.0% a 100.0% |
| Humanos, todos (falso positivo) | 71 | 160 | 0.0% | 0.0% a 0.0% |
| Humano: controle_voz (falso positivo) | 7 | 22 | 0.0% | 0.0% a 0.0% |
| Humano: gerenciador_senhas (falso positivo) | 7 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: leitor_tela (falso positivo) | 4 | 6 | 0.0% | 0.0% a 0.0% |
| Humano: movel_cgnat (falso positivo) | 11 | 18 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 29 | 73 | 0.0% | 0.0% a 0.0% |
| Humano: troca_aparelho (falso positivo) | 8 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: viajante (falso positivo) | 5 | 7 | 0.0% | 0.0% a 0.0% |

### Linha de base: heurística escrita à mão

- PR-AUC: **0.9721** · ROC-AUC: 0.9780
- Recall com FPR fixa de 1% no próprio teste: **65.9%**
- Com o limiar escolhido na validação (0.2891): recall 71.9%, clientes bons barrados 2.5%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 18 | 79 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 32 | 106 | 50.9% | 43.0% a 59.8% |
| Humanos, todos (falso positivo) | 71 | 160 | 2.5% | 0.6% a 5.0% |
| Humano: controle_voz (falso positivo) | 7 | 22 | 13.6% | 4.2% a 26.1% |
| Humano: gerenciador_senhas (falso positivo) | 7 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: leitor_tela (falso positivo) | 4 | 6 | 0.0% | 0.0% a 0.0% |
| Humano: movel_cgnat (falso positivo) | 11 | 18 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 29 | 73 | 1.4% | 0.0% a 4.3% |
| Humano: troca_aparelho (falso positivo) | 8 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: viajante (falso positivo) | 5 | 7 | 0.0% | 0.0% a 0.0% |

## Ablação: e se o modelo visse a velocidade absoluta?

Com as 5 features de ritmo absoluto (média e mínimo dos intervalos, tempo de sessão), elas respondem por **79% do ganho** e a separação fica quase perfeita. Esse resultado **não é usado**: ele mede que os atacantes do laboratório são mais rápidos que as pessoas simuladas, o que é uma escolha minha de parâmetros, não uma propriedade de atacantes. O modelo em produção não recebe essas features.

### Com ritmo absoluto (descartado)

- PR-AUC: **1.0000** · ROC-AUC: 1.0000
- Recall com FPR fixa de 1% no próprio teste: **100.0%**
- Com o limiar escolhido na validação (0.1262): recall 100.0%, clientes bons barrados 0.0%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 18 | 79 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 32 | 106 | 100.0% | 100.0% a 100.0% |
| Humanos, todos (falso positivo) | 71 | 160 | 0.0% | 0.0% a 0.0% |
| Humano: controle_voz (falso positivo) | 7 | 22 | 0.0% | 0.0% a 0.0% |
| Humano: gerenciador_senhas (falso positivo) | 7 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: leitor_tela (falso positivo) | 4 | 6 | 0.0% | 0.0% a 0.0% |
| Humano: movel_cgnat (falso positivo) | 11 | 18 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 29 | 73 | 0.0% | 0.0% a 0.0% |
| Humano: troca_aparelho (falso positivo) | 8 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: viajante (falso positivo) | 5 | 7 | 0.0% | 0.0% a 0.0% |

## Generalização

Treinando **sem nenhum exemplo do nível 2** e testando só no nível 2: detecção de **0.0%** (106 eventos), com o limiar de 1% de falso positivo. É a medida de quanto o modelo pega um atacante que nunca viu.

## O que o modelo usa (ganho)

| Feature | Parte do ganho |
|---|---|
| `cursor_linear` | 73.8% |
| `cliques_centro` | 12.2% |
| `valor` | 4.0% |
| `ua_automacao` | 2.0% |
| `ritmo_cv` | 1.5% |
| `sessao_paginas` | 1.4% |
| `ritmo_n` | 1.2% |
| `sessao_desvios` | 0.7% |
| `ev_disp_10m` | 0.6% |
| `digitacao_constante` | 0.6% |
| `digitacao_humana` | 0.5% |
| `ritmo_p90_p50` | 0.5% |

## Limiares de operação

- Desafiar a partir de 0.2616 (1% de falso positivo na validação).
- Negar a partir de 0.9900 (acima de todo score legítimo visto na validação).
