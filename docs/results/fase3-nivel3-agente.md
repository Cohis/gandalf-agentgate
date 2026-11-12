# Candidato com o atacante de nível 3 (agente de IA local), não ativado

Gerado em 2026-11-12 15:20. Features `f2`, 45 árvores. Dados: 2849 eventos em 1004 sessões, cobrindo 6.7 dias simulados.

Todo o tráfego avaliado aqui passou por um navegador real ou por um cliente HTTP real contra a loja. Os humanos são simulados (trajetórias de mouse reais, ritmo e digitação modelados). Leia `docs/limitations.md` antes de citar estes números.

## Divisão temporal

| Conjunto | Nível | Sessões | Eventos |
|---|---|---|---|
| teste | 0 | 58 | 125 |
| teste | 1 | 17 | 54 |
| teste | 2 | 35 | 111 |
| teste | 3 | 82 | 214 |
| treino | 0 | 386 | 915 |
| treino | 1 | 81 | 294 |
| treino | 2 | 141 | 528 |
| validacao | 0 | 74 | 204 |
| validacao | 1 | 17 | 77 |
| validacao | 2 | 40 | 132 |
| validacao | 3 | 73 | 195 |

## Resultado no período de teste (o mais recente)

### Modelo LightGBM (sem features de ritmo absoluto)

- PR-AUC: **1.0000** · ROC-AUC: 0.9999
- Recall com FPR fixa de 1% no próprio teste: **100.0%**
- Com o limiar escolhido na validação (0.2548): recall 100.0%, clientes bons barrados 2.4%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 17 | 54 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 35 | 111 | 100.0% | 100.0% a 100.0% |
| Atacante nível 3 (detecção) | 82 | 214 | 100.0% | 100.0% a 100.0% |
| Ferramenta: agente_llm (detecção) | 82 | 214 | 100.0% | 100.0% a 100.0% |
| Ferramenta: furtivo_toque (detecção) | 16 | 60 | 100.0% | 100.0% a 100.0% |
| Ferramenta: navegador_furtivo (detecção) | 19 | 51 | 100.0% | 100.0% a 100.0% |
| Ferramenta: playwright_padrao (detecção) | 10 | 31 | 100.0% | 100.0% a 100.0% |
| Ferramenta: script_http (detecção) | 7 | 23 | 100.0% | 100.0% a 100.0% |
| Humanos, todos (falso positivo) | 58 | 125 | 2.4% | 0.0% a 7.4% |
| Humano: celular (falso positivo) | 27 | 52 | 0.0% | 0.0% a 0.0% |
| Humano: gerenciador_senhas (falso positivo) | 3 | 10 | 0.0% | 0.0% a 0.0% |
| Humano: leitor_tela (falso positivo) | 3 | 5 | 60.0% | 0.0% a 100.0% |
| Humano: movel_cgnat (falso positivo) | 7 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 18 | 41 | 0.0% | 0.0% a 0.0% |

## Pessoas reais: uma de fora por vez

11 voluntários gravados (47 sessões, 200 eventos). Para cada um, o modelo é retreinado **sem nenhuma sessão dessa pessoa** e depois aplicado a ela. É a estimativa de falso positivo para alguém que o sistema nunca viu. O modelo final treina com cerca de três quartos das pessoas e usa as demais para escolher o limiar.

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| **Todas as pessoas reais** | 47 | 200 | 0.0% | 0.0% a 0.0% |
| No computador (mouse) | 22 | 99 | 0.0% | 0.0% a 0.0% |
| No celular (toque) | 25 | 101 | 0.0% | 0.0% a 0.0% |
| Participante 12345 | 5 | 14 | 0.0% | 0.0% a 0.0% |
| Participante A2 | 5 | 16 | 0.0% | 0.0% a 0.0% |
| Participante AMANDHA | 6 | 32 | 0.0% | 0.0% a 0.0% |
| Participante ANE | 1 | 5 | 0.0% | nan% a nan% |
| Participante EITA | 2 | 7 | 0.0% | 0.0% a 0.0% |
| Participante GABRIELA | 2 | 7 | 0.0% | 0.0% a 0.0% |
| Participante GDGSJC | 6 | 22 | 0.0% | 0.0% a 0.0% |
| Participante P89 | 6 | 35 | 0.0% | 0.0% a 0.0% |
| Participante PAMONHA | 2 | 6 | 0.0% | 0.0% a 0.0% |
| Participante PH112006 | 6 | 26 | 0.0% | 0.0% a 0.0% |
| Participante SIGMA | 6 | 30 | 0.0% | 0.0% a 0.0% |

