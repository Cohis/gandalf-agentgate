# Fase 7: coleta e avaliação final (candidatos, não ativados)

Coleta de 13/nov, feature set `f4`, com todas as camadas no ar: motor, loja-sombra, identidade de agente (Web Bot Auth + mandato) e passkey. Os atores rodaram em cinco lotes, um depois do outro (`scripts/final_campaign.sh`, 2 h 33 min):

| Lote | Sessões | Avaliações | Falhas |
|---|---|---|---|
| Base: pessoas, fraude humana, níveis 1 e 2 | 245 | 835 | 0 |
| Fazenda A (nível 4) no meio de pessoas | 110 | 311 | 0 |
| Agente legítimo (nível 5), 25 % fora do mandato | 40 | 120 | 0 |
| Agente de IA (nível 3, Qwen3.5-9B local) | 40 | 107 | 0 |
| Fazenda B (nível 4) no meio de pessoas | 110 | 296 | 7 |

As 7 falhas da fazenda B são o mesmo caso da fase 4: o formulário de cadastro não apareceu em 30 s e a conta não chegou a existir.

Relatório completo: `eval/out/final-f4.md` (`uv run python -m eval.final --cohort f4`). Os modelos são refeitos do zero com a receita do motor. O split é 60/20/20 no tempo **dentro de cada lote e tipo de ator**: um split único deixaria só o último lote no teste, e toda pessoa honesta do teste seria alguém que chegou durante uma fazenda.

## A escada

| Grupo | Sessões | Eventos | Taxa | IC 95 % |
|---|---|---|---|---|
| Atacante nível 1 (detectado) | 6 | 26 | 100 % | 100 a 100 % |
| Atacante nível 2 (detectado) | 6 | 26 | 85 % | 50 a 100 % |
| Atacante nível 3, agente de IA (detectado) | 9 | 22 | 100 % | 100 a 100 % |
| Atacante nível 4, fazenda (detectado) | 28 | 80 | 88 % | 77 a 96 % |
| Fraude humana, passos fraudulentos (detectado) | 7 | 47 | 83 % | 47 a 98 % |
| Pessoas simuladas, todas (barradas) | 55 | 140 | 2,9 % | 0 a 6,4 % |
| … num dia comum | 37 | 98 | 4,1 % | 0 a 9,1 % |
| … durante uma fazenda | 18 | 42 | 0 % | 0 a 0 % |
| … com deficiência (leitor de tela, voz) | 10 | 28 | 0 % | 0 a 0 % |
| … no celular (CGNAT) | 18 | 48 | 6,2 % | 0 a 15,7 % |
| Agente legítimo, passos dentro do mandato (aprovado) | 40 | 94 | 100 % | 100 a 100 % |
| Agente legítimo, passos fora do mandato (titular consultado) | 13 | 26 | 100 % | 100 a 100 % |
| Pessoas reais, uma de fora por vez (barradas) | 47 | 200 | 0 % | 0 a 0 % |
| Pessoas reais, sem nenhuma pessoa real no treino (barradas) | 47 | 200 | 0 % | 0 a 0 % |

Os grupos de ataque no período de teste são pequenos (6 a 28 sessões) e os intervalos são largos. Os números que mais pesam são a fazenda, as pessoas simuladas e as pessoas reais.

### Fazenda nunca vista

O sistema inteiro, refeito sem nenhuma conta de uma fazenda e aplicado a todas as contas dela:

| Fazenda nunca vista | Contas | Pegas | Das 20 primeiras | Primeira pega |
|---|---|---|---|---|
| A | 70 | 90 % | 75 % | 1 |
| B | 70 | 79 % | 80 % | 3 |

Na fase 4 eram 33 % e 84 %. Mas este número depende de o sistema ter visto **outra** fazenda: sem nenhuma conta de nível 4 no treino, a detecção cai para 16 % (A) e 10 % (B). Uma fazenda é um tipo de ataque que o sistema precisa ter visto ao menos uma vez.

### Bloquear só por IP

Um modelo só com as 15 features de IP pega 19 % da fazenda e 2 % da fraude humana, e barra 10 % das pessoas simuladas (29 % das que estão no celular, atrás de CGNAT). O motor completo pega 88 % e 83 %, e barra 2,9 % (6 % no celular).

## Quatro correções que a coleta final forçou

**1. O agente legítimo era desafiado no login.** O modelo de fraude no ar desafiava 100 % dos logins do agente dentro do mandato, sempre com o mesmo score (0,797). Para ele, o agente do titular entrando do datacenter do operador é, feature por feature, um robô de credential stuffing: rede de datacenter, evento de login, velocidades zeradas. O que distingue os dois é a assinatura verificada. Por isso a política mudou: **um agente verificado é julgado pelo mandato**, com limite por compra, limite total e ações permitidas. Os modelos não opinam sobre ele, e as regras de negação continuam valendo (`policy.py`, `Policy.declared`). Pelo mesmo motivo, o nível 5 saiu do treino. Como negativo de fraude, ele ensinaria que "login de datacenter é normal".

