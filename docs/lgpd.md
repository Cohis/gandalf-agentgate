# Dados pessoais (LGPD)

IP e telemetria de comportamento são dados pessoais. O projeto foi desenhado para guardar o mínimo.

## O que entra e o que fica

| Dado | Onde aparece | O que é guardado | Por quanto tempo |
|---|---|---|---|
| IP | Pedido ao motor | Hash com chave (HMAC) e prefixo /24 (ou /48) no banco; o IP cru só no Redis | Redis: 24 h; banco: retenção da tabela de eventos |
| Telemetria do navegador | Pedido ao motor | Só agregados (intervalos, proporções). Trajetória do mouse e teclas nunca saem da página | Retenção dos eventos |
| Número do cartão | Formulário da loja | Nunca chega ao motor: a loja envia um hash com chave | Não é guardado |
| Texto do cadastro (bio, endereço) | Pedido ao motor | Só um vetor de n-gramas com hash e o formato (sem conteúdo) | Retenção dos eventos |
| E-mail | Pedido ao motor | Só o formato (ex.: `a.a9@gmail.com`) | Não é guardado como texto |
| Sessões gravadas de voluntários | Loja em modo gravação | Os mesmos agregados, com um código de participante escolhido pela pessoa | Até o fim do projeto |

## Finalidade e base

Prevenção a fraude e segurança (LGPD, art. 7º, IX, legítimo interesse, e art. 11, II, g, no caso de dados sensíveis, que o projeto não coleta). No laboratório, as gravações de pessoas reais têm consentimento explícito na página `/gravar`, com texto do que é e do que não é registrado.

## Direitos dos titulares

- Um voluntário pode pedir a exclusão informando o seu código de participante: os eventos com aquele rótulo são apagados do banco.
- Os rótulos de pessoas reais ficam separados (`fonte = gravacao:humano_real`).

## Retenção

O plano prevê partições diárias com descarte após 30 dias. **No estado atual do laboratório essa retenção não está implementada**: os dados ficam até serem apagados manualmente. Está registrado como pendência.

## Decisões automatizadas (art. 20)

O motor nunca nega com base em um único sinal, e a decisão mais comum em caso de dúvida é pedir confirmação (passkey ou e-mail), não negar. Os motivos de cada decisão ficam registrados em linguagem clara, mas não são mostrados ao visitante, para não ensinar o atacante. Um pedido de revisão humana usaria esses motivos.
