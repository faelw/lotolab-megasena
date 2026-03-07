"""
=============================================================================
SYNC LOTERIAS — LotoLab Brasil
Sincronização confiável com detecção de arquivos vazios/corrompidos
=============================================================================
"""

import requests
import json
import os
import time
import urllib3
import logging
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================
LOTERIAS = [
    'megasena', 'quina', 'lotomania', 'timemania',
    'duplasena', 'diadesorte', 'supersete', 'maismilionaria', 'loteca'
]

HEROKU_URL  = "https://loteriascaixa-api.herokuapp.com/api"
CAIXA_URL   = "https://servicebus2.caixa.gov.br/portaldeloterias/api"
OUTPUT_DIR  = "dados_loterias"
HEADERS     = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Controle de tentativas e timeouts
MAX_RETRIES     = 3
RETRY_DELAY     = 1.5   # segundos entre retries
REQUEST_TIMEOUT = 15
DELAY_ENTRE_REQ = 0.35  # delay entre requests à Caixa para não ser bloqueado

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sync_loterias.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)


# =============================================================================
# VALIDAÇÃO DE ARQUIVOS — detecta vazio, corrompido ou inválido
# =============================================================================
def arquivo_valido(caminho: str, min_itens: int = 1) -> bool:
    """
    Retorna True se o arquivo existir, não estiver vazio, for JSON válido
    e tiver pelo menos min_itens registros.
    """
    if not os.path.exists(caminho):
        return False
    if os.path.getsize(caminho) == 0:
        log.warning(f"⚠️  Arquivo VAZIO detectado: {caminho}")
        return False
    try:
        with open(caminho, 'r', encoding='utf-8') as f:
            dados = json.load(f)
        if not isinstance(dados, list) or len(dados) < min_itens:
            log.warning(f"⚠️  Arquivo inválido ou insuficiente ({len(dados) if isinstance(dados, list) else 'não-lista'}): {caminho}")
            return False
        return True
    except json.JSONDecodeError as e:
        log.warning(f"⚠️  JSON corrompido em {caminho}: {e}")
        return False


def carregar_json_seguro(caminho: str) -> list:
    """Carrega um JSON seguro — retorna [] se inválido."""
    if not arquivo_valido(caminho):
        return []
    with open(caminho, 'r', encoding='utf-8') as f:
        return json.load(f)


def salvar_json(caminho: str, dados: list, indent: int | None = None) -> bool:
    """Salva JSON com verificação pós-gravação."""
    os.makedirs(os.path.dirname(caminho) if os.path.dirname(caminho) else OUTPUT_DIR, exist_ok=True)
    tmp = caminho + ".tmp"
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(dados, f, ensure_ascii=False, indent=indent)
        # Valida antes de substituir o arquivo real
        with open(tmp, 'r', encoding='utf-8') as f:
            json.load(f)
        os.replace(tmp, caminho)
        return True
    except Exception as e:
        log.error(f"❌ Erro ao salvar {caminho}: {e}")
        if os.path.exists(tmp):
            os.remove(tmp)
        return False


# =============================================================================
# REQUESTS COM RETRY
# =============================================================================
def get_com_retry(url: str, params: dict = None, verify: bool = True) -> dict | None:
    """GET com até MAX_RETRIES tentativas e backoff exponencial."""
    for tentativa in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, headers=HEADERS, params=params,
                timeout=REQUEST_TIMEOUT, verify=verify
            )
            if resp.status_code == 200:
                return resp.json()
            log.debug(f"HTTP {resp.status_code} em {url} (tentativa {tentativa})")
        except requests.exceptions.Timeout:
            log.debug(f"Timeout em {url} (tentativa {tentativa})")
        except requests.exceptions.ConnectionError:
            log.debug(f"Sem conexão em {url} (tentativa {tentativa})")
        except Exception as e:
            log.debug(f"Erro em {url}: {e} (tentativa {tentativa})")

        if tentativa < MAX_RETRIES:
            time.sleep(RETRY_DELAY * tentativa)

    return None


def fetch_heroku(loteria: str) -> list | None:
    """Busca todos os concursos em lote no Heroku."""
    dados = get_com_retry(f"{HEROKU_URL}/{loteria}", verify=True)
    if isinstance(dados, list) and len(dados) > 0:
        return dados
    return None


