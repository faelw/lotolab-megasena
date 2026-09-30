"""
===============================================================================
SYNC LOTERIAS — LotoLab Brasil (Versão Definitiva Multi-Fontes + Merge)
Parte 1: Setup, Configurações e Utilitários de Arquivo
===============================================================================
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

CAIXA_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api"
BRASILAPI_URL = "https://brasilapi.com.br/api/loterias/v1"
HEROKU_URL = "https://loteriascaixa-api.herokuapp.com/api"

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
    try:
        if valor is None:
            return 0
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return 0

def ordenar_por_concurso(dados: list[dict]) -> list[dict]:
    return sorted(
        dados,
        key=lambda x: numero_concurso(x.get("concurso")),
        reverse=True,
    )

def deduplicar_por_concurso(dados: list[dict]) -> list[dict]:
    resultado: list[dict] = []
    vistos: set[int] = set()

    for item in ordenar_por_concurso(dados):
        n = numero_concurso(item.get("concurso"))
        if n <= 0 or n in vistos:
            continue
        vistos.add(n)
        resultado.append(item)

    return resultado

def ler_json(caminho: str) -> list:
    if not os.path.exists(caminho):
        return []
    try:
        if os.path.getsize(caminho) == 0:
            return []
        with open(caminho, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return dados if isinstance(dados, list) else []
    except (OSError, json.JSONDecodeError) as erro:
        log.warning(f"⚠️ Não foi possível ler {caminho}: {erro}")
        return []

def salvar_json(caminho: str, dados: list, indent: int | None = None) -> bool:
    diretorio = os.path.dirname(caminho) or OUTPUT_DIR
    os.makedirs(diretorio, exist_ok=True)
    temporario = caminho + ".tmp"

    try:
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=indent)
            arquivo.flush()
            os.fsync(arquivo.fileno())

        with open(temporario, "r", encoding="utf-8") as arquivo:
            validado = json.load(arquivo)

        if not isinstance(validado, list):
            raise ValueError("JSON temporário não contém uma lista.")

        os.replace(temporario, caminho)
        return True

    except Exception as erro:
        log.error(f"❌ Erro ao salvar {caminho}: {erro}")
        try:
            if os.path.exists(temporario):
                os.remove(temporario)
        except OSError:
            pass
        return False
"""
===============================================================================
Parte 2: HTTP, Requisições e Normalização de Dados
===============================================================================
"""

# =============================================================================
# HTTP & CAMADAS DE REQUISIÇÃO (MULTIFONTES)
# =============================================================================

def get_com_retry(url: str, verify: bool = True) -> Any | None:
    for tentativa in range(1, MAX_RETRIES + 1):
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
                    log.warning(f"⚠️ JSON inválido recebido de {url}: {erro}")
            else:
                log.warning(
                    f"⚠️ HTTP {resposta.status_code} em {url} "
                    f"(tentativa {tentativa}/{MAX_RETRIES})"
                )
        except requests.exceptions.Timeout:
            log.warning(f"⏱️ Timeout em {url} (tentativa {tentativa}/{MAX_RETRIES})")
        except requests.exceptions.ConnectionError:
            log.warning(f"🌐 Falha de conexão em {url} (tentativa {tentativa}/{MAX_RETRIES})")
        except requests.RequestException as erro:
            log.warning(f"⚠️️ Erro HTTP em {url}: {erro} (tentativa {tentativa}/{MAX_RETRIES})")
        except Exception as erro:
            log.warning(f"⚠️ Erro inesperado em {url}: {erro} (tentativa {tentativa}/{MAX_RETRIES})")

        if tentativa < MAX_RETRIES:
            time.sleep(RETRY_DELAY * tentativa)

    return None

def fetch_caixa(loteria: str, concurso: int | str = "") -> dict | None:
    url = f"{CAIXA_URL}/{loteria}" if concurso == "" else f"{CAIXA_URL}/{loteria}/{concurso}"
    dados = get_com_retry(url, verify=False)
    return dados if isinstance(dados, dict) else None

def fetch_brasilapi(loteria: str) -> dict | None:
    url = f"{BRASILAPI_URL}/{loteria}"
    dados = get_com_retry(url, verify=True)
    return dados if isinstance(dados, dict) else None

def fetch_lote(loteria: str) -> list[dict] | None:
    url = f"{HEROKU_URL}/{loteria}"
    dados = get_com_retry(url, verify=True)
    
    if not isinstance(dados, list) or not dados:
        return None

    resultado = []
    for item in dados:
        if not isinstance(item, dict):
            continue
        n = numero_concurso(item.get("concurso") or item.get("numero"))
        if n > 0:
            resultado.append(item)

    return resultado if resultado else None

# =============================================================================
# FORMATAÇÃO E NORMALIZAÇÃO DE CADA FONTE
# =============================================================================

def formatar_caixa(dados_caixa: dict, loteria: str) -> dict | None:
    if not isinstance(dados_caixa, dict):
        return None

    concurso = numero_concurso(dados_caixa.get("numero"))
    if concurso <= 0:
        return None

    premiacoes = []
    for premio in (dados_caixa.get("listaRateioPremio", []) or []):
        if not isinstance(premio, dict):
            continue
        premiacoes.append({
            "descricao": premio.get("descricaoFaixa", ""),
            "faixa": premio.get("faixa", 0),
            "ganhadores": premio.get("numeroDeGanhadores", 0),
            "valorPremio": premio.get("valorPremio", 0.0),
        })

    local_ganhadores = []
    for ganhador in (dados_caixa.get("listaMunicipioUFGanhadores", []) or []):
        if not isinstance(ganhador, dict):
            continue
        local_ganhadores.append({
            "ganhadores": ganhador.get("ganhadores", 1),
            "municipio": ganhador.get("municipio", ""),
            "nomeFatansiaUL": "",
            "serie": "",
            "posicao": ganhador.get("posicao", 1),
            "uf": ganhador.get("uf", ""),
        })

    local_str = dados_caixa.get("localSorteio", "")
    municipio_str = dados_caixa.get("nomeMunicipioUFSorteio", "")
    local_completo = f"{local_str} em {municipio_str}" if (local_str and municipio_str) else local_str

    dezenas_brutas = dados_caixa.get("listaDezenas", []) or []
    dezenas = [str(d).zfill(2) for d in dezenas_brutas]

    return {
        "loteria": loteria,
        "concurso": concurso,
        "data": dados_caixa.get("dataApuracao", ""),
        "local": local_completo,
        "concursoEspecial": dados_caixa.get("indicadorConcursoEspecial") == 1,
        "dezenasOrdemSorteio": dezenas,
        "dezenas": sorted(dezenas),
        "trevos": dados_caixa.get("trevos", []),
        "timeCoracao": dados_caixa.get("nomeTimeCoracao"),
        "mesSorte": dados_caixa.get("mesSorte"),
        "premiacoes": premiacoes,
        "estadosPremiados": [],
        "observacao": dados_caixa.get("observacao", ""),
        "acumulou": dados_caixa.get("acumulado", False),
        "proximoConcurso": dados_caixa.get("numeroConcursoProximo", 0),
        "dataProximoConcurso": dados_caixa.get("dataProximoConcurso", ""),
        "localGanhadores": local_ganhadores,
        "valorArrecadado": dados_caixa.get("valorArrecadado", 0.0),
        "valorAcumuladoConcurso_0_5": dados_caixa.get("valorAcumuladoConcurso_0_5", 0.0),
        "valorAcumuladoConcursoEspecial": dados_caixa.get("valorAcumuladoConcursoEspecial", 0.0),
        "valorAcumuladoProximoConcurso": dados_caixa.get("valorAcumuladoProximoConcurso", 0.0),
        "valorEstimadoProximoConcurso": dados_caixa.get("valorEstimadoProximoConcurso", 0.0),
    }

def normalizar_item_generico(item: dict, loteria: str) -> dict | None:
    if not isinstance(item, dict):
        return None

    concurso = numero_concurso(item.get("concurso") or item.get("numero") or item.get("numeroConcurso"))
    if concurso <= 0:
        return None

    copia = dict(item)
    copia["concurso"] = concurso
    copia["loteria"] = copia.get("loteria") or loteria

    dezenas = copia.get("dezenas") or copia.get("listaDezenas") or []
    if isinstance(dezenas, list):
        copia["dezenas"] = sorted([str(d).zfill(2) for d in dezenas])

    ordem = copia.get("dezenasOrdemSorteio")
    if isinstance(ordem, list):
        copia["dezenasOrdemSorteio"] = [str(d).zfill(2) for d in ordem]
    else:
        copia["dezenasOrdemSorteio"] = copia["dezenas"]

    copia.setdefault("data", item.get("dataApuracao") or item.get("data") or "")
    copia.setdefault("local", item.get("localSorteio") or item.get("local") or "")
    copia.setdefault("acumulou", item.get("acumulado") or False)
    copia.setdefault("premiacoes", item.get("premiacoes") or [])
    copia.setdefault("proximoConcurso", item.get("numeroConcursoProximo") or 0)
    copia.setdefault("dataProximoConcurso", item.get("dataProximoConcurso") or "")

    return copia

def resumir(jogo: dict) -> dict:
    return {
        "concurso": numero_concurso(jogo.get("concurso")),
        "data": jogo.get("data", ""),
        "dezenas": jogo.get("dezenas", []),
    }

def buscar_concurso_detalhado(loteria: str, numero: int) -> dict | None:
    """Busca o concurso detalhado tentando Caixa e depois BrasilAPI."""
    dados = fetch_caixa(loteria, numero)
    if dados:
        fmt = formatar_caixa(dados, loteria)
        if fmt and fmt["concurso"] == numero:
            return fmt

    log.warning(f"⚠️ Caixa falhou para #{numero}. Buscando na BrasilAPI...")
    url_ba = f"{BRASILAPI_URL}/{loteria}/{numero}"
    dados_ba = get_com_retry(url_ba, verify=True)
    if dados_ba:
        fmt = normalizar_item_generico(dados_ba, loteria)
        if fmt and fmt["concurso"] == numero:
            log.info(f"✅ #{numero} recuperado via BrasilAPI.")
            return fmt

    return None

# =============================================================================
# OBTENÇÃO INTELIGENTE DO CONCURSO ATUAL (MULTIFONTES EM CASCATA)
# =============================================================================

def obter_concurso_atual(loteria: str) -> tuple[int, dict | None]:
    log.info("📡 Consultando concurso mais recente na Caixa...")
    dados = fetch_caixa(loteria)
    if dados:
        formatado = formatar_caixa(dados, loteria)
        if formatado and formatado["concurso"] > 0:
            return formatado["concurso"], formatado

    log.warning("⚠️️ Caixa indisponível. Tentando BrasilAPI...")
    dados_ba = fetch_brasilapi(loteria)
    if dados_ba:
        formatado = normalizar_item_generico(dados_ba, loteria)
        if formatado and formatado["concurso"] > 0:
            log.info("✅ Concurso obtido via BrasilAPI.")
            return formatado["concurso"], formatado

    log.warning("⚠️ BrasilAPI indisponível. Tentando API em Lote...")
    dados_lote = fetch_lote(loteria)
    if dados_lote:
        ordenado = ordenar_por_concurso(dados_lote)
        if ordenado:
            formatado = normalizar_item_generico(ordenado[0], loteria)
            if formatado and formatado["concurso"] > 0:
                log.info("✅ Concurso obtido via API em Lote.")
                return formatado["concurso"], formatado

    return 0, None

"""
===============================================================================
Parte 3: Lógica Core, Sincronização e Main
===============================================================================
"""

# =============================================================================
# ÚLTIMOS 10 E RECONSTRUÇÃO
# =============================================================================

def obter_ultimos_10_robusto(loteria: str, concurso_atual: int) -> list[dict]:
    inicio = max(1, concurso_atual - ULTIMOS_QTD + 1)
    log.info(f"🎯 Sincronizando últimos {ULTIMOS_QTD} detalhados da {loteria.upper()}: #{inicio} → #{concurso_atual}")

    resultados: list[dict] = []

    for numero in range(concurso_atual, inicio - 1, -1):
        detalhado = buscar_concurso_detalhado(loteria, numero)
        if detalhado:
            resultados.append(detalhado)
        else:
            log.warning(f"⚠️ Todas as fontes falharam para #{numero}.")
            
        time.sleep(DELAY_ENTRE_REQ)

    return resultados

def reconstruir_completo(loteria: str, concurso_mais_recente: int) -> list[dict]:
    log.info(f"🔁 Reconstruindo {loteria.upper()} do concurso 1 ao #{concurso_mais_recente}...")
    completos: list[dict] = []
    ids_obtidos: set[int] = set()

    if USAR_FONTE_LOTE:
        dados_lote = fetch_lote(loteria)
        if dados_lote:
            for item in dados_lote:
                normalizado = normalizar_item_generico(item, loteria)
                if not normalizado:
                    continue
                n = numero_concurso(normalizado.get("concurso"))
                if 0 < n <= concurso_mais_recente and n not in ids_obtidos:
                    completos.append(normalizado)
                    ids_obtidos.add(n)
            log.info(f"📦 Fonte em lote forneceu {len(completos)} concursos.")

    faltando = [n for n in range(1, concurso_mais_recente + 1) if n not in ids_obtidos]
    if faltando:
        log.info(f"🌐 Caixa/APIs serão usadas para {len(faltando)} concurso(s) faltantes.")
        for indice, numero in enumerate(faltando, start=1):
            dados = fetch_caixa(loteria, numero)
            if dados:
                formatado = formatar_caixa(dados, loteria)
                if formatado:
                    n = numero_concurso(formatado.get("concurso"))
                    if n > 0 and n not in ids_obtidos:
                        completos.append(formatado)
                        ids_obtidos.add(n)

            if indice % 50 == 0:
                log.info(f"   ... {indice}/{len(faltando)} concursos processados")
            time.sleep(DELAY_ENTRE_REQ)

    return deduplicar_por_concurso(completos)

def buscar_novos_concursos(loteria: str, ultimo_salvo: int, concurso_atual: int) -> list[dict]:
    if ultimo_salvo >= concurso_atual:
        return []

    novos: list[dict] = []
    ids_novos: set[int] = set()

    if USAR_FONTE_LOTE:
        dados_lote = fetch_lote(loteria)
        if dados_lote:
            for item in dados_lote:
                normalizado = normalizar_item_generico(item, loteria)
                if not normalizado:
                    continue
                n = numero_concurso(normalizado.get("concurso"))
                if ultimo_salvo < n <= concurso_atual and n not in ids_novos:
                    novos.append(normalizado)
                    ids_novos.add(n)

    faltando = [n for n in range(ultimo_salvo + 1, concurso_atual + 1) if n not in ids_novos]
    for numero in faltando:
        dados = fetch_caixa(loteria, numero)
        if dados:
            formatado = formatar_caixa(dados, loteria)
            if formatado:
                n = numero_concurso(formatado.get("concurso"))
                if ultimo_salvo < n <= concurso_atual and n not in ids_novos:
                    novos.append(formatado)
                    ids_novos.add(n)
        time.sleep(DELAY_ENTRE_REQ)

    return deduplicar_por_concurso(novos)

# =============================================================================
# PROCESSAMENTO PRINCIPAL POR LOTERIA
# =============================================================================

def process_loteria(loteria: str) -> bool:
    log.info("")
    log.info("=" * 70)
    log.info(f"🔄 SINCRONIZANDO {loteria.upper()}")
    log.info("=" * 70)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    caminho_todos = os.path.join(OUTPUT_DIR, f"{loteria}_todos.json")
    caminho_ultimos = os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json")

    concurso_atual, dados_recente = obter_concurso_atual(loteria)
    if concurso_atual <= 0:
        log.error(f"❌ Não foi possível determinar o concurso atual para {loteria.upper()}.")
        return False

    log.info(f"🏁 Concurso oficial atual: #{concurso_atual}")

    todos = ler_json(caminho_todos)
    ultimos_existentes = ler_json(caminho_ultimos)
    todos_validos = isinstance(todos, list) and len(todos) > 0

    if not todos_validos:
        log.warning(f"⚠️ {os.path.basename(caminho_todos)} ausente ou inválido.")
        completos = reconstruir_completo(loteria, concurso_atual)
        if not completos:
            log.error(f"❌ Falha na reconstrução de {loteria.upper()}.")
            return False

        todos = [resumir(item) for item in completos]
        todos = deduplicar_por_concurso(todos)
        if not salvar_json(caminho_todos, todos):
            return False
        log.info(f"💾 Histórico reconstruído: {len(todos)} concursos.")
    else:
        todos = deduplicar_por_concurso([{**i, "concurso": numero_concurso(i.get("concurso"))} for i in todos if isinstance(i, dict)])

    ultimo_salvo = max((numero_concurso(item.get("concurso")) for item in todos), default=0)
    log.info(f"💾 Último concurso no histórico local: #{ultimo_salvo}")

    if concurso_atual > ultimo_salvo:
        log.info(f"⚡ {concurso_atual - ultimo_salvo} concurso(s) novo(s) encontrado(s).")
        novos = buscar_novos_concursos(loteria, ultimo_salvo, concurso_atual)
        if novos:
            todos.extend([resumir(item) for item in novos])
            todos = deduplicar_por_concurso(todos)
            if not salvar_json(caminho_todos, todos):
                log.error(f"❌ Falha ao salvar histórico de {loteria.upper()}.")
                return False
            log.info(f"✅ Histórico atualizado com {len(novos)} novo(s).")

    # Passo 5: Sincronização dos Últimos 10 (Com Mesclagem Inteligente)
    if VALIDAR_10_NA_CAIXA:
        ultimos_novos = obter_ultimos_10_robusto(loteria, concurso_atual)
        
        # Junta os dados recém-baixados com o cache antigo. 
        # Garante que concursos não obtidos por instabilidade sejam preservados do cache.
        mesclado = ultimos_novos + ultimos_existentes
        
        # Deduplica (mantendo sempre a versão mais recente) e corta nos 10
        ultimos_finais = deduplicar_por_concurso(mesclado)[:ULTIMOS_QTD]
    else:
        ultimos_finais = [dict(item) for item in todos[:ULTIMOS_QTD]]

    ultimos_finais = ordenar_por_concurso(ultimos_finais)[:ULTIMOS_QTD]
    if not ultimos_finais:
        log.error(f"❌ Falha ao produzir {loteria}_ultimos_10.json.")
        return False

    if not salvar_json(caminho_ultimos, ultimos_finais, indent=2):
        log.error(f"❌ Falha ao salvar últimos 10 de {loteria.upper()}.")
        return False

    log.info(f"✅ {loteria.upper()} sincronizada com sucesso.")
    return True

# =============================================================================
# RELATÓRIO E MAIN
# =============================================================================

def imprimir_relatorio(resultados: dict[str, bool]) -> None:
    log.info("")
    log.info("=" * 70)
    log.info("📋 RELATÓRIO FINAL — MULTIFONTES")
    log.info("=" * 70)

    sucesso = [l for l, r in resultados.items() if r]
    falhas = [l for l, r in resultados.items() if not r]

    for loteria in sucesso:
        ultimos = ler_json(os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json"))
        numeros = [numero_concurso(item.get("concurso")) for item in ultimos]
        log.info(f"✅ {loteria.upper():<18} → últimos: {numeros}")

    for loteria in falhas:
        log.error(f"❌ {loteria.upper():<18} → falha na sincronização")

    log.info("")
    log.info(f"Sucesso: {len(sucesso)}/{len(resultados)}")
    log.info(f"Concluído em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")

def main() -> None:
    log.info("")
    log.info("=" * 70)
    log.info("🚀 LotoLab Brasil — Sincronização Robusta Multi-Fontes")
    log.info("=" * 70)

    resultados: dict[str, bool] = {}
    for loteria in LOTERIAS:
        try:
            resultados[loteria] = process_loteria(loteria)
        except Exception as erro:
            log.exception(f"💥 Erro inesperado em {loteria.upper()}: {erro}")
            resultados[loteria] = False
        time.sleep(0.5)

    imprimir_relatorio(resultados)

if __name__ == "__main__":
    main()
