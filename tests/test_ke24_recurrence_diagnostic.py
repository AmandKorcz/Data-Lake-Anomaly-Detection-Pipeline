import pandas as pd
from tools.diagnose_ke24_recurrence import summarize


def row(alert_id, src, company="TKN_COMPANY", doc="TKN_DOC", period="2026-03", amount=10):
    return dict(
        alert_id=alert_id, _source_file_sha256=src,
        company_code=company, reference_document=doc, period_key=period,
        profit_center="TKN_CENTER", product="TKN_PRODUCT", currency="BRL",
        currency_type=10, dominant_measure="revenue", signal_method="training_p95",
        total_absolute_value=amount, dominant_measure_value=amount,
    )


def test_reexport_with_new_hash_still_matches_full_business_key():
    prior = pd.DataFrame([row("old", "sourceA")])
    current = pd.DataFrame([row("new", "sourceB")])
    s = summarize(prior, current)
    assert s["alert_ids_em_comum"] == 0
    assert s["arquivos_origem_em_comum"] == 0
    assert s["etapas"][-1]["candidatos_em_correspondencia_1_para_1"] == 1


def test_changed_amount_stops_matching_at_final_step():
    prior = pd.DataFrame([row("old", "sourceA")])
    current = pd.DataFrame([row("new", "sourceB", amount=11)])
    s = summarize(prior, current)
    assert s["etapas"][-2]["candidatos_com_alguma_correspondencia"] == 1
    assert s["etapas"][-1]["candidatos_com_alguma_correspondencia"] == 0


def test_different_document_stops_matching_early():
    prior = pd.DataFrame([row("old", "sourceA", doc="TKN_DOC_A")])
    current = pd.DataFrame([row("new", "sourceB", doc="TKN_DOC_B")])
    s = summarize(prior, current)
    assert s["etapas"][0]["candidatos_com_alguma_correspondencia"] == 1
    assert s["etapas"][1]["candidatos_com_alguma_correspondencia"] == 0