## Ablação: e se o modelo visse a velocidade absoluta?

Com as 5 features de ritmo absoluto (média e mínimo dos intervalos, tempo de sessão), elas respondem por **20% do ganho** e a separação fica quase perfeita. Esse resultado **não é usado**: ele mede que os atacantes do laboratório são mais rápidos que as pessoas simuladas, o que é uma escolha minha de parâmetros, não uma propriedade de atacantes. O modelo em produção não recebe essas features.

### Com ritmo absoluto (descartado)

- PR-AUC: **0.9997** · ROC-AUC: 0.9993
- Recall com FPR fixa de 1% no próprio teste: **100.0%**
- Com o limiar escolhido na validação (0.1080): recall 100.0%, clientes bons barrados 2.4%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 17 | 54 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 35 | 111 | 100.0% | 100.0% a 100.0% |
| Atacante nível 3 (detecção) | 82 | 214 | 100.0% | 100.0% a 100.0% |
| Ferramenta: agente_llm (detecção) | 82 | 214 | 100.0% | 100.0% a 100.0% |
| Ferramenta: furtivo_toque (detecção) | 16 | 60 | 100.0% | 100.0% a 100.0% |
| Ferramenta: navegador_furtivo (detecção) | 19 | 51 | 100.0% | 100.0% a 100.0% |
| Ferramenta: playwright_padrao (detecção) | 10 | 31 | 100.0% | 100.0% a 100.0% |
| Ferramenta: script_http (detecção) | 7 | 23 | 100.0% | 100.0% a 100.0% |
| Humanos, todos (falso positivo) | 58 | 125 | 2.4% | 0.0% a 6.7% |
| Humano: celular (falso positivo) | 27 | 52 | 0.0% | 0.0% a 0.0% |
| Humano: gerenciador_senhas (falso positivo) | 3 | 10 | 0.0% | 0.0% a 0.0% |
| Humano: leitor_tela (falso positivo) | 3 | 5 | 60.0% | 0.0% a 100.0% |
| Humano: movel_cgnat (falso positivo) | 7 | 17 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 18 | 41 | 0.0% | 0.0% a 0.0% |

## Generalização

Para cada nível de atacante, um modelo é treinado **sem nenhum exemplo daquele nível** e aplicado só a ele, com o limiar de 1% de falso positivo. É a medida de quanto o sistema pega um atacante que nunca viu.

| Nível deixado de fora | Eventos no teste | Detectados sem ter visto |
|---|---|---|
| 1 | 54 | 53.7% |
| 2 | 111 | 55.0% |
| 3 | 214 | 100.0% |

## O que o modelo usa (ganho)

| Feature | Parte do ganho |
|---|---|
| `cliques_centro` | 76.1% |
| `cursor_linear` | 10.2% |
| `ritmo_cv` | 4.5% |
| `valor` | 2.0% |
| `sessao_desvios` | 1.6% |
| `ua_movel` | 1.4% |
| `toque` | 1.1% |
| `digitacao_humana` | 0.8% |
| `sessao_paginas` | 0.6% |
| `ja4_ausente` | 0.5% |
| `ua_automacao` | 0.4% |
| `digitacao_constante` | 0.3% |

## Limiares de operação

- Desafiar a partir de 0.2548 (1% de falso positivo na validação).
- Negar a partir de 0.9900 (acima de todo score legítimo visto na validação).