**2. O ritmo relativo era artefato do simulador.** Mesmo depois da calibração, as pessoas simuladas são mais regulares que os voluntários (`ritmo_cv` mediana 0,55 contra 0,73), e a fazenda é mais irregular que os dois (1,01). O modelo aprendia que irregular é fazenda. Sem `ritmo_cv`, `ritmo_p90_p50` e `ritmo_n`:

| | Com ritmo | Sem ritmo |
|---|---|---|
| Fazenda nunca vista, modelo de automação (A / B) | 71 / 90 % | 76 / 73 % |
| Pessoas simuladas barradas pelo modelo de automação | 5,7 % | 0 % |
| Pessoas reais barradas, sem nenhuma real no treino | 3,5 % | 0 % |

As features continuam sendo calculadas e registradas. Voltam ao modelo quando houver sessões reais suficientes.

**3. As gravações não servem para o modelo de fraude.** Treinado só com dados simulados, o modelo de fraude barrava 36 % dos eventos das pessoas reais. O comportamento delas é real, mas o contexto que esse modelo lê vem do arranjo da gravação. Todos vieram pelo túnel (rede desconhecida, sem ASN) e cumpriram uma lista de tarefas em poucos minutos (cerca de 13 eventos por aparelho em 10 min). Como exemplos honestos com peso 3, elas ensinavam que esse é o perfil de um bom cliente, e ele também é o de um robô. Agora as gravações só entram no modelo de automação, e as linhas de pessoas reais medem só esse modelo. Com a mudança, a fraude humana detectada subiu de 74 % para 83 %.

**4. Atrito: teto de 5 % para o modelo de fraude.** Com limiares escolhidos só por custo, o modelo de fraude desafiava 14 % das pessoas simuladas (21 % das que têm deficiência), sobretudo o cliente novo comprando o item mais barato. A política agora tem `atrito_maximo: 0.05`: o limiar de desafio nunca passa de 5 % dos eventos honestos na validação. No mesmo modelo, esse teto levou o atrito de 14 % para 7 % e a fraude humana de 85 % para 74 %, sem mudar a detecção da fazenda nem a dos robôs. A correção 3 depois recuperou a fraude humana.

## Ablação

| Sem a camada | Detecção média | Pessoas barradas |
|---|---|---|
| (sistema completo) | 91 % | 2,9 % |
| Ambiente (navegador, user-agent, JA4) | 90 % | 2,9 % |
| Ritmo e ponteiro (só ponteiro, digitação e toque entram no modelo) | 88 % | 2,9 % |
| Forma da sessão | 88 % | 2,9 % |
| Velocidade e escala | 83 % | 5,7 % |
| Armadilhas (honeypot, canário) | 91 % | 2,9 % |
| Rede e reputação de IP | 85 % | 1,4 % |

Nenhuma camada sozinha decide. As que mais pesam são velocidade e escala (fazenda, fraude) e rede.

## Candidatos

Treinados com todas as campanhas (`make eval`, de f1 a f4, 7.951 eventos), com o mesmo split por lote e tipo de ator. **Não ativados.**

- Automação `20261113-2328-0444b2`: no teste, níveis 1, 2 e 3 a 100 % e fazenda a 84,5 %, barrando 1,1 % das pessoas simuladas. Pessoas reais, uma de fora por vez: 0 de 200. Principais features: `cliques_centro`, `valor`, `cursor_linear`, `ev_disp_24h`.
- Fraude `fr-20261113-2328-3b507d`: desafia a partir de 0,7251 e nega a partir de 0,9786. Desafia 3,6 % das pessoas honestas simuladas e pega 100 % do cupom multicontas, 99 % do teste de cartão manual e 36 % da conta roubada. Principais features: `valor`, `ev_disp_24h`, `cadastros_ip_1h`.

Custo por mil eventos no teste, com o modelo de automação que está no ar: aprovar tudo 400, só automação 250, só fraude 179, os dois cruzados 125 (na fase 4 eram 166).

## O que continua limitado

- **Conta roubada**: o modelo de fraude pega 36 %. Quem tem a senha de alguém e se comporta como uma pessoa é o caso mais difícil, e a defesa é o desafio fora da sessão no checkout, não o modelo.
- **Fazenda de um tipo nunca visto**: 10 a 16 %.
- **Ritmo das pessoas simuladas**: ainda mais regular que o dos voluntários. Por isso o ritmo relativo ficou fora do modelo.
- **Pessoas reais**: são 11, todas por um túnel, e medem só o modelo de automação.
