import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SCRIPT = HERE / "tools" / "integrate_ke24_business_checks.py"
spec = importlib.util.spec_from_file_location("ke24_installer", SCRIPT)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def _example_source():
    run = '''from types import SimpleNamespace\nfrom pathlib import Path\n\ndef _live_adapters():\n    from src.ml.load_ke24_alert_rules import load_protected_alert_rules\n    return SimpleNamespace(\n        load_rules=load_protected_alert_rules,\n    )\n\ndef execute_readonly_stages(adapters, processed, secret_key):\n    with _timed_stage("Feature engineering em memoria"):\n        pass\n    result = {}\n    if classification_summary is not None:\n        result.update(classification_summary)\n    return result\n\ndef run_last_status():\n    for key, value in status.get("metrics", {}).items():\n        if key in {\n                   "priority_low"} and type(value) is int:\n            pass\n'''
    guard = '''SAFE_METRICS = (\n    "priority_low",\n)\n'''
    # Teste do motor de atualizacao, sem executar o programa sintetico.
    return run, guard


def test_installer_is_idempotent_and_preserves_original_anchors(tmp_path):
    run, guard = _example_source()
    rp = tmp_path / installer.RUN
    gp = tmp_path / installer.GUARD
    rp.parent.mkdir(parents=True, exist_ok=True)
    gp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(run, encoding="utf-8")
    gp.write_text(guard, encoding="utf-8")
    assert len(installer.apply(tmp_path)) == 2
    assert installer.apply(tmp_path) == []
    assert "run_business_checks=run_readonly_business_checks" in rp.read_text(encoding="utf-8")
    assert '"business_pending_company_currency"' in gp.read_text(encoding="utf-8")


def test_installer_refuses_mismatch_without_writing(tmp_path):
    run, guard = _example_source()
    run = run.replace("load_rules=load_protected_alert_rules,", "load_rules=None,")
    rp = tmp_path / installer.RUN
    gp = tmp_path / installer.GUARD
    rp.parent.mkdir(parents=True, exist_ok=True)
    gp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(run, encoding="utf-8")
    gp.write_text(guard, encoding="utf-8")
    with pytest.raises(RuntimeError, match="divergente"):
        installer.apply(tmp_path)
    assert rp.read_text(encoding="utf-8") == run
    assert gp.read_text(encoding="utf-8") == guard
