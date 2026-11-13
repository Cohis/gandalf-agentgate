# Fase 4: fazenda de contas com identidades geradas por LLM (candidatos, não ativados)

Coleta de 13/nov, feature set `f3`: base honesta (155 sessões: pessoas, fraude humana, níveis 1 e 2), fazenda A (50 pessoas + 70 contas falsas) e fazenda B (50 pessoas + 70 contas falsas, outro lote de identidades). 1.161 avaliações rotuladas. Na fazenda B, 5 das 70 contas falharam antes do cadastro (o formulário não apareceu em 30 s) e ficaram fora. Modelos gerados: automação `20261113-1532-f21838`, fraude `fr-20261113-1532-634061`. Relatórios completos em `eval/out/fase2.md` e `eval/out/fraude.md`.

## Resultado principal

Cada conta da fazenda é uma sessão que imita uma pessoa e conta como pega se qualquer evento dela foi desafiado ou negado.

| Fazenda (período de teste) | Contas | Modelo de automação no ar | Decisão final (automação no ar + fraude novo) | Das 20 primeiras |
|---|---|---|---|---|
| A | 45 | 0% | 100% | 100% |
| B | 32 | 0% | 94% | 95% |

O modelo de automação que está no ar não pega nenhuma conta: as contas da fazenda se comportam como pessoas. Quem pega é o modelo de fraude, com sinais de transação, velocidade e escala. Pelo ganho, os principais são o valor (36%), os eventos do aparelho em 24 h (12%), os cadastros do IP em 1 h (7%) e os cadastros em 6 h (4%).

### Fazenda nunca vista

Esta é a pergunta que importa para uma fazenda nova: o modelo de fraude foi treinado sem nenhuma conta da fazenda e aplicado a todas as contas dela.

| Fazenda nunca vista | Contas | Pegas | Das 20 primeiras | Primeira conta pega |
|---|---|---|---|---|
| A | 144 | 33% | 20% | 1 |
| B | 70 | 84% | 75% | 3 |

Os dois lotes não se comportam igual. A fazenda B, com bios mais parecidas entre si, é pega mesmo sem ter sido vista. A fazenda A, mais variada, escapa em dois terços das contas.

## Achado: o modelo de automação novo pega a fazenda pelo motivo errado

O candidato de automação marca 96% dos eventos de nível 4 no teste. Parece bom, mas tem dois problemas:

- **Sem ter visto nenhuma conta de nível 4**, o modelo detecta 0%.
- **Treinado numa fazenda e aplicado à outra**, pega 62% das contas de A e 93% das de B.

O que separa a fazenda é sobretudo `ritmo_cv`, a variação do intervalo entre ações:

| Grupo | `ritmo_cv` p10 | mediana | p90 |
|---|---|---|---|
| Pessoas simuladas | 0.33 | 0.47 | 0.67 |
| **Pessoas reais (11 voluntários)** | 0.42 | **0.73** | 1.18 |
| Fazenda | 0.68 | 0.95 | 1.29 |
| Outros robôs | 0.12 | 0.37 | 0.54 |

As pessoas reais ficam mais perto da fazenda do que das pessoas simuladas. O gerador de pessoas é regular demais, e o modelo aprende "irregular = fazenda". As pessoas reais ainda passam (0 de 200 eventos barrados, uma de fora por vez) porque estão no treino com peso 3. Mas o sinal é frágil, e a correção está na fonte: calibrar o ritmo do gerador pelos voluntários antes da coleta final. É o mesmo tipo de vazamento do ritmo absoluto da fase 2, agora num sinal relativo.

## Leitor de tela depois da correção do Tab

Com a telemetria de foco por teclado e a persona que navega por Tab, o perfil de leitor de tela teve **0 de 18 eventos** barrados pelo modelo de automação (10 sessões; era 15 de 24 desafiados ao vivo). A amostra é pequena e será repetida na coleta final.

## Atrito do modelo de fraude

O modelo de fraude desafia 12% dos eventos de pessoas honestas simuladas (eram 7%), e a decisão final chega a 15%. O que pesa nesses casos é valor baixo somado a aparelho novo (`ev_disp_24h` baixo). Na prática, é o cliente novo comprando o item mais barato, exatamente o padrão do teste de cartão. O desafio é uma confirmação fora da sessão (custo 0,5 na política), não uma negação. Mesmo assim é atrito demais, e a coleta final precisa medir isso com o ritmo corrigido.

## Custo por mil eventos no teste

| Política | Custo |
|---|---|
| Aprovar tudo | 400 |
| Só automação | 225 |
| Só fraude | 210 |
| Os dois cruzados (o motor) | 166 |

## Decisão

Nenhum dos dois candidatos foi ativado. O de automação aprendeu um artefato do simulador. O de fraude é melhor contra a fazenda, mas tem mais atrito, e os dois serão retreinados na coleta final da fase 7 com o ritmo calibrado e as camadas das fases 5 e 6 no ar.
