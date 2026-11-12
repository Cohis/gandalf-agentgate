# Candidato treinado no layout novo, testado em pessoas reais (não ativado)

Gerado em 2026-11-12 08:29. Features `f1`, 32 árvores. Dados: 1878 eventos em 643 sessões, cobrindo 6.5 dias simulados.

Todo o tráfego avaliado aqui passou por um navegador real ou por um cliente HTTP real contra a loja. Os humanos são simulados (trajetórias de mouse reais, ritmo e digitação modelados). Leia `docs/limitations.md` antes de citar estes números.

## Divisão temporal

| Conjunto | Nível | Sessões | Eventos |
|---|---|---|---|
| teste | 0 | 85 | 278 |
| teste | 1 | 22 | 88 |
| teste | 2 | 22 | 63 |
| treino | 0 | 224 | 470 |
| treino | 1 | 61 | 224 |
| treino | 2 | 100 | 382 |
| validacao | 0 | 84 | 204 |
| validacao | 1 | 17 | 66 |
| validacao | 2 | 28 | 103 |

## Resultado no período de teste (o mais recente)

### Modelo LightGBM (sem features de ritmo absoluto)

- PR-AUC: **0.9986** · ROC-AUC: 0.9992
- Recall com FPR fixa de 1% no próprio teste: **98.7%**
- Com o limiar escolhido na validação (0.3758): recall 99.3%, clientes bons barrados 6.5%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 22 | 88 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 22 | 63 | 98.4% | 94.7% a 100.0% |
| Humanos, todos (falso positivo) | 85 | 278 | 6.5% | 3.2% a 10.1% |
| Humanos REAIS gravados (falso positivo) | 36 | 164 | 10.4% | 5.3% a 16.1% |
| Humanos simulados (falso positivo) | 49 | 114 | 0.9% | 0.0% a 2.9% |
| Humano: controle_voz (falso positivo) | 6 | 16 | 0.0% | 0.0% a 0.0% |
| Humano: gerenciador_senhas (falso positivo) | 8 | 22 | 4.5% | 0.0% a 15.0% |
| Humano: leitor_tela (falso positivo) | 4 | 6 | 0.0% | 0.0% a 0.0% |
| Humano: movel_cgnat (falso positivo) | 8 | 15 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 20 | 52 | 0.0% | 0.0% a 0.0% |
| Humano: real_A2 (falso positivo) | 5 | 16 | 0.0% | 0.0% a 0.0% |
| Humano: real_AMANDHA (falso positivo) | 6 | 32 | 28.1% | 12.8% a 51.9% |
| Humano: real_ANE (falso positivo) | 1 | 5 | 0.0% | nan% a nan% |
| Humano: real_EITA (falso positivo) | 2 | 7 | 14.3% | 0.0% a 100.0% |
| Humano: real_GABRIELA (falso positivo) | 2 | 7 | 0.0% | 0.0% a 0.0% |
| Humano: real_P89 (falso positivo) | 6 | 35 | 0.0% | 0.0% a 0.0% |
| Humano: real_PAMONHA (falso positivo) | 2 | 6 | 0.0% | 0.0% a 0.0% |
| Humano: real_PH112006 (falso positivo) | 6 | 26 | 26.9% | 19.4% a 38.5% |
| Humano: real_SIGMA (falso positivo) | 6 | 30 | 0.0% | 0.0% a 0.0% |
| Humano: troca_aparelho (falso positivo) | 1 | 1 | 0.0% | nan% a nan% |
| Humano: viajante (falso positivo) | 2 | 2 | 0.0% | 0.0% a 0.0% |

## Ablação: e se o modelo visse a velocidade absoluta?

Com as 5 features de ritmo absoluto (média e mínimo dos intervalos, tempo de sessão), elas respondem por **72% do ganho** e a separação fica quase perfeita. Esse resultado **não é usado**: ele mede que os atacantes do laboratório são mais rápidos que as pessoas simuladas, o que é uma escolha minha de parâmetros, não uma propriedade de atacantes. O modelo em produção não recebe essas features.

### Com ritmo absoluto (descartado)

- PR-AUC: **0.9998** · ROC-AUC: 0.9999
- Recall com FPR fixa de 1% no próprio teste: **99.3%**
- Com o limiar escolhido na validação (0.2366): recall 100.0%, clientes bons barrados 21.2%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 22 | 88 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 22 | 63 | 100.0% | 100.0% a 100.0% |
| Humanos, todos (falso positivo) | 85 | 278 | 21.2% | 13.0% a 30.9% |
| Humanos REAIS gravados (falso positivo) | 36 | 164 | 35.4% | 22.5% a 51.0% |
| Humanos simulados (falso positivo) | 49 | 114 | 0.9% | 0.0% a 2.9% |
| Humano: controle_voz (falso positivo) | 6 | 16 | 0.0% | 0.0% a 0.0% |
| Humano: gerenciador_senhas (falso positivo) | 8 | 22 | 4.5% | 0.0% a 15.8% |
| Humano: leitor_tela (falso positivo) | 4 | 6 | 0.0% | 0.0% a 0.0% |
| Humano: movel_cgnat (falso positivo) | 8 | 15 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 20 | 52 | 0.0% | 0.0% a 0.0% |
| Humano: real_A2 (falso positivo) | 5 | 16 | 12.5% | 0.0% a 22.2% |
| Humano: real_AMANDHA (falso positivo) | 6 | 32 | 31.2% | 5.0% a 75.0% |
| Humano: real_ANE (falso positivo) | 1 | 5 | 20.0% | nan% a nan% |
| Humano: real_EITA (falso positivo) | 2 | 7 | 14.3% | 0.0% a 100.0% |
| Humano: real_GABRIELA (falso positivo) | 2 | 7 | 28.6% | 25.0% a 33.3% |
| Humano: real_P89 (falso positivo) | 6 | 35 | 8.6% | 1.5% a 23.8% |
| Humano: real_PAMONHA (falso positivo) | 2 | 6 | 33.3% | 33.3% a 33.3% |
| Humano: real_PH112006 (falso positivo) | 6 | 26 | 50.0% | 19.2% a 87.0% |
| Humano: real_SIGMA (falso positivo) | 6 | 30 | 80.0% | 50.0% a 100.0% |
| Humano: troca_aparelho (falso positivo) | 1 | 1 | 0.0% | nan% a nan% |
| Humano: viajante (falso positivo) | 2 | 2 | 0.0% | 0.0% a 0.0% |

## Generalização

Treinando **sem nenhum exemplo do nível 2** e testando só no nível 2: detecção de **0.0%** (63 eventos), com o limiar de 1% de falso positivo. É a medida de quanto o modelo pega um atacante que nunca viu.

## O que o modelo usa (ganho)

| Feature | Parte do ganho |
|---|---|
| `cursor_linear` | 72.5% |
| `cliques_centro` | 13.5% |
| `ritmo_cv` | 4.9% |
| `sessao_desvios` | 2.3% |
| `digitacao_humana` | 1.7% |
| `ua_automacao` | 1.5% |
| `ritmo_p90_p50` | 1.0% |
| `valor` | 1.0% |
| `sessao_paginas` | 0.8% |
| `inconsistencias_fp` | 0.4% |
| `cadastros_ip_1h` | 0.2% |
| `disps_por_conta_24h` | 0.1% |

## Limiares de operação

- Desafiar a partir de 0.3758 (1% de falso positivo na validação).
- Negar a partir de 0.9900 (acima de todo score legítimo visto na validação).
