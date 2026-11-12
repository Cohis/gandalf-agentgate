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

## Pessoas reais e celular (12/nov/2026)

- **O que as gravações mostraram.** Um modelo treinado só com humanos simulados barrava 10,4% dos eventos de voluntários reais, todos de quem usava celular: no toque não há trajetória de cursor, e isso era o principal sinal de script. Com telemetria de toque e pessoas reais no treino, a estimativa deixando uma pessoa de fora por vez foi de 0 em 200 eventos (11 voluntários).
- **Onze pessoas não são a população.** São conhecidos de uma só rede de contatos, e 0 em 200 não significa taxa zero.
- **O modelo agora depende de um sinal só.** 88% do ganho vem de `cliques_centro`: scripts clicam no centro exato dos elementos. Um script que sorteie a posição do clique dentro do botão derruba a detecção. Esse é o atacante de nível 4.
- **Acessibilidade continua sendo o ponto fraco.** No teste, 2 de 5 eventos do perfil leitor de tela e 1 de 6 do controle por voz foram sinalizados. As amostras são mínimas, mas a direção é ruim: quem não usa mouse nem toque se parece com um script.
- **Toque emulado.** O toque dos humanos simulados e dos scripts vem do mesmo emulador, com tamanho de contato fixo. Por isso o tamanho do contato é registrado mas não entra no modelo.
- **Gravações antigas de celular.** As feitas antes da telemetria de toque tiveram o toque deduzido por regra (`upgrade_f1`), não medido.

## Atacante de nível 3: agente de IA local (12/nov/2026)

- **O agente é um Qwen3.5 de 9B rodando no notebook**, não um agente comercial. Ele é lento (cerca de 2 s por ação na placa em velocidade normal) e conclui pouco: relatou sucesso em 45% de 150 sessões.
- **O laço do agente é próprio.** A biblioteca browser-use não funcionou com esse modelo. O laço age como as bibliotecas comuns (clique no centro do elemento, valor escrito direto no campo), mas um agente que mova o mouse de verdade ou use visão computacional não está coberto.
- **Ele é detectado pelo mesmo sinal que os scripts**: 100% dos cliques no centro exato e nenhum movimento de mouse. Por isso o modelo o pega mesmo sem nunca ter visto um exemplo dele. O resultado diz mais sobre o jeito de agir do laço do que sobre agentes de IA em geral.
- **Seguiu a instrução escondida na página** em 23% dos eventos e nunca preencheu o campo honeypot.
- **Parte das sessões rodou com a placa de vídeo em clock reduzido**, o que deixou os intervalos entre ações maiores. As features de velocidade absoluta estão fora do modelo justamente por isso.

## Modelo de fraude e decisão por custo (12/nov/2026)

- **A fraude humana é simulada.** Os fraudadores usam trajetórias de mouse reais, mas os roteiros (abuso de cupom, teste de cartão, conta roubada) são meus. Nenhum fraudador real participou.
- **Os limiares dependem de uma suposição**: que 2% do tráfego real seja fraude (`prevalencia_fraude` no `policy.yaml`). Com outra taxa, os limiares mudam. Os custos também são escolhas de política, não medidas.
- **`valor` é o sinal mais usado (39% do ganho).** Testadores de cartão compram o item de R$ 4,90 e abusadores de cupom compram o que o cupom cobre. Um fraudador que compre itens de valor comum perde esse sinal.
- **Conta roubada é o cenário mais difícil (62% sinalizado)**: o login de um cliente que trocou de aparelho ou viajou se parece com o de quem roubou a senha. É exatamente o caso em que o desafio é a confirmação fora da sessão, e não a negação.
- **Fricção para pessoas honestas.** Com esses custos, cerca de 7% dos eventos de pessoas honestas simuladas recebem um pedido de confirmação (nenhum é negado), a maioria em logins. É o preço que a política aceita para pegar a fraude humana.
- **Pessoas reais no modelo de fraude**: 3,5% de 200 eventos seriam desafiados, mas três quartos dessas pessoas estavam no treino, então o número é otimista.
