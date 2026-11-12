# Modelo com toque e pessoas reais (ativo desde 12/nov/2026)

Gerado em 2026-11-12 09:51. Features `f2`, 8 árvores. Dados: 2440 eventos em 849 sessões, cobrindo 6.5 dias simulados.

Todo o tráfego avaliado aqui passou por um navegador real ou por um cliente HTTP real contra a loja. Os humanos são simulados (trajetórias de mouse reais, ritmo e digitação modelados). Leia `docs/limitations.md` antes de citar estes números.

## Divisão temporal

| Conjunto | Nível | Sessões | Eventos |
|---|---|---|---|
| teste | 0 | 86 | 196 |
| teste | 1 | 24 | 84 |
| teste | 2 | 51 | 158 |
| treino | 0 | 324 | 779 |
| treino | 1 | 71 | 261 |
| treino | 2 | 120 | 455 |
| validacao | 0 | 108 | 269 |
| validacao | 1 | 20 | 80 |
| validacao | 2 | 45 | 158 |

## Resultado no período de teste (o mais recente)

### Modelo LightGBM (sem features de ritmo absoluto)

- PR-AUC: **1.0000** · ROC-AUC: 1.0000
- Recall com FPR fixa de 1% no próprio teste: **100.0%**
- Com o limiar escolhido na validação (0.2916): recall 100.0%, clientes bons barrados 1.5%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 24 | 84 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 51 | 158 | 100.0% | 100.0% a 100.0% |
| Ferramenta: furtivo_toque (detecção) | 21 | 68 | 100.0% | 100.0% a 100.0% |
| Ferramenta: navegador_furtivo (detecção) | 30 | 90 | 100.0% | 100.0% a 100.0% |
| Ferramenta: playwright_padrao (detecção) | 13 | 38 | 100.0% | 100.0% a 100.0% |
| Ferramenta: script_http (detecção) | 11 | 46 | 100.0% | 100.0% a 100.0% |
| Humanos, todos (falso positivo) | 86 | 196 | 1.5% | 0.0% a 4.2% |
| Humano: celular (falso positivo) | 40 | 87 | 0.0% | 0.0% a 0.0% |
| Humano: controle_voz (falso positivo) | 3 | 6 | 16.7% | 0.0% a 100.0% |
| Humano: gerenciador_senhas (falso positivo) | 4 | 13 | 0.0% | 0.0% a 0.0% |
| Humano: leitor_tela (falso positivo) | 3 | 5 | 40.0% | 0.0% a 66.7% |
| Humano: movel_cgnat (falso positivo) | 10 | 22 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 24 | 58 | 0.0% | 0.0% a 0.0% |
| Humano: troca_aparelho (falso positivo) | 2 | 5 | 0.0% | 0.0% a 0.0% |

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

Com as 5 features de ritmo absoluto (média e mínimo dos intervalos, tempo de sessão), elas respondem por **21% do ganho** e a separação fica quase perfeita. Esse resultado **não é usado**: ele mede que os atacantes do laboratório são mais rápidos que as pessoas simuladas, o que é uma escolha minha de parâmetros, não uma propriedade de atacantes. O modelo em produção não recebe essas features.

### Com ritmo absoluto (descartado)

- PR-AUC: **1.0000** · ROC-AUC: 1.0000
- Recall com FPR fixa de 1% no próprio teste: **100.0%**
- Com o limiar escolhido na validação (0.1964): recall 100.0%, clientes bons barrados 1.5%

| Grupo | Sessões | Eventos | Sinalizados | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detecção) | 24 | 84 | 100.0% | 100.0% a 100.0% |
| Atacante nível 2 (detecção) | 51 | 158 | 100.0% | 100.0% a 100.0% |
| Ferramenta: furtivo_toque (detecção) | 21 | 68 | 100.0% | 100.0% a 100.0% |
| Ferramenta: navegador_furtivo (detecção) | 30 | 90 | 100.0% | 100.0% a 100.0% |
| Ferramenta: playwright_padrao (detecção) | 13 | 38 | 100.0% | 100.0% a 100.0% |
| Ferramenta: script_http (detecção) | 11 | 46 | 100.0% | 100.0% a 100.0% |
| Humanos, todos (falso positivo) | 86 | 196 | 1.5% | 0.0% a 4.3% |
| Humano: celular (falso positivo) | 40 | 87 | 0.0% | 0.0% a 0.0% |
| Humano: controle_voz (falso positivo) | 3 | 6 | 0.0% | 0.0% a 0.0% |
| Humano: gerenciador_senhas (falso positivo) | 4 | 13 | 0.0% | 0.0% a 0.0% |
| Humano: leitor_tela (falso positivo) | 3 | 5 | 60.0% | 0.0% a 100.0% |
| Humano: movel_cgnat (falso positivo) | 10 | 22 | 0.0% | 0.0% a 0.0% |
| Humano: padrao (falso positivo) | 24 | 58 | 0.0% | 0.0% a 0.0% |
| Humano: troca_aparelho (falso positivo) | 2 | 5 | 0.0% | 0.0% a 0.0% |

## Generalização

Treinando **sem nenhum exemplo do nível 2** e testando só no nível 2: detecção de **0.0%** (158 eventos), com o limiar de 1% de falso positivo. É a medida de quanto o modelo pega um atacante que nunca viu.

## O que o modelo usa (ganho)

| Feature | Parte do ganho |
|---|---|
| `cliques_centro` | 87.9% |
| `cursor_linear` | 5.0% |
| `ritmo_cv` | 3.5% |
| `valor` | 1.9% |
| `ritmo_p90_p50` | 0.7% |
| `digitacao_humana` | 0.5% |
| `sessao_desvios` | 0.4% |

## Limiares de operação

- Desafiar a partir de 0.2916 (1% de falso positivo na validação).
- Negar a partir de 0.9900 (acima de todo score legítimo visto na validação).
