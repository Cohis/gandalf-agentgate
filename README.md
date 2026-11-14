# Gandalf — AgentGate

**Anti-agent abuse system.** Motor de risco em tempo real que detecta e barra ataques e fraudes feitos por agentes de IA (e por automação em geral) contra uma loja online, sem barrar as pessoas nem os agentes que agem com autorização.

> "You shall not pass." — mas só para quem não deveria passar.

[English version](README.en.md)

A pergunta do sistema não é "humano ou robô?". Agentes legítimos existem: pessoas mandam um assistente comprar por elas. A pergunta é:

**Quem está agindo, em nome de quem, e com qual autorização?**

| Ator | O que o motor faz |
|---|---|
| Humano | Análise de fraude; na dúvida, confirmação fora da sessão |
| Agente declarado (assinatura Web Bot Auth + mandato do titular) | Aprova dentro do mandato; fora dele, pergunta ao titular |
| Automação disfarçada (agente ou script se passando por gente) | Desafia com passkey ou nega |

O desafio é **passkey no aparelho do titular**, não captcha: modelos multimodais resolvem captcha, mas o agente não controla o autenticador do dono da conta.

## Resultado final

Coleta de 13/nov/2026 com todas as camadas no ar. Os modelos foram refeitos do zero, e a avaliação usa o período mais recente de cada lote e de cada tipo de ator. Decisão como o motor toma: desafio ou negação conta como sinalizado.

| Grupo | Sessões | Resultado | IC 95 % |
|---|---|---|---|
| Nível 1: script HTTP e Playwright padrão | 6 | 100 % pegos | 100 a 100 % |
| Nível 2: navegador furtivo | 6 | 85 % pegos | 50 a 100 % |
| Nível 3: agente de IA (Qwen3.5-9B local, navegador real) | 9 | 100 % pegos | 100 a 100 % |
| Nível 4: fazenda de contas com identidades geradas por LLM e mouse humano | 28 | 88 % pegos | 77 a 96 % |
| Fraude humana (conta roubada, cupom, teste de cartão) | 7 | 83 % pegos | 47 a 98 % |
| Pessoas simuladas, incluindo leitor de tela, voz e celular | 55 | **2,9 % barradas** | 0 a 6,4 % |
| … com deficiência | 10 | 0 % barradas | 0 a 0 % |
| **Pessoas reais (11 voluntários), sem nenhuma delas no treino** | 47 | **0 % barradas** | 0 a 0 % |
| Nível 5: agente legítimo, passos dentro do mandato | 40 | 100 % aprovados | 100 a 100 % |
| Nível 5: agente legítimo, passos fora do mandato | 13 | 100 % levados ao titular | 100 a 100 % |

Bloquear só por IP pega 19 % da fazenda e 2 % da fraude humana, e barra 29 % das pessoas no celular (CGNAT). Uma fazenda nunca vista é pega em 79 a 90 % das contas, mas só porque o sistema viu outra; sem nenhuma fazenda no treino, cai para 10 a 16 %.

Leia isto antes de citar a tabela:

- **Os grupos de ataque no teste são pequenos** (6 a 28 sessões) e os intervalos são largos.
- **As pessoas simuladas usam trajetórias de mouse reais, mas o ritmo e a navegação são modelados.** As reais são 11, conhecidas de uma só rede de contatos, e validam só o modelo de automação.
- **Todos os atacantes foram escritos por mim.** Um atacante que eu não imaginei pode não se parecer com nenhum deles.

Relatório completo, com a ablação: [docs/results/fase7-final.md](docs/results/fase7-final.md). O que cada número mede e não mede: [docs/limitations.md](docs/limitations.md).

## O que o projeto pegou de errado em si mesmo

Este projeto vale mais pelos erros que encontrou do que pela tabela. Em todos eles o modelo parecia ótimo e estava aprendendo outra coisa:

1. **Um defeito de instrumentação** (fase 2). 87 % do ganho vinha de duas features de ritmo porque clicar num campo contava como duas ações, só para quem usa mouse. O modelo sinalizava usuários de leitor de tela. A telemetria foi corrigida e a coleta, refeita.
2. **Os parâmetros do simulador** (fase 2). Meus atacantes eram simplesmente mais rápidos que minhas pessoas. As cinco features de velocidade absoluta saíram do modelo.
3. **O celular** (fase 3). Treinado só com pessoas simuladas, o modelo barrava 10 % dos voluntários reais, todos no celular: no toque não há cursor. A solução foi telemetria de toque e pessoas reais no treino.
4. **A regularidade do ritmo** (fases 4 e 7). As pessoas simuladas são mais regulares que as reais mesmo depois de calibradas, e o modelo aprendia "irregular = fazenda". Sem essas features, a fazenda continua sendo pega, e as pessoas reais nunca vistas barradas caem de 3,5 % para 0 %.
5. **As gravações no modelo de fraude** (fase 7). Os voluntários vieram por um túnel e cumpriram uma lista de tarefas em minutos. Para o modelo de fraude, isso ensinava que "rede desconhecida e muitos eventos por aparelho" é cliente bom, que é o perfil de um robô. Fora desse modelo, a fraude humana detectada subiu de 74 % para 83 %.
6. **O agente legítimo parecia credential stuffing** (fase 7). Logar a partir do datacenter do operador é, feature por feature, o que um robô de senhas roubadas faz. A política mudou: um agente verificado é julgado pelo mandato, não pelos modelos.