def fetch_caixa(loteria: str, concurso: int | str = "") -> dict | None:
    """Busca concurso específico (ou o mais recente) na API da Caixa."""
    url = f"{CAIXA_URL}/{loteria}/{concurso}" if concurso else f"{CAIXA_URL}/{loteria}"
    return get_com_retry(url, verify=False)


# =============================================================================
# FORMATAÇÃO — Caixa → padrão LotoLab
# =============================================================================
def formatar(dados_caixa: dict, loteria: str) -> dict | None:
    """Converte o JSON da Caixa para o padrão que o Flutter consome."""
    if not dados_caixa or not isinstance(dados_caixa, dict):
        return None

    concurso = dados_caixa.get("numero")
    if not concurso:
        return None

    premiacoes = [
        {
            "descricao": p.get("descricaoFaixa", ""),
            "faixa":     p.get("faixa", 0),
            "ganhadores":p.get("numeroDeGanhadores", 0),
            "valorPremio":p.get("valorPremio", 0.0),
        }
        for p in dados_caixa.get("listaRateioPremio", [])
    ]

    local_ganhadores = [
        {
            "ganhadores":     g.get("ganhadores", 1),
            "municipio":      g.get("municipio", ""),
            "nomeFatansiaUL": "",
            "serie":          "",
            "posicao":        g.get("posicao", 1),
            "uf":             g.get("uf", ""),
        }
        for g in dados_caixa.get("listaMunicipioUFGanhadores", [])
    ]

    local_str = dados_caixa.get("localSorteio", "")
    mun_str   = dados_caixa.get("nomeMunicipioUFSorteio", "")
    local_completo = f"{local_str} em {mun_str}" if local_str and mun_str else local_str

    dezenas = dados_caixa.get("listaDezenas", [])
    dezenas_str = [str(d).zfill(2) for d in dezenas]

    return {
        "loteria":                        loteria,
        "concurso":                       concurso,
        "data":                           dados_caixa.get("dataApuracao", ""),
        "local":                          local_completo,
        "concursoEspecial":               dados_caixa.get("indicadorConcursoEspecial") == 1,
        "dezenasOrdemSorteio":            dezenas_str,
        "dezenas":                        sorted(dezenas_str),
        "trevos":                         dados_caixa.get("trevos", []),
        "timeCoracao":                    dados_caixa.get("nomeTimeCoracao"),
        "mesSorte":                       dados_caixa.get("mesSorte"),
        "premiacoes":                     premiacoes,
        "estadosPremiados":               [],
        "observacao":                     dados_caixa.get("observacao", ""),
        "acumulou":                       dados_caixa.get("acumulado", False),
        "proximoConcurso":                dados_caixa.get("numeroConcursoProximo", 0),
        "dataProximoConcurso":            dados_caixa.get("dataProximoConcurso", ""),
        "localGanhadores":                local_ganhadores,
        "valorArrecadado":                dados_caixa.get("valorArrecadado", 0.0),
        "valorAcumuladoConcurso_0_5":     dados_caixa.get("valorAcumuladoConcurso_0_5", 0.0),
        "valorAcumuladoConcursoEspecial": dados_caixa.get("valorAcumuladoConcursoEspecial", 0.0),
        "valorAcumuladoProximoConcurso":  dados_caixa.get("valorAcumuladoProximoConcurso", 0.0),
        "valorEstimadoProximoConcurso":   dados_caixa.get("valorEstimadoProximoConcurso", 0.0),
    }


def resumir(jogo: dict) -> dict:
    """Extrai apenas os campos necessários para o arquivo _todos.json."""
    return {
        "concurso": jogo.get("concurso"),
        "data":     jogo.get("data", ""),
        "dezenas":  jogo.get("dezenas", []),
    }


