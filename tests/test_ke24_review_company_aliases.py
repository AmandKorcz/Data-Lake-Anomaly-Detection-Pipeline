import json

import pytest

from src.privacy.anonymize_ke24 import generate_token
from src.review import ke24_review_company_aliases as aliases

KEY = b'x' * 32


def write(path, companies):
    path.write_text(json.dumps({'schema_version': 1, 'companies': companies}), encoding='utf-8')


def test_private_codes_are_tokenized_using_project_hmac(tmp_path):
    path = tmp_path / 'companies.json'
    write(path, [{'company_code': '0001', 'company_label': 'Empresa Alfa'},
                 {'company_code': 'RU01', 'company_label': 'Empresa Beta'}])
    result = aliases.load_private_company_aliases(KEY, path=path)
    assert result[generate_token('0001', 'company_code', '0001', KEY)] == 'Empresa Alfa'
    assert result[generate_token('RU01', 'company_code', 'RU01', KEY)] == 'Empresa Beta'
    assert '0001' not in str(result) and 'RU01' not in str(result)


def test_init_template_never_overwrites(tmp_path):
    path = tmp_path / 'aliases.json'
    assert aliases.create_empty_alias_file(path=path) == path
    assert json.loads(path.read_text()) == {'schema_version': 1, 'companies': []}
    with pytest.raises(FileExistsError):
        aliases.create_empty_alias_file(path=path)


def test_empty_template_blocks_preview(tmp_path):
    path = tmp_path / 'aliases.json'
    aliases.create_empty_alias_file(path=path)
    with pytest.raises(ValueError, match='sem empresas'):
        aliases.load_private_company_aliases(KEY, path=path)


def test_missing_private_config_fails(tmp_path):
    with pytest.raises(ValueError, match='ausente'):
        aliases.load_private_company_aliases(KEY, path=tmp_path / 'none.json')


@pytest.mark.parametrize('company', [
    {'company_code': '=IMPORTDATA("x")', 'company_label': 'Empresa A'},
    {'company_code': 'X', 'company_label': '=HYPERLINK("x")'},
    {'company_code': 'X', 'company_label': 'X'},
    {'company_code': 'X Y', 'company_label': 'Empresa A'},
])
def test_invalid_private_fields_are_blocked(tmp_path, company):
    path = tmp_path / 'aliases.json'
    write(path, [company])
    with pytest.raises(ValueError):
        aliases.load_private_company_aliases(KEY, path=path)


def test_duplicate_company_and_alias_are_blocked(tmp_path):
    path = tmp_path / 'aliases.json'
    write(path, [{'company_code':'A','company_label':'Empresa A'},
                 {'company_code':'A','company_label':'Empresa B'}])
    with pytest.raises(ValueError, match='duplicado'):
        aliases.load_private_company_aliases(KEY, path=path)
    write(path, [{'company_code':'A','company_label':'Empresa A'},
                 {'company_code':'B','company_label':'empresa a'}])
    with pytest.raises(ValueError, match='duplicado'):
        aliases.load_private_company_aliases(KEY, path=path)


def test_never_save_private_map_within_repo():
    with pytest.raises(ValueError, match='fora'):
        aliases.create_empty_alias_file(path=aliases.ROOT/'config'/'sensitive.json')


def test_invalid_private_key_blocked(tmp_path):
    path = tmp_path / 'aliases.json'
    write(path, [{'company_code':'A','company_label':'Empresa A'}])
    with pytest.raises(ValueError, match='Chave'):
        aliases.load_private_company_aliases(b'short', path=path)
