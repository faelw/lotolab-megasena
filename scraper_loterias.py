"""
===============================================================================
SYNC LOTERIAS — LotoLab Brasil
Versão robusta — histórico incremental + últimos 10 sempre sincronizados
===============================================================================

Objetivos:
- Manter *_todos.json com o histórico completo resumido.
- Manter *_ultimos_10.json SEMPRE com os 10 concursos mais recentes e completos.
- Nunca depender do estado anterior de *_ultimos_10.json para descobrir os 10
  últimos.
- Atualizar apenas concursos novos no histórico.
- Corrigir automaticamente um *_ultimos_10.json antigo, incompleto ou inválido.
- Usar Caixa como fonte oficial para validação dos 10 últimos.
- Usar a API em lote como acelerador do histórico quando disponível.
- Deduplicar concursos.
- Gravar arquivos atomicamente.
- Validar os dados antes de substituir arquivos existentes.
- Não apagar dados históricos válidos por causa de falha parcial de rede.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

LOTERIAS = [
    "megasena",
    "quina",
    "lotomania",
    "timemania",
    "duplasena",
    "diadesorte",
    "supersete",
    "maismilionaria",
    "loteca",
]

HEROKU_URL = "https://loteriascaixa-api.herokuapp.com/api"

CAIXA_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api"

OUTPUT_DIR = "dados_loterias"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
}

MAX_RETRIES = 3
RETRY_DELAY = 1.5
REQUEST_TIMEOUT = 20
DELAY_ENTRE_REQ = 0.40

ULTIMOS_QTD = 10

VALIDAR_10_NA_CAIXA = True

USAR_FONTE_LOTE = True


# =============================================================================
# LOG
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            "sync_loterias.log",
            encoding="utf-8"
        ),
    ],
)

log = logging.getLogger(__name__)


# =============================================================================
# UTILITÁRIOS
# =============================================================================

def numero_concurso(valor: Any) -> int:
    """Converte o número do concurso para int. Retorna 0 se inválido."""
    try:
        if valor is None:
            return 0

        return int(str(valor).strip())

    except (TypeError, ValueError):
        return 0


def ordenar_por_concurso(
    dados: list[dict],
) -> list[dict]:
    """Ordena do concurso mais recente para o mais antigo."""
    return sorted(
        dados,
        key=lambda x: numero_concurso(
            x.get("concurso")
        ),
        reverse=True,
    )


def deduplicar_por_concurso(
    dados: list[dict],
) -> list[dict]:
    """
    Remove duplicidades mantendo o primeiro registro encontrado.

    Como os dados são previamente ordenados do mais recente para o mais antigo,
    o primeiro registro de cada concurso é o preferencial.
    """
    resultado: list[dict] = []

    vistos: set[int] = set()

    for item in ordenar_por_concurso(dados):

        n = numero_concurso(
            item.get("concurso")
        )

        if n <= 0:
            continue

        if n in vistos:
            continue

        vistos.add(n)

        resultado.append(item)

    return resultado


def ler_json(caminho: str) -> list:
    """Lê um JSON de forma segura. Retorna [] em caso de erro."""

    if not os.path.exists(caminho):
        return []

    try:

        if os.path.getsize(caminho) == 0:
            return []

        with open(
            caminho,
            "r",
            encoding="utf-8"
        ) as arquivo:

            dados = json.load(arquivo)

        return dados if isinstance(
            dados,
            list
        ) else []

    except (
        OSError,
        json.JSONDecodeError
    ) as erro:

        log.warning(
            f"⚠️ Não foi possível ler "
            f"{caminho}: {erro}"
        )

        return []


def arquivo_valido(
    caminho: str,
    min_itens: int = 1,
) -> bool:
    """Verifica existência, tamanho, JSON válido e quantidade mínima."""

    if not os.path.exists(caminho):
        return False

    try:

        if os.path.getsize(caminho) == 0:
            return False

        dados = ler_json(caminho)

        if not isinstance(
            dados,
            list
        ):
            return False

        return len(dados) >= min_itens

    except OSError:
        return False


def salvar_json(
    caminho: str,
    dados: list,
    indent: int | None = None,
) -> bool:
    """
    Salva de maneira atômica:

    arquivo temporário -> validação -> os.replace().
    """

    diretorio = (
        os.path.dirname(caminho)
        or OUTPUT_DIR
    )

    os.makedirs(
        diretorio,
        exist_ok=True
    )

    temporario = caminho + ".tmp"

    try:

        with open(
            temporario,
            "w",
            encoding="utf-8"
        ) as arquivo:

            json.dump(
                dados,
                arquivo,
                ensure_ascii=False,
                indent=indent,
            )

            arquivo.flush()
            os.fsync(
                arquivo.fileno()
            )

        # Validação do JSON antes de substituir
        # o arquivo oficial.

        with open(
            temporario,
            "r",
            encoding="utf-8"
        ) as arquivo:

            validado = json.load(
                arquivo
            )

        if not isinstance(
            validado,
            list
        ):
            raise ValueError(
                "JSON temporário não contém uma lista."
            )

        os.replace(
            temporario,
            caminho
        )

        return True

    except Exception as erro:

        log.error(
            f"❌ Erro ao salvar "
            f"{caminho}: {erro}"
        )

        try:

            if os.path.exists(
                temporario
            ):
                os.remove(
                    temporario
                )

        except OSError:
            pass

        return False


# =============================================================================
# HTTP
# =============================================================================

def get_com_retry(
    url: str,
    verify: bool = True,
) -> Any | None:
    """
    GET com retry e backoff simples.
    """

    for tentativa in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            resposta = requests.get(
                url,
                headers=HEADERS,
                timeout=REQUEST_TIMEOUT,
                verify=verify,
            )

            if resposta.status_code == 200:

                try:

                    return resposta.json()

                except ValueError as erro:

                    log.warning(
                        f"⚠️ JSON inválido recebido "
                        f"de {url}: {erro}"
                    )

            else:

                log.warning(
                    f"⚠️ HTTP "
                    f"{resposta.status_code} em {url} "
                    f"(tentativa "
                    f"{tentativa}/{MAX_RETRIES})"
                )

        except requests.exceptions.Timeout:

            log.warning(
                f"⏱️ Timeout em {url} "
                f"(tentativa "
                f"{tentativa}/{MAX_RETRIES})"
            )

        except requests.exceptions.ConnectionError:

            log.warning(
                f"🌐 Falha de conexão em {url} "
                f"(tentativa "
                f"{tentativa}/{MAX_RETRIES})"
            )

        except requests.RequestException as erro:

            log.warning(
                f"⚠️ Erro HTTP em {url}: {erro} "
                f"(tentativa "
                f"{tentativa}/{MAX_RETRIES})"
            )

        except Exception as erro:

            log.warning(
                f"⚠️ Erro inesperado em {url}: {erro} "
                f"(tentativa "
                f"{tentativa}/{MAX_RETRIES})"
            )

        if tentativa < MAX_RETRIES:

            time.sleep(
                RETRY_DELAY * tentativa
            )

    return None


def fetch_caixa(
    loteria: str,
    concurso: int | str = "",
) -> dict | None:
    """
    Consulta a Caixa.

    Sem concurso:
        retorna o concurso mais recente.

    Com concurso:
        retorna o concurso específico.
    """

    if concurso == "":
        url = (
            f"{CAIXA_URL}/{loteria}"
        )

    else:
        url = (
            f"{CAIXA_URL}/{loteria}/"
            f"{concurso}"
        )

    dados = get_com_retry(
        url,
        verify=False
    )

    return (
        dados
        if isinstance(dados, dict)
        else None
    )


def fetch_lote(
    loteria: str,
) -> list[dict] | None:
    """
    Busca histórico em lote na fonte alternativa.

    Não é considerada fonte oficial para decidir
    qual é o concurso atual.
    """

    url = (
        f"{HEROKU_URL}/{loteria}"
    )

    dados = get_com_retry(
        url,
        verify=True
    )

    if not isinstance(
        dados,
        list
    ) or not dados:

        return None

    resultado = []

    for item in dados:

        if not isinstance(
            item,
            dict
        ):
            continue

        n = numero_concurso(
            item.get("concurso")
        )

        if n > 0:
            resultado.append(item)

    return (
        resultado
        if resultado
        else None
    )


# =============================================================================
# FORMATAÇÃO
# =============================================================================

def formatar(
    dados_caixa: dict,
    loteria: str,
) -> dict | None:
    """
    Converte o retorno da Caixa para o formato
    utilizado pelo LotoLab.
    """

    if not isinstance(
        dados_caixa,
        dict
    ):
        return None

    concurso = numero_concurso(
        dados_caixa.get("numero")
    )

    if concurso <= 0:
        return None

    premiacoes = []

    for premio in (
        dados_caixa.get(
            "listaRateioPremio",
            []
        )
        or []
    ):

        if not isinstance(
            premio,
            dict
        ):
            continue

        premiacoes.append(
            {
                "descricao": premio.get(
                    "descricaoFaixa",
                    ""
                ),
                "faixa": premio.get(
                    "faixa",
                    0
                ),
                "ganhadores": premio.get(
                    "numeroDeGanhadores",
                    0
                ),
                "valorPremio": premio.get(
                    "valorPremio",
                    0.0
                ),
            }
        )

    local_ganhadores = []

    for ganhador in (
        dados_caixa.get(
            "listaMunicipioUFGanhadores",
            []
        )
        or []
    ):

        if not isinstance(
            ganhador,
            dict
        ):
            continue

        local_ganhadores.append(
            {
                "ganhadores": ganhador.get(
                    "ganhadores",
                    1
                ),
                "municipio": ganhador.get(
                    "municipio",
                    ""
                ),
                "nomeFatansiaUL": "",
                "serie": "",
                "posicao": ganhador.get(
                    "posicao",
                    1
                ),
                "uf": ganhador.get(
                    "uf",
                    ""
                ),
            }
        )

    local_str = dados_caixa.get(
        "localSorteio",
        ""
    )

    municipio_str = dados_caixa.get(
        "nomeMunicipioUFSorteio",
        ""
    )

    if local_str and municipio_str:

        local_completo = (
            f"{local_str} em "
            f"{municipio_str}"
        )

    else:

        local_completo = local_str

    dezenas_brutas = (
        dados_caixa.get(
            "listaDezenas",
            []
        )
        or []
    )

    dezenas = [
        str(d).zfill(2)
        for d in dezenas_brutas
    ]

    return {
        "loteria": loteria,
        "concurso": concurso,
        "data": dados_caixa.get(
            "dataApuracao",
            ""
        ),
        "local": local_completo,
        "concursoEspecial": (
            dados_caixa.get(
                "indicadorConcursoEspecial"
            ) == 1
        ),
        "dezenasOrdemSorteio": dezenas,
        "dezenas": sorted(dezenas),
        "trevos": dados_caixa.get(
            "trevos",
            []
        ),
        "timeCoracao": dados_caixa.get(
            "nomeTimeCoracao"
        ),
        "mesSorte": dados_caixa.get(
            "mesSorte"
        ),
        "premiacoes": premiacoes,
        "estadosPremiados": [],
        "observacao": dados_caixa.get(
            "observacao",
            ""
        ),
        "acumulou": dados_caixa.get(
            "acumulado",
            False
        ),
        "proximoConcurso": dados_caixa.get(
            "numeroConcursoProximo",
            0
        ),
        "dataProximoConcurso": dados_caixa.get(
            "dataProximoConcurso",
            ""
        ),
        "localGanhadores": local_ganhadores,
        "valorArrecadado": dados_caixa.get(
            "valorArrecadado",
            0.0
        ),
        "valorAcumuladoConcurso_0_5": (
            dados_caixa.get(
                "valorAcumuladoConcurso_0_5",
                0.0
            )
        ),
        "valorAcumuladoConcursoEspecial": (
            dados_caixa.get(
                "valorAcumuladoConcursoEspecial",
                0.0
            )
        ),
        "valorAcumuladoProximoConcurso": (
            dados_caixa.get(
                "valorAcumuladoProximoConcurso",
                0.0
            )
        ),
        "valorEstimadoProximoConcurso": (
            dados_caixa.get(
                "valorEstimadoProximoConcurso",
                0.0
            )
        ),
    }


def normalizar_item_lote(
    item: dict,
    loteria: str,
) -> dict | None:
    """
    Normaliza registros vindos da fonte em lote.
    """

    if not isinstance(
        item,
        dict
    ):
        return None

    concurso = numero_concurso(
        item.get("concurso")
    )

    if concurso <= 0:
        return None

    copia = dict(item)

    copia["concurso"] = concurso

    copia["loteria"] = (
        copia.get("loteria")
        or loteria
    )

    dezenas = copia.get(
        "dezenas",
        []
    )

    if isinstance(
        dezenas,
        list
    ):

        copia["dezenas"] = sorted(
            [
                str(d).zfill(2)
                for d in dezenas
            ]
        )

    ordem = copia.get(
        "dezenasOrdemSorteio"
    )

    if isinstance(
        ordem,
        list
    ):

        copia[
            "dezenasOrdemSorteio"
        ] = [
            str(d).zfill(2)
            for d in ordem
        ]

    return copia


def resumir(
    jogo: dict,
) -> dict:
    """
    Conteúdo enxuto do *_todos.json.
    """

    return {
        "concurso": numero_concurso(
            jogo.get("concurso")
        ),
        "data": jogo.get(
            "data",
            ""
        ),
        "dezenas": jogo.get(
            "dezenas",
            []
        ),
    }
# =============================================================================
# ÚLTIMOS 10 — PARTE CRÍTICA
# =============================================================================

def obter_ultimos_10_caixa(
    loteria: str,
    concurso_atual: int,
) -> list[dict]:
    """
    Busca os 10 concursos mais recentes DIRETAMENTE na Caixa.

    Esta é a principal correção do problema original.

    Não importa se:
    - o histórico já está atualizado;
    - o *_ultimos_10.json está velho;
    - não houve concurso novo desde a última execução;
    - o arquivo anterior contém apenas 1, 2 ou 5 concursos.

    Os 10 últimos são reconstruídos a partir do número atual informado pela
    Caixa.
    """

    inicio = max(
        1,
        concurso_atual - ULTIMOS_QTD + 1
    )

    log.info(
        f"🎯 Sincronizando últimos "
        f"{ULTIMOS_QTD} da "
        f"{loteria.upper()}: "
        f"#{inicio} → #{concurso_atual}"
    )

    resultados: list[dict] = []

    for numero in range(
        concurso_atual,
        inicio - 1,
        -1
    ):

        dados = fetch_caixa(
            loteria,
            numero
        )

        if not dados:

            log.warning(
                f"⚠️ Não foi possível obter "
                f"{loteria.upper()} #{numero}."
            )

            continue

        formatado = formatar(
            dados,
            loteria
        )

        if not formatado:

            log.warning(
                f"⚠️ Retorno inválido para "
                f"{loteria.upper()} #{numero}."
            )

            continue

        resultados.append(
            formatado
        )

        # Evita sobrecarregar a API da Caixa.
        time.sleep(
            DELAY_ENTRE_REQ
        )

    resultados = deduplicar_por_concurso(
        resultados
    )

    resultados = resultados[
        :ULTIMOS_QTD
    ]

    numeros = [
        numero_concurso(
            item.get("concurso")
        )
        for item in resultados
    ]

    log.info(
        f"📌 Últimos encontrados: "
        f"{numeros}"
    )

    return resultados


def ultimos_10_estao_corretos(
    ultimos: list[dict],
    concurso_atual: int,
) -> bool:
    """
    Verifica se um arquivo local de últimos 10
    está realmente atualizado.
    """

    if len(ultimos) != ULTIMOS_QTD:
        return False

    ordenados = deduplicar_por_concurso(
        ultimos
    )

    if len(ordenados) != ULTIMOS_QTD:
        return False

    numeros = [
        numero_concurso(
            item.get("concurso")
        )
        for item in ordenados
    ]

    esperados = list(
        range(
            concurso_atual,
            max(
                0,
                concurso_atual - ULTIMOS_QTD
            ),
            -1,
        )
    )

    return numeros == esperados


# =============================================================================
# RECONSTRUÇÃO COMPLETA
# =============================================================================

def reconstruir_completo(
    loteria: str,
    concurso_mais_recente: int,
) -> list[dict]:
    """
    Reconstrói o histórico quando *_todos.json
    não existe ou está inválido.

    Primeiro tenta a fonte em lote.

    Depois busca na Caixa os concursos que faltarem.
    """

    log.info(
        f"🔁 Reconstruindo "
        f"{loteria.upper()} "
        f"do concurso 1 ao "
        f"#{concurso_mais_recente}..."
    )

    completos: list[dict] = []

    ids_obtidos: set[int] = set()

    # -------------------------------------------------------------------------
    # 1. Fonte em lote
    # -------------------------------------------------------------------------

    if USAR_FONTE_LOTE:

        dados_lote = fetch_lote(
            loteria
        )

        if dados_lote:

            for item in dados_lote:

                normalizado = (
                    normalizar_item_lote(
                        item,
                        loteria
                    )
                )

                if not normalizado:
                    continue

                n = numero_concurso(
                    normalizado.get(
                        "concurso"
                    )
                )

                if n > concurso_mais_recente:
                    continue

                if n in ids_obtidos:
                    continue

                completos.append(
                    normalizado
                )

                ids_obtidos.add(n)

            log.info(
                f"📦 Fonte em lote forneceu "
                f"{len(completos)} concursos."
            )

    # -------------------------------------------------------------------------
    # 2. Concursos faltantes
    # -------------------------------------------------------------------------

    faltando = [
        n
        for n in range(
            1,
            concurso_mais_recente + 1
        )
        if n not in ids_obtidos
    ]

    if faltando:

        log.info(
            f"🌐 Caixa será usada para "
            f"{len(faltando)} concurso(s) "
            f"faltantes."
        )

        for indice, numero in enumerate(
            faltando,
            start=1
        ):

            dados = fetch_caixa(
                loteria,
                numero
            )

            if dados:

                formatado = formatar(
                    dados,
                    loteria
                )

                if formatado:

                    n = numero_concurso(
                        formatado.get(
                            "concurso"
                        )
                    )

                    if (
                        n > 0
                        and n not in ids_obtidos
                    ):

                        completos.append(
                            formatado
                        )

                        ids_obtidos.add(n)

            if indice % 50 == 0:

                log.info(
                    f"   ... "
                    f"{indice}/"
                    f"{len(faltando)} "
                    f"concursos processados"
                )

            time.sleep(
                DELAY_ENTRE_REQ
            )

    completos = deduplicar_por_concurso(
        completos
    )

    log.info(
        f"✅ Reconstrução concluída: "
        f"{len(completos)} concursos."
    )

    return completos


# =============================================================================
# ATUALIZAÇÃO INCREMENTAL DO HISTÓRICO
# =============================================================================

def buscar_novos_concursos(
    loteria: str,
    ultimo_salvo: int,
    concurso_atual: int,
) -> list[dict]:
    """
    Busca somente concursos posteriores ao
    último salvo.

    Usa a fonte em lote primeiro e Caixa
    como complemento.
    """

    novos: list[dict] = []

    ids_novos: set[int] = set()

    if ultimo_salvo >= concurso_atual:
        return []

    # -------------------------------------------------------------------------
    # 1. Fonte em lote
    # -------------------------------------------------------------------------

    if USAR_FONTE_LOTE:

        dados_lote = fetch_lote(
            loteria
        )

        if dados_lote:

            for item in dados_lote:

                normalizado = (
                    normalizar_item_lote(
                        item,
                        loteria
                    )
                )

                if not normalizado:
                    continue

                n = numero_concurso(
                    normalizado.get(
                        "concurso"
                    )
                )

                if n <= ultimo_salvo:
                    continue

                if n > concurso_atual:
                    continue

                if n in ids_novos:
                    continue

                novos.append(
                    normalizado
                )

                ids_novos.add(n)

            if novos:

                log.info(
                    f"📥 Fonte em lote: "
                    f"{len(novos)} novo(s)."
                )

    # -------------------------------------------------------------------------
    # 2. Caixa complementa lacunas
    # -------------------------------------------------------------------------

    faltando = [
        n
        for n in range(
            ultimo_salvo + 1,
            concurso_atual + 1
        )
        if n not in ids_novos
    ]

    if faltando:

        log.info(
            f"🌐 Caixa complementará "
            f"{len(faltando)} concurso(s)."
        )

        for numero in faltando:

            dados = fetch_caixa(
                loteria,
                numero
            )

            if dados:

                formatado = formatar(
                    dados,
                    loteria
                )

                if formatado:

                    n = numero_concurso(
                        formatado.get(
                            "concurso"
                        )
                    )

                    if (
                        ultimo_salvo < n
                        <= concurso_atual
                        and n not in ids_novos
                    ):

                        novos.append(
                            formatado
                        )

                        ids_novos.add(n)

            time.sleep(
                DELAY_ENTRE_REQ
            )

    return deduplicar_por_concurso(
        novos
    )


# =============================================================================
# PROCESSAMENTO PRINCIPAL
# =============================================================================

def process_loteria(
    loteria: str,
) -> bool:

    log.info("")
    log.info("=" * 70)

    log.info(
        f"🔄 SINCRONIZANDO "
        f"{loteria.upper()}"
    )

    log.info("=" * 70)

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    caminho_todos = os.path.join(
        OUTPUT_DIR,
        f"{loteria}_todos.json",
    )

    caminho_ultimos = os.path.join(
        OUTPUT_DIR,
        f"{loteria}_ultimos_10.json",
    )

    # -------------------------------------------------------------------------
    # PASSO 1 — concurso oficial atual
    # -------------------------------------------------------------------------

    log.info(
        "📡 Consultando concurso mais "
        "recente na Caixa..."
    )

    dados_recente = fetch_caixa(
        loteria
    )

    if not dados_recente:

        log.error(
            f"❌ Não foi possível acessar "
            f"a Caixa para "
            f"{loteria.upper()}."
        )

        return False

    concurso_atual = numero_concurso(
        dados_recente.get(
            "numero"
        )
    )

    if concurso_atual <= 0:

        log.error(
            f"❌ Concurso inválido "
            f"retornado para "
            f"{loteria.upper()}."
        )

        return False

    log.info(
        f"🏁 Concurso oficial atual: "
        f"#{concurso_atual}"
    )

    # -------------------------------------------------------------------------
    # PASSO 2 — carregar arquivos locais
    # -------------------------------------------------------------------------

    todos = ler_json(
        caminho_todos
    )

    ultimos_existentes = ler_json(
        caminho_ultimos
    )

    todos_validos = (
        isinstance(
            todos,
            list
        )
        and len(todos) > 0
    )

    ultimos_validos = (
        isinstance(
            ultimos_existentes,
            list
        )
        and len(ultimos_existentes) > 0
    )

    # Evita warning de variável não utilizada
    # e documenta que o arquivo anterior é apenas
    # um fallback.
    _ = ultimos_validos

    # -------------------------------------------------------------------------
    # PASSO 3 — reconstrução completa
    # -------------------------------------------------------------------------

    if not todos_validos:

        log.warning(
            f"⚠️ "
            f"{os.path.basename(caminho_todos)} "
            f"está ausente, vazio ou inválido."
        )

        completos = reconstruir_completo(
            loteria,
            concurso_atual
        )

        if not completos:

            log.error(
                f"❌ Não foi possível reconstruir "
                f"{loteria.upper()}."
            )

            return False

        todos = [
            resumir(item)
            for item in completos
        ]

        todos = deduplicar_por_concurso(
            todos
        )

        if not salvar_json(
            caminho_todos,
            todos,
        ):

            return False

        log.info(
            f"💾 Histórico reconstruído: "
            f"{len(todos)} concursos."
        )

    else:

        # Normaliza o histórico existente.

        todos_normalizados = []

        for item in todos:

            if not isinstance(
                item,
                dict
            ):
                continue

            n = numero_concurso(
                item.get("concurso")
            )

            if n <= 0:
                continue

            copia = dict(item)

            copia["concurso"] = n

            todos_normalizados.append(
                copia
            )

        todos = deduplicar_por_concurso(
            todos_normalizados
        )

    # -------------------------------------------------------------------------
    # PASSO 4 — último concurso local
    # -------------------------------------------------------------------------

    ultimo_salvo = max(
        (
            numero_concurso(
                item.get("concurso")
            )
            for item in todos
        ),
        default=0,
    )

    log.info(
        f"💾 Último concurso no histórico "
        f"local: #{ultimo_salvo}"
    )

    # -------------------------------------------------------------------------
    # PASSO 5 — atualização incremental
    # -------------------------------------------------------------------------

    if concurso_atual > ultimo_salvo:

        quantidade = (
            concurso_atual
            - ultimo_salvo
        )

        log.info(
            f"⚡ {quantidade} concurso(s) "
            f"novo(s) encontrado(s)."
        )

        novos = buscar_novos_concursos(
            loteria,
            ultimo_salvo,
            concurso_atual,
        )

        if novos:

            novos_resumidos = [
                resumir(item)
                for item in novos
            ]

            todos.extend(
                novos_resumidos
            )

            todos = deduplicar_por_concurso(
                todos
            )

            if not salvar_json(
                caminho_todos,
                todos,
            ):

                log.error(
                    f"❌ Falha ao salvar "
                    f"histórico de "
                    f"{loteria.upper()}."
                )

                return False

            log.info(
                f"✅ Histórico atualizado "
                f"com {len(novos)} novo(s)."
            )

        else:

            log.warning(
                f"⚠️ A Caixa indica "
                f"#{concurso_atual}, "
                f"mas nenhum concurso novo "
                f"foi obtido."
            )

    else:

        log.info(
            "✅ Histórico já contém "
            "o concurso atual."
        )

    # -------------------------------------------------------------------------
    # PASSO 6 — ÚLTIMOS 10
    #
    # ESTE PASSO SEMPRE É EXECUTADO.
    #
    # Isso corrige o bug original: não damos
    # return antes de atualizar
    # *_ultimos_10.json.
    # -------------------------------------------------------------------------

    if VALIDAR_10_NA_CAIXA:

        ultimos_novos = (
            obter_ultimos_10_caixa(
                loteria,
                concurso_atual,
            )
        )

        if len(ultimos_novos) < ULTIMOS_QTD:

            log.warning(
                f"⚠️ Caixa retornou apenas "
                f"{len(ultimos_novos)}/"
                f"{ULTIMOS_QTD} "
                f"dos últimos concursos."
            )

            # Se já temos um arquivo local
            # válido, não o destruímos.

            if (
                len(ultimos_existentes)
                >= len(ultimos_novos)
            ):

                ultimos_novos = (
                    ultimos_existentes
                )

        ultimos_finais = (
            deduplicar_por_concurso(
                ultimos_novos
            )[:ULTIMOS_QTD]
        )

    else:

        # Modo alternativo:
        # deriva os últimos 10 do histórico.

        ultimos_finais = [
            dict(item)
            for item in todos[
                :ULTIMOS_QTD
            ]
        ]

    # -------------------------------------------------------------------------
    # PASSO 7 — validação final
    # -------------------------------------------------------------------------

    ultimos_finais = ordenar_por_concurso(
        ultimos_finais
    )[:ULTIMOS_QTD]

    numeros_finais = [
        numero_concurso(
            item.get("concurso")
        )
        for item in ultimos_finais
    ]

    log.info(
        f"📋 Últimos {ULTIMOS_QTD}: "
        f"{numeros_finais}"
    )

    if not ultimos_finais:

        log.error(
            f"❌ Não foi possível produzir "
            f"{loteria}_ultimos_10.json."
        )

        return False

    # -------------------------------------------------------------------------
    # PASSO 8 — salvar últimos 10
    # -------------------------------------------------------------------------

    if not salvar_json(
        caminho_ultimos,
        ultimos_finais,
        indent=2,
    ):

        log.error(
            f"❌ Falha ao salvar últimos 10 "
            f"de {loteria.upper()}."
        )

        return False

    # -------------------------------------------------------------------------
    # PASSO 9 — verificação pós-gravação
    # -------------------------------------------------------------------------

    ultimos_verificacao = ler_json(
        caminho_ultimos
    )

    if not ultimos_verificacao:

        log.error(
            f"❌ Verificação pós-gravação "
            f"falhou em "
            f"{caminho_ultimos}."
        )

        return False

    numeros_verificacao = [
        numero_concurso(
            item.get("concurso")
        )
        for item in ultimos_verificacao
    ]

    if len(ultimos_verificacao) == ULTIMOS_QTD:

        if (
            concurso_atual
            not in numeros_verificacao
        ):

            log.error(
                f"❌ O concurso atual "
                f"#{concurso_atual} "
                f"não está nos últimos 10!"
            )

            return False

    log.info(
        f"✅ {loteria.upper()} sincronizada."
    )

    log.info(
        f"   Histórico: "
        f"{len(todos)} concursos"
    )

    log.info(
        f"   Últimos: "
        f"{len(ultimos_verificacao)} concursos"
    )

    if numeros_verificacao:

        log.info(
            f"   Mais recente: "
            f"#{numeros_verificacao[0]}"
        )

    else:

        log.info(
            "   Mais recente: "
            "não disponível"
        )

    return True
# =============================================================================
# RELATÓRIO
# =============================================================================

def imprimir_relatorio(
    resultados: dict[str, bool],
) -> None:

    log.info("")
    log.info("=" * 70)
    log.info("📋 RELATÓRIO FINAL")
    log.info("=" * 70)

    sucesso = [
        loteria
        for loteria, resultado
        in resultados.items()
        if resultado
    ]

    falhas = [
        loteria
        for loteria, resultado
        in resultados.items()
        if not resultado
    ]

    for loteria in sucesso:

        caminho_todos = os.path.join(
            OUTPUT_DIR,
            f"{loteria}_todos.json",
        )

        caminho_ultimos = os.path.join(
            OUTPUT_DIR,
            f"{loteria}_ultimos_10.json",
        )

        todos = ler_json(
            caminho_todos
        )

        ultimos = ler_json(
            caminho_ultimos
        )

        numeros = [
            numero_concurso(
                item.get("concurso")
            )
            for item in ultimos
        ]

        log.info(
            f"✅ {loteria.upper():<18} "
            f"→ {len(todos):>5} históricos | "
            f"últimos: {numeros}"
        )

    for loteria in falhas:

        log.error(
            f"❌ {loteria.upper():<18} "
            f"→ falha na sincronização"
        )

    log.info("")

    log.info(
        f"Sucesso: "
        f"{len(sucesso)}/{len(resultados)}"
    )

    log.info(
        "Concluído em: "
        f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    )


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:

    log.info("")

    log.info("=" * 70)

    log.info(
        "🚀 LotoLab Brasil — "
        "Sincronização de Dados"
    )

    log.info("=" * 70)

    log.info(
        f"Loterias: "
        f"{', '.join(LOTERIAS)}"
    )

    log.info(
        f"Diretório: "
        f"{os.path.abspath(OUTPUT_DIR)}"
    )

    log.info(
        f"Últimos concursos: "
        f"{ULTIMOS_QTD}"
    )

    log.info(
        "Validação Caixa dos últimos 10: "
        f"{'ATIVA' if VALIDAR_10_NA_CAIXA else 'DESATIVADA'}"
    )

    resultados: dict[str, bool] = {}

    for loteria in LOTERIAS:

        try:

            resultados[loteria] = (
                process_loteria(
                    loteria
                )
            )

        except Exception as erro:

            log.exception(
                f"💥 Erro inesperado em "
                f"{loteria.upper()}: "
                f"{erro}"
            )

            resultados[loteria] = False

        time.sleep(0.5)

    imprimir_relatorio(
        resultados
    )


if __name__ == "__main__":
    main()