## Estado do projeto

| Fase | Entrega | Estado |
|---|---|---|
| 1 | Contratos, motor com regras, loja, telemetria, painel, compose | pronta |
| 2 | Atacantes 1 e 2, simulador, primeiro LightGBM, explicações | pronta ([resultado](docs/results/fase2.md)) |
| 3 | Agente de IA real (LLM local), pessoas reais gravadas, modelo de fraude | pronta ([agente](docs/results/fase3-nivel3-agente.md), [pessoas](docs/results/fase3-toque-e-pessoas-reais.md), [fraude](docs/results/fase3-fraude.md)) |
| 4 | Camada de escala: semelhança entre cadastros, fazenda de contas | pronta ([resultado](docs/results/fase4-fazenda.md)) |
| 5 | Agente legítimo assinado (Web Bot Auth + mandato) e passkey | pronta |
| 6 | Rede e reputação de IP (tabelas locais, fontes públicas) | pronta |
| 7 | Coleta e avaliação final, ablação, carga | pronta ([resultado](docs/results/fase7-final.md)) |
| 8 | Módulo B: IEEE-CIS Fraud Detection | não iniciada (depende de uma conta Kaggle) |

Modelos no ar: automação `20261113-2328-0444b2` e fraude `fr-20261113-2328-3b507d`.

## Desempenho

Notebook i5-12450HX, 8 GB de RAM, tudo em Docker na mesma máquina, incluindo o gerador de carga; motor com 1 processo, os dois modelos no caminho.

| Cenário | p50 | p99 |
|---|---|---|
| 200 req/s constantes, medido pelo motor | 1,9 ms | 4,3 ms |
| 200 req/s constantes, ponta a ponta | 3,8 ms | 9,7 ms |
| Rajada: 200 requisições no mesmo instante, a cada segundo | 370 ms | 490 ms |

Nenhum erro em nenhum cenário. A rajada mede fila, não o motor. O teto de um processo é de cerca de 210 req/s: pedindo 400, a fila cresce. Os modelos são trocados sem reiniciar o motor.

## Rodar

```bash
make up         # chaves de laboratório + docker compose up (motor, lojas, painel)
```

- Loja: <https://localhost:8443> (certificado autoassinado, aceite o aviso)
- Painel: <http://localhost:8501>

```bash
make test       # unidade, contrato e paridade online/offline (sem Docker)
make e2e        # ponta a ponta com o compose no ar: pilha, passkey, retenção
make simulate RUN=coleta   # tráfego rotulado numa loja privada (porta 8444)
make labels RUN=coleta     # entrega o gabarito ao motor
make eval                  # treina os dois modelos e escreve os relatórios, sem ativar
make train                 # treina, ativa e recarrega o motor
uv run python -m eval.final --cohort f4   # a avaliação final do sistema inteiro
scripts/final_campaign.sh  # a coleta final completa (cerca de 2 h 30)
make load       # 200 req/s constantes por 30 s
make load-burst # pior caso: 200 clientes disparando no mesmo instante
make retencao   # LGPD: descarte após 30 dias (rodar diariamente)
```

O atacante de nível 3 usa um LLM local (llama.cpp com Qwen3.5-9B na GPU); a campanha final sobe e derruba o servidor sozinha.

## Como funciona

```
visitante ──HTTPS──▶ proxy (JA4) ──▶ loja ──POST /avaliar──▶ motor ──▶ aprovar / desafiar / negar
agente ─── assinado (Web Bot Auth) ──┘                         │
                                    Redis (janelas, anti-replay)┤
                                  PostgreSQL (eventos, vetores) ┤
            operador do agente (chaves públicas, lista permitida)┘
```

Quem chama o motor é o **backend da loja**, nunca o navegador. Para cada cadastro, login, item no carrinho ou pagamento:

1. valida a entrada (Pydantic; campos desconhecidos são rejeitados);
2. enriquece o IP com tabelas locais (nenhuma chamada externa por requisição);
3. lê as janelas deslizantes no Redis (10 min, 1 h, 24 h) em uma única ida;
4. calcula as features, incluindo a semelhança do cadastro com os das últimas 6 h;
5. aplica as regras duras (casos óbvios);
6. pontua com dois modelos: automação e fraude;
7. verifica a assinatura do agente e o mandato, se houver;
8. decide, explica em frases, grava e responde.

