-- Gandalf schema, phase 1.
-- Privacy: the raw IP is never stored here (keyed hash + truncated prefix only),
-- and sign-up free text is never stored (only its embedding, from phase 4).

CREATE TABLE IF NOT EXISTS events (
    avaliacao_id   text PRIMARY KEY,
    id_requisicao  text NOT NULL UNIQUE,
    recebido_em    timestamptz NOT NULL DEFAULT now(),
    momento        timestamptz NOT NULL,
    evento         text NOT NULL,
    conta_id       text,
    dispositivo_id text NOT NULL,
    sessao_id      text NOT NULL,
    ip_hmac        text NOT NULL,
    ip_prefixo     text NOT NULL,
    asn            integer NOT NULL DEFAULT 0,
    pais           text NOT NULL DEFAULT '',
    tipo_rede      text NOT NULL DEFAULT 'desconhecida',
    ja4            text,
    http2_fp       text,
    ua_familia     text NOT NULL DEFAULT '',
    cartao_hash    text,
    valor          double precision,
    telemetria     jsonb NOT NULL,
    sessao         jsonb NOT NULL,
    ator_declarado boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS events_momento_idx ON events (momento);
CREATE INDEX IF NOT EXISTS events_conta_idx ON events (conta_id, momento);
CREATE INDEX IF NOT EXISTS events_disp_idx ON events (dispositivo_id, momento);

CREATE TABLE IF NOT EXISTS decisions (
    avaliacao_id     text PRIMARY KEY REFERENCES events ON DELETE CASCADE,
    decisao          text NOT NULL,
    acao             text NOT NULL,
    ator_tipo        text NOT NULL,
    ator_confianca   double precision NOT NULL,
    score_automacao  double precision NOT NULL,
    score_fraude     double precision,
    motivos          jsonb NOT NULL,
    regras_acionadas jsonb NOT NULL,
    desafio_id       text,
    versao_modelo    text NOT NULL,
    versao_features  text NOT NULL,
    versao_politica  text NOT NULL,
    modo_degradado   boolean NOT NULL DEFAULT false,
    latencia_ms      double precision NOT NULL,
    estagios_ms      jsonb NOT NULL
);

-- The exact vector used for the decision. Training reads this table, so the
-- features a model learns from are the ones serving computed (no train/serve skew,
-- and point-in-time correct by construction).
CREATE TABLE IF NOT EXISTS feature_snapshots (
    avaliacao_id    text PRIMARY KEY REFERENCES events ON DELETE CASCADE,
    versao_features text NOT NULL,
    features        jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS labels (
    avaliacao_id   text PRIMARY KEY,
    eh_automacao   boolean NOT NULL,
    eh_fraude      boolean NOT NULL,
    ator_real      text NOT NULL,
    nivel_atacante smallint NOT NULL DEFAULT 0,
    perfil         text NOT NULL DEFAULT '',
    fonte          text NOT NULL DEFAULT 'simulador',
    chegou_em      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS challenges (
    desafio_id   text PRIMARY KEY,
    avaliacao_id text NOT NULL,
    tipo         text NOT NULL,
    estado       text NOT NULL DEFAULT 'pendente',  -- pendente | aprovado | falhou | expirado
    criado_em    timestamptz NOT NULL DEFAULT now(),
    resolvido_em timestamptz
);

CREATE TABLE IF NOT EXISTS model_registry (
    versao    text PRIMARY KEY,
    alvo      text NOT NULL,           -- automacao | fraude
    caminho   text NOT NULL,
    versao_features text NOT NULL,
    metricas  jsonb NOT NULL DEFAULT '{}',
    limiares  jsonb NOT NULL DEFAULT '{}',
    ativo     boolean NOT NULL DEFAULT false,
    criado_em timestamptz NOT NULL DEFAULT now()
);

-- Simulator ground truth lives apart; labels reach the engine only via POST /rotulos.
CREATE SCHEMA IF NOT EXISTS lab;
