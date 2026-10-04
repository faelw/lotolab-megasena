"""
===============================================================================
SYNC LOTERIAS — LotoLab Brasil (Versão Definitiva: Injeção Direta Anti-Block)
Script Completo: Setup, Normalização (Correção Super Sete e +Milionária) e Core
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
    "megasena", "quina", "lotomania", "timemania", "duplasena",
    "diadesorte", "supersete", "maismilionaria", "loteca",
]

CAIXA_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api"
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
DELAY_ENTRE_REQ = 0.50

ULTIMOS_QTD = 10
VALIDAR_10_NA_CAIXA = True
USAR_FONTE_LOTE = True

# =============================================================================
# LOG
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("sync_loterias.log", encoding="utf-8")],
)
log = logging.getLogger(__name__)

# =============================================================================
# UTILITÁRIOS
# =============================================================================

def numero_concurso(valor: Any) -> int:
    try:
        return 0 if valor is None else int(str(valor).strip())
    except (TypeError, ValueError):
        return 0

def ordenar_por_concurso(dados: list[dict]) -> list[dict]:
    return sorted(dados, key=lambda x: numero_concurso(x.get("concurso")), reverse=True)

def deduplicar_por_concurso(dados: list[dict]) -> list[dict]:
    resultado, vistos = [], set()
    for item in ordenar_por_concurso(dados):
        n = numero_concurso(item.get("concurso"))
        if n > 0 and n not in vistos:
            vistos.add(n)
            resultado.append(item)
    return resultado

def ler_json(caminho: str) -> list:
    if not os.path.exists(caminho) or os.path.getsize(caminho) == 0:
        return []
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        return dados if isinstance(dados, list) else []
    except (OSError, json.JSONDecodeError):
        return []

def salvar_json(caminho: str, dados: list, indent: int | None = None) -> bool:
    os.makedirs(os.path.dirname(caminho) or OUTPUT_DIR, exist_ok=True)
    temporario = caminho + ".tmp"
    try:
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=indent)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
        return True
    except Exception as erro:
        log.error(f"❌ Erro ao salvar {caminho}: {erro}")
        return False

# =============================================================================
# HTTP, REQUIÇÕES E NORMALIZAÇÃO DE DADOS
# =============================================================================

def get_com_retry(url: str, verify: bool = True) -> Any | None:
    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            resposta = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, verify=verify)
            if resposta.status_code == 200:
                return resposta.json()
            log.warning(f"⚠️ HTTP {resposta.status_code} em {url} (tentativa {tentativa}/{MAX_RETRIES})")
        except Exception as erro:
            log.warning(f"⚠️ Erro em {url}: {erro} (tentativa {tentativa}/{MAX_RETRIES})")
        if tentativa < MAX_RETRIES:
            time.sleep(RETRY_DELAY * tentativa)
    return None

def fetch_caixa(loteria: str, concurso: int | str = "") -> dict | None:
    url = f"{CAIXA_URL}/{loteria}" if concurso == "" else f"{CAIXA_URL}/{loteria}/{concurso}"
    dados = get_com_retry(url, verify=False)
    return dados if isinstance(dados, dict) else None

def fetch_lote(loteria: str) -> list[dict] | None:
    dados = get_com_retry(f"{HEROKU_URL}/{loteria}", verify=True)
    if not isinstance(dados, list): return None
    return [i for i in dados if isinstance(i, dict) and numero_concurso(i.get("concurso") or i.get("numero")) > 0]

def formatar_caixa(dados_caixa: dict, loteria: str) -> dict | None:
    if not isinstance(dados_caixa, dict): return None
    concurso = numero_concurso(dados_caixa.get("numero"))
    if concurso <= 0: return None

    premiacoes = [{
        "descricao": p.get("descricaoFaixa", ""), "faixa": p.get("faixa", 0),
        "ganhadores": p.get("numeroDeGanhadores", 0), "valorPremio": p.get("valorPremio", 0.0)
    } for p in (dados_caixa.get("listaRateioPremio") or []) if isinstance(p, dict)]

    locais = [{
        "ganhadores": g.get("ganhadores", 1), "municipio": g.get("municipio", ""),
        "nomeFatansiaUL": "", "serie": "", "posicao": g.get("posicao", 1), "uf": g.get("uf", "")
    } for g in (dados_caixa.get("listaMunicipioUFGanhadores") or []) if isinstance(g, dict)]

    loc_str, mun_str = dados_caixa.get("localSorteio", ""), dados_caixa.get("nomeMunicipioUFSorteio", "")
    dezenas = [str(d).zfill(2) for d in (dados_caixa.get("listaDezenas") or [])]
    
    # CORREÇÃO TREVOS: A Caixa utiliza 'listaDezenasTrevos' para a +Milionária
    trevos_brutos = dados_caixa.get("trevos") or dados_caixa.get("listaDezenasTrevos") or []
    trevos = [str(t).zfill(2) for t in trevos_brutos]

    return {
        "loteria": loteria, "concurso": concurso, "data": dados_caixa.get("dataApuracao", ""),
        "local": f"{loc_str} em {mun_str}" if loc_str and mun_str else loc_str,
        "concursoEspecial": dados_caixa.get("indicadorConcursoEspecial") == 1,
        "dezenasOrdemSorteio": dezenas, 
        "dezenas": dezenas if loteria == "supersete" else sorted(dezenas), # CORREÇÃO SUPER SETE
        "trevos": trevos,
        "timeCoracao": dados_caixa.get("nomeTimeCoracao"),
        "mesSorte": dados_caixa.get("mesSorte"), "premiacoes": premiacoes,
        "estadosPremiados": [], "observacao": dados_caixa.get("observacao", ""),
        "acumulou": dados_caixa.get("acumulado", False),
        "proximoConcurso": dados_caixa.get("numeroConcursoProximo", 0),
        "dataProximoConcurso": dados_caixa.get("dataProximoConcurso", ""),
        "localGanhadores": locais, "valorArrecadado": dados_caixa.get("valorArrecadado", 0.0),
        "valorAcumuladoConcurso_0_5": dados_caixa.get("valorAcumuladoConcurso_0_5", 0.0),
        "valorAcumuladoConcursoEspecial": dados_caixa.get("valorAcumuladoConcursoEspecial", 0.0),
        "valorAcumuladoProximoConcurso": dados_caixa.get("valorAcumuladoProximoConcurso", 0.0),
        "valorEstimadoProximoConcurso": dados_caixa.get("valorEstimadoProximoConcurso", 0.0),
    }

def normalizar_item_generico(item: dict, loteria: str) -> dict | None:
    if not isinstance(item, dict): return None
    concurso = numero_concurso(item.get("concurso") or item.get("numero") or item.get("numeroConcurso"))
    if concurso <= 0: return None
    
    copia = dict(item)
    copia["concurso"] = concurso
    copia["loteria"] = copia.get("loteria") or loteria
    
    dez = copia.get("dezenas") or copia.get("listaDezenas") or []
    dez_str = [str(d).zfill(2) for d in dez] if isinstance(dez, list) else []
    
    # CORREÇÃO SUPER SETE
    copia["dezenas"] = dez_str if loteria == "supersete" else sorted(dez_str)
    
    ordem = copia.get("dezenasOrdemSorteio")
    copia["dezenasOrdemSorteio"] = [str(d).zfill(2) for d in ordem] if isinstance(ordem, list) else copia["dezenas"]
    
    # CORREÇÃO TREVOS: Garante extração robusta se usar a API de backup (Heroku)
    trv = copia.get("trevos") or copia.get("listaDezenasTrevos") or copia.get("trevosSorteados") or []
    copia["trevos"] = [str(t).zfill(2) for t in trv] if isinstance(trv, list) else []
    
    return copia

def resumir(jogo: dict) -> dict:
    return {"concurso": numero_concurso(jogo.get("concurso")), "data": jogo.get("data", ""), "dezenas": jogo.get("dezenas", [])}

def obter_concurso_atual(loteria: str) -> tuple[int, dict | None]:
    log.info("📡 Buscando concurso oficial na Caixa...")
    dados = fetch_caixa(loteria)
    if dados:
        fmt = formatar_caixa(dados, loteria)
        if fmt and fmt["concurso"] > 0: return fmt["concurso"], fmt

    log.warning("⚠️ Caixa falhou. Tentando Lote Heroku...")
    dados_lote = fetch_lote(loteria)
    if dados_lote:
        ordenado = ordenar_por_concurso(dados_lote)
        if ordenado:
            fmt = normalizar_item_generico(ordenado[0], loteria)
            if fmt and fmt["concurso"] > 0: return fmt["concurso"], fmt
    return 0, None

# =============================================================================
# LÓGICA CORE, INJEÇÃO DIRETA E MAIN
# =============================================================================

def reconstruir_completo(loteria: str, concurso_mais_recente: int) -> list[dict]:
    log.info(f"🔁 Reconstruindo {loteria.upper()} do 1 ao #{concurso_mais_recente}...")
    completos, ids_obtidos = [], set()

    if USAR_FONTE_LOTE:
        dados_lote = fetch_lote(loteria)
        if dados_lote:
            for item in dados_lote:
                nrm = normalizar_item_generico(item, loteria)
                if nrm and 0 < nrm["concurso"] <= concurso_mais_recente and nrm["concurso"] not in ids_obtidos:
                    completos.append(nrm); ids_obtidos.add(nrm["concurso"])

    faltando = [n for n in range(1, concurso_mais_recente + 1) if n not in ids_obtidos]
    for i, numero in enumerate(faltando, 1):
        dados = fetch_caixa(loteria, numero)
        if dados:
            fmt = formatar_caixa(dados, loteria)
            if fmt and fmt["concurso"] > 0:
                completos.append(fmt); ids_obtidos.add(fmt["concurso"])
        if i % 50 == 0: log.info(f"   ... {i}/{len(faltando)}")
        time.sleep(DELAY_ENTRE_REQ)
    return deduplicar_por_concurso(completos)

def buscar_lacuna(loteria: str, ultimo_salvo: int, alvo: int) -> list[dict]:
    novos, ids = [], set()
    faltando = [n for n in range(ultimo_salvo + 1, alvo + 1)]
    for numero in faltando:
        dados = fetch_caixa(loteria, numero)
        if dados:
            fmt = formatar_caixa(dados, loteria)
            if fmt and fmt["concurso"] > 0: novos.append(fmt)
        time.sleep(DELAY_ENTRE_REQ)
    return novos

def process_loteria(loteria: str) -> bool:
    log.info(f"\n{'='*70}\n🔄 SINCRONIZANDO {loteria.upper()}\n{'='*70}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    cam_todos, cam_ultimos = os.path.join(OUTPUT_DIR, f"{loteria}_todos.json"), os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json")

    # Passo 1: Pegamos o dado mais fresco existente (Injeção Direta)
    concurso_atual, dados_recente = obter_concurso_atual(loteria)
    if concurso_atual <= 0 or not dados_recente:
        log.error(f"❌ Falha fatal: Impossível obter concurso atual de {loteria.upper()}.")
        return False

    todos, ultimos_existentes = ler_json(cam_todos), ler_json(cam_ultimos)

    if not todos:
        completos = reconstruir_completo(loteria, concurso_atual)
        todos = deduplicar_por_concurso([resumir(item) for item in completos])
    else:
        todos = deduplicar_por_concurso([{**i, "concurso": numero_concurso(i.get("concurso"))} for i in todos if isinstance(i, dict)])

    ultimo_salvo = max((numero_concurso(item.get("concurso")) for item in todos), default=0)
    
    # Passo 2: Atualização do Histórico (_todos.json)
    if concurso_atual > ultimo_salvo:
        log.info(f"⚡ Novo concurso: #{ultimo_salvo} -> #{concurso_atual}")
        novos = [dados_recente] # INJEÇÃO DIRETA GARANTIDA
        
        # Se perdeu mais de 1 dia, busca a lacuna, mas mantem o atual salvo!
        if concurso_atual - ultimo_salvo > 1:
            log.info(f"🔍 Buscando lacuna de {concurso_atual - ultimo_salvo - 1} concurso(s)...")
            novos.extend(buscar_lacuna(loteria, ultimo_salvo, concurso_atual - 1))
            
        todos.extend([resumir(item) for item in novos])
        todos = deduplicar_por_concurso(todos)
        if salvar_json(cam_todos, todos):
            log.info("✅ Histórico (_todos.json) atualizado com sucesso.")
    else:
        log.info("✅ Histórico já possui o concurso atual.")

    # Passo 3: Atualização dos 10 Últimos (Bypass Anti-Bot)
    ultimos_novos = []
    if VALIDAR_10_NA_CAIXA:
        inicio = max(1, concurso_atual - ULTIMOS_QTD + 1)
        # Não busca o atual de novo! Já temos ele na memória (dados_recente). Busca só os outros 9.
        for numero in range(concurso_atual - 1, inicio - 1, -1):
            dados = fetch_caixa(loteria, numero)
            if dados:
                fmt = formatar_caixa(dados, loteria)
                if fmt: ultimos_novos.append(fmt)
            time.sleep(DELAY_ENTRE_REQ)

    # A MÁGICA FINAL: Injeta o `dados_recente` à força no topo, junta com o que conseguiu baixar,
    # e por fim preenche o que faltar com o cache antigo. 
    mesclado = [dados_recente] + ultimos_novos + ultimos_existentes
    ultimos_finais = deduplicar_por_concurso(mesclado)[:ULTIMOS_QTD]

    if salvar_json(cam_ultimos, ultimos_finais, indent=2):
        log.info(f"✅ Últimos 10 atualizados. Topo: #{ultimos_finais[0]['concurso']}")
        return True
    return False

def main() -> None:
    log.info(f"\n{'='*70}\n🚀 LotoLab Brasil — Sincronização Injeção Direta\n{'='*70}")
    resultados = {loteria: False for loteria in LOTERIAS}
    for loteria in LOTERIAS:
        try:
            resultados[loteria] = process_loteria(loteria)
        except Exception as erro:
            log.exception(f"💥 Erro inesperado em {loteria.upper()}: {erro}")
        time.sleep(0.5)

    log.info(f"\n{'='*70}\n📋 RELATÓRIO FINAL\n{'='*70}")
    for loteria, sucesso in resultados.items():
        if sucesso:
            ultimos = ler_json(os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json"))
            log.info(f"✅ {loteria.upper():<18} → topo: #{numero_concurso(ultimos[0].get('concurso')) if ultimos else 'N/A'}")
        else:
            log.error(f"❌ {loteria.upper():<18} → falha na sincronização")

if __name__ == "__main__":
    main()