### Duas perguntas, cruzadas

- **Isto é automação não declarada?** O modelo de comportamento (ponteiro, toque, digitação, forma da sessão, ambiente) tem limiar em 1 % de falso positivo. Quando a resposta é sim, o desafio é passkey.
- **Isto é fraude?** O modelo de transação (velocidade, valor, escala, rede) nunca vê mouse nem ritmo. Os limiares são escolhidos por custo, com um teto de 5 % de clientes honestos desafiados. Quando a resposta é sim, o desafio é confirmação fora da sessão.

A resposta mais severa vence. Um **agente verificado** não passa por nenhum dos dois: a assinatura prova qual operador age, e o mandato (um JWS assinado pela loja depois que o titular confirmou) diz em nome de quem, com qual limite por compra, qual limite total e quais ações. Dentro dele, o agente passa; fora, o titular é consultado.

### O mesmo código de features no online e no offline

- **Uma biblioteca só** (`packages/features`): `compute(evento, contagens, rede)` é uma função pura. O motor e o replay offline chamam a mesma função.
- **O motor grava o vetor que usou** (`feature_snapshots`), e o treino lê esse vetor. A feature foi calculada antes de o rótulo existir, então não há vazamento temporal.
- **Um teste prova isso**: `tests/parity` refaz 300 eventos offline e exige vetores idênticos aos gravados.

### Defesa em camadas

Nenhum sinal isolado decide. A ablação da fase 7 mostra quanto cada camada pesa.

1. **Ambiente**: `navigator.webdriver`, inconsistências de fingerprint, JA4.
2. **Ponteiro e toque**: trajetória do cursor, cliques no centro exato, toque, foco por teclado (o leitor de tela navega por Tab e não pode parecer script).
3. **Sessão**: o agente vai direto ao objetivo; a pessoa se distrai.
4. **Velocidade e escala**: contas por aparelho, por IP, por cartão, e cadastros escritos do mesmo jeito (n-gramas com hash, sem guardar o texto).
5. **Armadilhas**: campo invisível e texto canário. São **sinais para o modelo, nunca bloqueio**, porque leitores de tela também leem conteúdo escondido; o carregador de regras recusa regras que as usem.
6. **Rede e reputação**: ASN, Spamhaus DROP, FireHOL, saídas Tor e o histórico da própria loja por /24. É só mais uma feature: no Brasil, o CGNAT coloca milhares de pessoas no mesmo IPv4.
7. **Identidade**: Web Bot Auth (RFC 9421) e mandato.
8. **Desafio**: passkey (WebAuthn) ou confirmação fora da sessão.

## Segurança e privacidade

- **Ataques só contra a loja de laboratório.** Os geradores de tráfego recusam qualquer alvo que não seja local. Não aponte nada daqui para sistemas de terceiros.
- **IP confiável é só o que o proxy viu.** O cliente pode mandar o `X-Forwarded-For` que quiser; a loja lê apenas a entrada que o proxy acrescentou.
- **Diretórios de agentes só de uma lista permitida.** O motor nunca busca chaves numa URL que a requisição indica, porque isso seria um SSRF.
- **LGPD** ([docs/lgpd.md](docs/lgpd.md)): o IP cru vive só no Redis por até 24 h; no banco ficam um hash com chave e o prefixo. O número do cartão nunca chega ao motor. O texto do cadastro não é armazenado. O navegador envia só agregados. Eventos reais são descartados após 30 dias, e um voluntário pode ser apagado pelo seu código.
- **Os motivos da decisão não são mostrados ao visitante**: ensinariam o atacante a se ajustar.

Modelo de ameaças: [docs/threat-model.md](docs/threat-model.md).

## Estrutura

```
packages/contracts     contratos da API (Pydantic) e chaves, fonte única
packages/features      a biblioteca de features, pura
services/engine        o motor (FastAPI): pipeline, regras, política, identidade, persistência
services/shop          a loja de laboratório e o script de telemetria (TypeScript)
services/agent_operator  operador de agentes do laboratório (diretório de chaves)
services/dashboard     painel (Streamlit)
simulator              pessoas e fraudadores simulados, num navegador real
attackers              níveis 1 a 5
jobs                   treino, reputação de IP, retenção
eval                   avaliação final do sistema
migrations             esquema do PostgreSQL
tests                  contrato, unidade, paridade, ponta a ponta
loadtest               carga em malha aberta e Locust
```

O código é em inglês; os campos da API são em português. Créditos das bases e bibliotecas: [docs/credits.md](docs/credits.md).
