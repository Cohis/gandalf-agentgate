# Gandalf — AgentGate

**Anti-agent abuse system.** Motor de risco em tempo real que detecta e barra ataques e fraudes feitos por agentes de IA (e por automação em geral) contra uma loja online.

> "You shall not pass." — mas só para quem não deveria passar.

[English version](README.en.md)

A pergunta do sistema não é "humano ou robô?". Agentes legítimos existem: pessoas mandam um assistente comprar por elas. A pergunta é:

**Quem está agindo, em nome de quem, e com qual autorização?**

| Ator | O que o motor faz |
|---|---|
| Humano | Análise normal de fraude |
| Agente declarado (assinado, com mandato do usuário) | Aprova dentro do mandato; fora dele, pede confirmação ao titular |
| Automação disfarçada (agente ou script se passando por humano) | Desafia ou nega |

O desafio é **passkey no aparelho do titular**, não captcha: modelos multimodais já resolvem captcha, mas o agente não controla o autenticador do dono da conta.

## Estado do projeto

Em construção, por fases. Esta tabela diz o que existe de verdade hoje.

| Fase | Entrega | Estado |
|---|---|---|
| 1 | Esqueleto: contratos, motor com regras, loja, telemetria, painel, compose | **pronta e validada** |
| 2 | Atacantes 1 e 2, simulador, primeiro modelo LightGBM, SHAP | a fazer |
| 3 | Agente de IA real (LLM local), sessões humanas gravadas | a fazer |
| 4 | Camada de escala: embeddings, FAISS, HDBSCAN | a fazer |
| 5 | Agente legítimo assinado (Web Bot Auth) e passkey | a fazer |
| 6 | Reputação de IP | a fazer |
| 7 | Tabela final, ablação, teste de carga | a fazer |
| 8 | Módulo B: IEEE-CIS Fraud Detection | a fazer |

Na fase 1 o score vem de uma **heurística escrita à mão** (`heuristica-0`), não de um modelo treinado. Ela existe para o pipeline funcionar de ponta a ponta e para servir de linha de base que o modelo terá de superar. Ainda não há nenhum número de detecção para mostrar.

### Medições da fase 1

Notebook i5-12450HX, 8 GB de RAM, tudo em Docker na mesma máquina, motor com 1 worker.

| Cenário | p50 | p99 |
|---|---|---|
| 200 req/s constantes, medido pelo motor | 0,5 ms | 1,3 ms |
| 200 req/s constantes, ponta a ponta (cliente HTTP) | 2,4 ms | 4,6 ms |
| Rajada: 200 requisições no mesmo instante, a cada segundo (cliente) | 160 ms | 240 ms |

A rajada é o pior caso de um único worker: as 200 requisições entram na fila juntas e o motor leva cerca de 0,9 ms para atender cada uma. Ainda não há modelo nem embeddings no caminho; esses números vão subir nas fases 2 e 4.

## Rodar

```bash
docker compose up -d --build --wait
```

- Loja: <https://localhost:8443> (certificado autoassinado, aceite o aviso)
- Painel: <http://localhost:8501>

Sem Docker, para mexer na loja e nas regras (estado em memória, sem JA4):

```bash
make dev        # loja em http://127.0.0.1:8080
```

Testes (não precisam de Docker):

```bash
make test       # unidade, contrato e paridade online/offline
make e2e        # ponta a ponta, com o compose no ar
make load       # 200 req/s constantes por 30 s no motor
make load-burst # pior caso: 200 clientes disparando no mesmo instante
```

## Como funciona

```
visitante ──HTTPS──▶ proxy (calcula JA4) ──▶ loja ──POST /avaliar──▶ motor
                                                                     │
                                                    Redis (janelas) ─┤
                                                 PostgreSQL (eventos)┘
```

Quem chama o motor é o **backend da loja**, nunca o navegador. Para cada cadastro, login, item no carrinho ou pagamento:

