def selecionar_destino(novas_extracoes: int) -> str:

    if type(novas_extracoes) is not int or novas_extracoes < 0:
        raise ValueError("Quantidade de novas extracoes invalida.")

    if novas_extracoes == 0:
        return "validacao"

    return "aguardando_publicacao"