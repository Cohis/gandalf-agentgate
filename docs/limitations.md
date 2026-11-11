# Limitações e o que é real em cada número

Este documento existe para que nenhum resultado do projeto seja lido além do que ele mede.

## O que é real e o que é simulado

| Elemento | Situação |
|---|---|
| Navegador, TLS e JA4 | Reais. Todo o tráfego avaliado passa por um Chromium real ou por um cliente HTTP real, através do proxy. |
| Script de telemetria | Real. É o mesmo script que roda para um visitante. |
| Trajetórias de mouse dos humanos | Reais, reproduzidas de gravações do Balabit Mouse Dynamics Challenge. A amostragem original é de cerca de 10 Hz; os pontos intermediários são interpolados. |
| Ritmo e digitação dos humanos | **Modelados** (distribuições log-normais). Não vêm de gravações. |
| Navegação dos humanos | **Modelada**: probabilidades fixas de se distrair, comprar ou sair. |
| Endereços IP | **Fictícios.** O laboratório roda em uma máquina só; o gerador de tráfego informa o IP por um cabeçalho que só existe em `LAB_MODE`. |
| Relógio | **Comprimido.** Dias simulados cabem em horas. Dentro de uma sessão, o tempo é real. |
| Atacantes | Scripts e navegadores reais, escritos por mim. Não são ferramentas de fraude apreendidas. |

## A circularidade

O risco central de um projeto como este é treinar um modelo para reconhecer o próprio simulador. O que foi feito a respeito:

- **Nada de evento sintético direto.** O plano previa um gerador rápido que publicaria eventos direto no motor. Ele não foi construído: com este volume não é necessário, e seria a fonte mais óbvia de circularidade. Toda linha de treino veio de uma sessão de navegador ou de um cliente HTTP.
- **Mesma pilha para humanos simulados e para o atacante de nível 2.** Os dois usam o mesmo Chromium com o mesmo mascaramento. As features de ambiente (webdriver, user-agent, inconsistências) são idênticas entre eles, então o modelo não consegue separá-los por ali. Só o comportamento distingue.
- **Teste de generalização.** O modelo é retreinado sem nenhum exemplo do nível 2 e medido só no nível 2.
- **Divisão temporal por sessão.** O teste é o período mais recente, e nenhuma sessão aparece em dois conjuntos.

O que isso **não** resolve: os humanos simulados e os atacantes saíram da mesma cabeça. Um atacante que eu não imaginei pode não se parecer com nenhum dos dois. A fase 3 ataca isso com sessões humanas gravadas de pessoas reais e com um agente de IA de verdade.

## Limitações conhecidas dos sinais

- **Inconsistências de fingerprint.** O Chromium em modo headless renderiza WebGL por software, o que conta como uma inconsistência em toda sessão de navegador do laboratório, humana ou não. O sinal fica sem valor aqui; num navegador de verdade ele varia.
- **Tipo de rede.** Enquanto a fase 6 não chega, o motor classifica todo IP como "desconhecido". Quando chegar, o tipo de rede de cada sessão simulada terá sido escolhido pelo gerador, e portanto estará correlacionado com o rótulo por construção. Esse número precisará ser lido com esse cuidado.
- **Telemetria forjável.** Quem lê o script da página pode fabricar o que ele envia. O atacante de nível 4 fará exatamente isso.
- **JA4.** Se o proxy falhar ao calcular a impressão, um cabeçalho forjado pelo cliente pode passar adiante. É um sinal, nunca uma prova.
- **Ritmo de agentes.** As estatísticas de ritmo não dependem da escala, mas a hipótese de que um agente de IA tem intervalos regulares ainda não foi testada contra um agente real (fase 3).

## Tamanho da amostra

O período de teste tem poucas dezenas de sessões por grupo. Os intervalos de confiança do relatório são calculados reamostrando sessões inteiras e são largos. Uma taxa de 0% de falso positivo em 15 sessões de um perfil não significa que o perfil esteja seguro: significa que não houve erro em 15 tentativas.

## Modo sombra

Os dados de treino foram coletados com a loja em modo sombra: o motor é consultado, mas ninguém é barrado. Sem isso, um atacante bloqueado no cadastro nunca chegaria ao pagamento e essa parte do comportamento faltaria nos dados. A consequência é que as contagens de velocidade refletem um atacante que nunca foi interrompido.

## Replay offline e minimização de dados

O banco não guarda o IP cru nem o user-agent completo, por decisão de privacidade. A consequência é que os eventos gravados não bastam para recalcular todas as features do zero: o replay offline só reconstrói o que depende de conta, dispositivo, cartão e do hash do IP. A garantia de paridade entre online e offline é verificada em `tests/parity`, com as requisições originais em memória, e o treino usa sempre os vetores que o motor gravou.

## Medições de latência

As medições com o modelo no caminho foram feitas com o notebook em perfil de economia de energia e com outras aplicações em uso, e não são comparáveis com as da fase 1. Nas mesmas condições, com e sem modelo, a diferença foi de cerca de 0,5 ms na mediana.