1. valida a entrada (Pydantic, campos desconhecidos são rejeitados);
2. enriquece o IP com dados locais (nenhuma chamada externa por requisição);
3. lê as janelas deslizantes no Redis (10 min, 1 h, 24 h) em uma única ida;
4. calcula as features;
5. aplica as regras duras (casos óbvios);
6. pontua e decide (aprovar, desafiar, negar);
7. explica a decisão em frases;
8. grava as janelas, enfileira a persistência e responde.

### O mesmo código de features no online e no offline

É o erro clássico de sistemas de ML em produção: treinar com uma feature calculada de um jeito e servir com outra. Aqui:

- **Uma biblioteca só** (`packages/features`): `compute(evento, contagens, rede)` é uma função pura. O motor e o replay offline chamam a mesma função; só o armazenamento muda (Redis ou memória), com a mesma semântica.
- **O motor grava o vetor que usou** (`feature_snapshots`). O treino lê esse vetor. A feature foi calculada antes de o rótulo existir, então não há vazamento temporal.
- **Um teste prova isso**: `tests/parity` refaz 300 eventos offline e exige vetores idênticos aos gravados.

### Defesa em camadas

Nenhum sinal isolado decide.

1. **Ambiente**: `navigator.webdriver`, inconsistências de fingerprint, JA4, tipo de rede.
2. **Ritmo**: a forma dos intervalos entre ações. As estatísticas não dependem da escala (um LLM lento e um rápido têm a mesma regularidade).
3. **Sessão**: o agente vai direto ao objetivo; o humano se distrai.
4. **Escala**: quantas contas por dispositivo, por IP, por cartão. (Semelhança entre cadastros na fase 4.)
5. **Armadilhas**: campo invisível e texto canário. São **sinais para o modelo, nunca bloqueio**, porque leitores de tela também leem conteúdo escondido. O carregador de regras recusa qualquer regra que use esses sinais.
6. **Desafio**: passkey (fase 5).

Reputação de IP é só mais uma feature. No Brasil, o CGNAT das operadoras coloca milhares de pessoas no mesmo IPv4; proxy residencial e VPN tornam a troca de IP barata.

## Segurança e privacidade

- **Ataques só contra a loja de laboratório.** Os geradores de tráfego recusam qualquer alvo que não seja local. Não aponte nada daqui para sistemas de terceiros.
- **IP confiável é só o que o proxy viu.** O cliente pode mandar o `X-Forwarded-For` que quiser; o proxy acrescenta ao final o endereço real, e a loja lê apenas essa última entrada.
- **LGPD**: o IP cru vive só no Redis por até 24 h; no banco ficam um hash com chave e o prefixo (/24 ou /48). O número do cartão nunca chega ao motor. O texto do cadastro não é armazenado. O navegador envia apenas agregados, nunca a trajetória do mouse nem as teclas.
- **Os motivos da decisão não são mostrados ao visitante**: ensinariam o atacante a se ajustar.

## Limitações conhecidas

- A loja guarda contas e sessões em memória: é um alvo de laboratório.
- Com o proxy atual, se o cálculo do JA4 falhar, um cabeçalho `X-JA4-Fingerprint` forjado pelo cliente pode passar adiante. O JA4 é tratado como sinal, nunca como prova.
- Em `LAB_MODE`, os geradores de tráfego informam o IP e o relógio simulados. O IP do laboratório é fictício; o JA4 é real.
- A telemetria do navegador pode ser forjada por quem ler o script. A defesa não depende dela sozinha.

## Estrutura

```
packages/contracts   contratos da API (Pydantic), fonte única
packages/features    a biblioteca de features, pura
services/engine      o motor (FastAPI): pipeline, regras, política, persistência
services/shop        a loja de laboratório e o script de telemetria (TypeScript)
services/dashboard   painel (Streamlit)
migrations           esquema do PostgreSQL
tests                contrato, unidade, paridade, e2e
loadtest             Locust
```

O código é em inglês; os campos da API são em português.