# =============================================================================
# RECONSTRUÇÃO COMPLETA — quando arquivo está vazio/corrompido
# =============================================================================
def reconstruir_completo(loteria: str, concurso_mais_recente: int) -> list:
    """
    Baixa do concurso 1 até o mais recente da Caixa.
    Primeiro tenta o Heroku (rápido). Depois complementa com a Caixa.
    """
    log.info(f"🔁 Reconstruindo {loteria.upper()} do zero (concurso 1 → {concurso_mais_recente})...")
    completos = []
    ids_obtidos = set()

    # Tentativa 1: Heroku (todos de uma vez)
    dados_heroku = fetch_heroku(loteria)
    if dados_heroku:
        for j in dados_heroku:
            n = int(j.get("concurso", 0))
            if n > 0:
                completos.append(j)
                ids_obtidos.add(n)
        log.info(f"  📦 Heroku forneceu {len(completos)} concursos.")

    # Tentativa 2: Caixa — completa o que faltou
    faltando = [n for n in range(1, concurso_mais_recente + 1) if n not in ids_obtidos]
    if faltando:
        log.info(f"  🌐 Baixando {len(faltando)} concursos restantes da Caixa...")
        for i, num in enumerate(faltando):
            dados_brutos = fetch_caixa(loteria, num)
            if dados_brutos:
                formatado = formatar(dados_brutos, loteria)
                if formatado:
                    completos.append(formatado)
                    ids_obtidos.add(num)
            if i % 50 == 0 and i > 0:
                log.info(f"    ... {i}/{len(faltando)} baixados")
            time.sleep(DELAY_ENTRE_REQ)

    completos_ordenados = sorted(completos, key=lambda x: int(x.get("concurso", 0)), reverse=True)
    log.info(f"  ✅ Reconstrução concluída: {len(completos_ordenados)} concursos.")
    return completos_ordenados


