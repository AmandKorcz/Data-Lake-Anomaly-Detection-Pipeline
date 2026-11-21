import pytest
from tools.install_ke24_recurrence import (
    transform, ANCHOR_FUNC, ANCHOR_CALL, ANCHOR_PRINT, ANCHOR_PREVIEW,
    MARKER,
)


def original_source():
    return ('# coding: utf-8\n\ndef stub():\n'
            + ANCHOR_CALL + '\n' + ANCHOR_PRINT + '\n'
            + ANCHOR_PREVIEW + '\n' + ANCHOR_FUNC + '    pass\n')


def test_installer_preserves_unrelated_local_lines():
    source = '# uma mensagem personalizada\n' + original_source()
    installed = transform(source)
    assert '# uma mensagem personalizada' in installed
    assert MARKER in installed
    assert transform(installed) == installed


def test_installer_blocks_unknown_source():
    with pytest.raises(RuntimeError, match='diverge'):
        transform('def main():\n    pass\n')


def test_installer_blocks_partial_installation():
    with pytest.raises(RuntimeError, match='parcial'):
        transform(MARKER + '\n')
