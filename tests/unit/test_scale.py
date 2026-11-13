"""The scale layer: sign-ups written the same way are noticed, ordinary ones are not."""

from __future__ import annotations

from gandalf_features import ScaleWindow, email_shape, embed, text_of

from tests.conftest import KEY, Harness, make_event

FARM = [  # what an LLM writes when asked for a batch of profiles
    ("Apaixonada por culinária saudável e viagens em família.", "Rua das Palmeiras, 245, Centro"),
    ("Apaixonado por tecnologia, música e viagens com amigos.", "Rua dos Ipês, 812, Centro"),
    ("Apaixonada por leitura, yoga e viagens pelo Brasil.", "Rua das Acácias, 377, Centro"),
]
PEOPLE = [("", ""), ("vi no insta", "R. Dom Pedro II 88 ap 3"), ("aposentado", "")]


def test_text_vectors() -> None:
    assert embed("") is None
    a, b = (embed(text_of(*FARM[i])) for i in (0, 1))
    c = embed(text_of(*PEOPLE[1]))
    assert float(a @ b) > 0.3 > float(a @ c)
    assert embed(text_of("", "Rua das Flores, 12")) is None  # an address alone is not compared
    assert email_shape("Ana.Souza1987@Gmail.com") == "a.a9@gmail.com"
    assert email_shape("sem-arroba") == ""


def test_window_counts_similar_signups_and_reads_before_writing() -> None:
    w = ScaleWindow()
    for i, (bio, addr) in enumerate(FARM):
        shape = ("a.a9@gmail.com", "rua a, 9, a", "3M|a.a9@gmail.com|rua a, 9, a|2M.")
        info = w.query(embed(text_of(bio, addr)), shape, 1000.0 + i)
        assert info.semelhantes_6h == i and info.cadastros_6h == i
        assert info.mesmo_formato_email_6h == i == info.mesmo_formato_endereco_6h
        assert info.mesmo_perfil_6h == i
        w.add(embed(text_of(bio, addr)), shape, 1000.0 + i)
    later = w.query(embed(text_of(*FARM[0])), ("", "", ""), 1000.0 + 7 * 3600)
    assert later.cadastros_6h == 0  # out of the 6-hour window
    assert w.query(None, ("", "", ""), 1001.5).sim_max_6h is None  # no text: no similarity


def test_engine_scores_a_farm_burst(harness: Harness) -> None:
    out = []
    for i, (bio, addr) in enumerate(FARM):
        ev = make_event(
            evento="cadastro",
            conta_id=f"c_farm{i}",
            dispositivo_id=f"d_farm{i}",
            ip=f"177.20.30.{i + 1}",
            offset_s=i * 300,
            dados_cadastro={"nome": "x", "email": f"x{i}@gmail.com", "bio": bio, "endereco": addr},
        )
        r = harness.client.post("/avaliar", json=ev, headers={"X-Gandalf-Key": KEY})
        out.append(r.json())
    feats = [rec.features["features"] for rec in harness.storage.records]
    assert [f["escala_semelhantes_6h"] for f in feats] == [0.0, 1.0, 2.0]
    assert [f["escala_mesmo_endereco_6h"] for f in feats] == [0.0, 1.0, 2.0]
    assert feats[2]["escala_sim_max_6h"] > 0.3
    assert all(rec.embedding is not None for rec in harness.storage.records)
    assert "Apaixonad" not in repr(harness.storage.records)  # the text is never kept
    login = harness.client.post("/avaliar", json=make_event(), headers={"X-Gandalf-Key": KEY})
    assert login.status_code == 200
    assert harness.storage.records[-1].features["features"]["escala_cadastros_6h"] is None


def test_profile_signature_keeps_format_not_content() -> None:
    from gandalf_features import profile_signature

    a = profile_signature(
        "Fernanda Costa Silva",
        "fernanda.costa.cafe@gmail.com",
        "Rua das Acácias, 142, Centro",
        "Apaixonada por café de especialidade e artesanato local.",
    )
    b = profile_signature(
        "Lucas Mendes Oliveira",
        "lucas.mendes.ok@gmail.com",
        "Rua do Farol, 55, Copacabana",
        "Fotógrafo urbano que adora câmeras e óculos escuros.",
    )
    c = profile_signature("ana", "ana1996@bol.com.br", "", "vi no insta")
    assert a == b != c
    assert "Fernanda" not in a and "Acácias" not in a