# =============================================================================
# PROCESSAMENTO PRINCIPAL DE CADA LOTERIA
# =============================================================================
def process_loteria(loteria: str) -> bool:
    log.info(f"\n{'='*55}")
    log.info(f"🔄  Sincronizando {loteria.upper()}")
    log.info(f"{'='*55}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    caminho_todos   = os.path.join(OUTPUT_DIR, f"{loteria}_todos.json")
    caminho_ultimos = os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json")

    # ── PASSO 1: Descobre o concurso mais recente na Caixa ──────────────────
    log.info("📡 Consultando concurso mais recente na Caixa...")
    dados_recente = fetch_caixa(loteria)
    if not dados_recente:
        log.error(f"❌ Não foi possível acessar a Caixa para {loteria}. Abortando.")
        return False

    concurso_caixa = int(dados_recente.get("numero", 0))
    if concurso_caixa == 0:
        log.error(f"❌ Concurso inválido retornado para {loteria}.")
        return False

    log.info(f"   Concurso mais recente: #{concurso_caixa}")

    # ── PASSO 2: Verifica integridade dos arquivos locais ───────────────────
    todos_valido   = arquivo_valido(caminho_todos,   min_itens=1)
    ultimos_valido = arquivo_valido(caminho_ultimos, min_itens=1)

    todos_resumido  = carregar_json_seguro(caminho_todos)
    ultimos_10      = carregar_json_seguro(caminho_ultimos)

    # Detecta se precisa de reconstrução completa
    precisa_reconstruir = (
        not todos_valido or
        not ultimos_valido or
        len(todos_resumido) == 0
    )

    if precisa_reconstruir:
        log.warning(f"⚠️  Arquivo(s) vazio(s) ou corrompido(s) — iniciando reconstrução completa!")
        completos = reconstruir_completo(loteria, concurso_caixa)
        if not completos:
            log.error(f"❌ Reconstrução falhou para {loteria}.")
            return False

        resumidos = [resumir(j) for j in completos]
        salvar_json(caminho_todos, resumidos)
        salvar_json(caminho_ultimos, completos[:10], indent=2)
        log.info(f"💾 Reconstrução salva: {len(resumidos)} resumos + 10 detalhados.")
        return True

    # ── PASSO 3: Atualização incremental ────────────────────────────────────
    ultimo_salvo = max(int(j.get("concurso", 0)) for j in todos_resumido)
    log.info(f"   Último salvo localmente: #{ultimo_salvo}")

    if concurso_caixa <= ultimo_salvo:
        log.info("✅ Dados já estão 100% atualizados.")
        return True

    log.info(f"⚡ {concurso_caixa - ultimo_salvo} concurso(s) novo(s) encontrado(s).")
    novos_completos = []
    novos_ids = set()

    # Tenta o Heroku primeiro (mais rápido)
    dados_heroku = fetch_heroku(loteria)
    if dados_heroku:
        for j in dados_heroku:
            n = int(j.get("concurso", 0))
            if n > ultimo_salvo and n not in novos_ids:
                novos_completos.append(j)
                novos_ids.add(n)
        if novos_completos:
            log.info(f"   📥 Heroku: {len(novos_completos)} concurso(s) novo(s).")

    # Complementa com a Caixa para o que o Heroku não cobriu
    faltando_caixa = [n for n in range(ultimo_salvo + 1, concurso_caixa + 1) if n not in novos_ids]
    if faltando_caixa:
        log.info(f"   🌐 Baixando {len(faltando_caixa)} concurso(s) da Caixa...")
        for num in faltando_caixa:
            dados_brutos = fetch_caixa(loteria, num)
            if dados_brutos:
                formatado = formatar(dados_brutos, loteria)
                if formatado:
                    novos_completos.append(formatado)
                    novos_ids.add(num)
            time.sleep(DELAY_ENTRE_REQ)

    if not novos_completos:
        log.warning(f"⚠️  Nenhum dado novo obtido para {loteria}.")
        return False

    # ── PASSO 4: Mescla e salva ──────────────────────────────────────────────
    novos_resumidos = [resumir(j) for j in novos_completos]
    todos_resumido.extend(novos_resumidos)
    todos_ordenados = sorted(todos_resumido, key=lambda x: int(x.get("concurso", 0)), reverse=True)

    # Remove duplicatas por concurso
    vistos = set()
    todos_sem_dup = []
    for j in todos_ordenados:
        k = int(j.get("concurso", 0))
        if k not in vistos:
            vistos.add(k)
            todos_sem_dup.append(j)

    ultimos_10.extend(novos_completos)
    ultimos_10_ord = sorted(ultimos_10, key=lambda x: int(x.get("concurso", 0)), reverse=True)

    # Remove duplicatas nos últimos 10
    vistos_u = set()
    ultimos_sem_dup = []
    for j in ultimos_10_ord:
        k = int(j.get("concurso", 0))
        if k not in vistos_u:
            vistos_u.add(k)
            ultimos_sem_dup.append(j)

    ok_todos   = salvar_json(caminho_todos, todos_sem_dup)
    ok_ultimos = salvar_json(caminho_ultimos, ultimos_sem_dup[:10], indent=2)

    if ok_todos and ok_ultimos:
        log.info(f"💾 Salvos {len(novos_completos)} novo(s). Total: {len(todos_sem_dup)} concursos.")
        return True
    else:
        log.error(f"❌ Falha ao salvar arquivos de {loteria}.")
        return False


# =============================================================================
# RELATÓRIO FINAL
# =============================================================================
def imprimir_relatorio(resultados: dict[str, bool]) -> None:
    log.info(f"\n{'='*55}")
    log.info("📋  RELATÓRIO FINAL")
    log.info(f"{'='*55}")
    ok  = [l for l, r in resultados.items() if r]
    err = [l for l, r in resultados.items() if not r]
    for l in ok:
        caminho = os.path.join(OUTPUT_DIR, f"{l}_todos.json")
        qtd = len(carregar_json_seguro(caminho))
        log.info(f"  ✅  {l.upper():<18} → {qtd} concursos salvos")
    for l in err:
        log.info(f"  ❌  {l.upper():<18} → falha na sincronização")
    log.info(f"\n  Sucesso: {len(ok)}/{len(resultados)}")
    log.info(f"  Concluído em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")


# =============================================================================
# MAIN
# =============================================================================
def main():
    log.info("🚀 LotoLab Brasil — Sincronização de Dados")
    log.info(f"   Loterias: {', '.join(LOTERIAS)}")
    log.info(f"   Diretório: {os.path.abspath(OUTPUT_DIR)}\n")

    resultados = {}
    for loteria in LOTERIAS:
        try:
            resultados[loteria] = process_loteria(loteria)
        except Exception as e:
            log.exception(f"💥 Erro inesperado em {loteria}: {e}")
            resultados[loteria] = False
        time.sleep(0.5)  # pausa entre loterias

    imprimir_relatorio(resultados)


if __name__ == "__main__":
    main()
