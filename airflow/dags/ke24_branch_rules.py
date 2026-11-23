PUBLICAR = "Publicar lote"
SOMENTE_PREVIA = "Somente previa"


def selecionar_destino(resposta):
    if isinstance(resposta, dict) and resposta.get("chosen_options") == [PUBLICAR]:
        return "publicar_lote"
    return "somente_previa"
