# Modelo de ameaça

## O que se protege

Uma loja online: o cadastro (o cupom de primeira compra é dinheiro), o login (contas têm cartão e endereço), o carrinho (estoque limitado) e o pagamento (cartões de terceiros). O motor decide, a cada uma dessas ações, se aprova, pede confirmação ou nega.

## Quem ataca, e como (a escada)

| Nível | Quem | O que faz | O que esconde | O que o entrega |
|---|---|---|---|---|
| 1 | Script simples | Cliente HTTP ou Playwright padrão | Nada | Sem telemetria, `webdriver`, user-agent de biblioteca |
| 2 | Script furtivo | Navegador mascarado | O ambiente | Clica no centro exato, não move o ponteiro, vai direto ao objetivo |
| 3 | Agente de IA | LLM local lendo a página e decidindo cada ação | O roteiro (cada sessão é diferente) | Age como as bibliotecas de agente: clique no centro, campo preenchido por código, ritmo de inferência |
| 4 | Fazenda de contas | Cada conta imita uma pessoa (ponteiro gravado, digitação humana), IP residencial, aparelho novo, identidade escrita por LLM | A sessão individual | A escala: dezenas de cadastros em poucas horas, no mesmo formato |
| Fraude humana | Pessoa de verdade | Cupom com várias contas, teste de cartão à mão, conta roubada | O comportamento (é humano) | O que faz: contas por aparelho, cartões tentados, valor |
| 5 | Agente legítimo | Compra em nome do titular, assinado e com mandato | Nada: se declara | Precisa passar |

## Defesa em camadas

1. **Ambiente**: telemetria ausente, `navigator.webdriver`, inconsistências de fingerprint, JA4.
2. **Ritmo e ponteiro**: forma dos intervalos (sem velocidade absoluta), linearidade do cursor, cliques no centro, digitação, toque, foco por Tab.
3. **Sessão**: desvios do caminho, páginas vistas.
4. **Escala**: janelas de velocidade por dispositivo, conta, cartão e IP; semelhança e formato dos cadastros nas últimas 6 horas.
5. **Armadilhas**: campo invisível e texto canário. Só sinais, nunca bloqueio: leitores de tela também leem conteúdo escondido.
6. **Desafio**: passkey no aparelho do titular, ou confirmação fora da sessão. Captcha não, porque modelos multimodais resolvem.

Por cima, a **identidade**: um agente que se declara (Web Bot Auth) e traz mandato do titular é julgado pelo que o titular autorizou, não por ser um robô.

## O que o atacante controla

- Tudo o que roda no navegador dele, inclusive a telemetria: ela pode ser forjada por quem ler o script. Por isso as armadilhas são verificadas no servidor e nenhum sinal decide sozinho.
- O IP (proxy residencial é barato) e o aparelho (um contexto de navegador novo por conta).
- O conteúdo do cadastro, se usar um LLM com instruções diferentes.

## O que o atacante não controla

- O aparelho do titular (passkey) e o e-mail do titular (confirmação fora da sessão).
- A chave privada de um operador de agente confiável.
- O fato de precisar de muitas contas para o golpe valer a pena.

## A corrida armamentista

Cada camada tem um contorno conhecido, e o projeto mostra alguns na prática:

- O nível 4 contorna a sessão individual (passa no modelo de automação) e é pego pela escala.
- Um atacante que sorteie a posição do clique, mova o ponteiro de verdade e varie o formato dos cadastros a cada conta escaparia das duas camadas. O próximo passo seria correlacionar entre dispositivos (grafo), e assim por diante.

O objetivo não é ser impossível de contornar, e sim tornar o ataque caro o bastante para não compensar, sem custo para as pessoas.

## Fora do escopo

- Ataques à infraestrutura (DDoS, exploração do servidor).
- Prompt injection contra um chatbot de atendimento (extensão futura prevista no plano).
- Fraude amigável (o titular contesta uma compra que fez).
