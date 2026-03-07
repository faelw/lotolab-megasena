import requests
import json
import os
import time
import urllib3

# Evita avisos de segurança SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOTERIAS = [
    'megasena', 'quina', 'lotomania', 'timemania', 
    'duplasena', 'diadesorte', 'supersete', 'maismilionaria', 'loteca'
]

HEROKU_URL = "https://loteriascaixa-api.herokuapp.com/api"
CAIXA_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api"
OUTPUT_DIR = "dados_loterias"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_heroku(loteria):
    """Tenta buscar todos os dados de uma vez na API antiga (Heroku)."""
    try:
        response = requests.get(f"{HEROKU_URL}/{loteria}", timeout=10)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def fetch_concurso_caixa(loteria, concurso=""):
    """Busca um concurso específico (ou o mais recente) na API Oficial da Caixa."""
    url = f"{CAIXA_URL}/{loteria}/{concurso}" if concurso else f"{CAIXA_URL}/{loteria}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return None

def formatar_para_padrao_antigo(dados_caixa, loteria):
    """Traduz o JSON da Caixa para o formato exato que o seu Flutter (LotoLab) lê."""
    if not dados_caixa: return None
    
    premiacoes = []
    for p in dados_caixa.get("listaRateioPremio", []):
        premiacoes.append({
            "descricao": p.get("descricaoFaixa", ""),
            "faixa": p.get("faixa", 0),
            "ganhadores": p.get("numeroDeGanhadores", 0),
            "valorPremio": p.get("valorPremio", 0.0)
        })
        
    local_ganhadores = []
    for g in dados_caixa.get("listaMunicipioUFGanhadores", []):
        local_ganhadores.append({
            "ganhadores": g.get("ganhadores", 1),
            "municipio": g.get("municipio", ""),
            "nomeFatansiaUL": "", "serie": "",
            "posicao": g.get("posicao", 1),
            "uf": g.get("uf", "")
        })
        
    local_str = dados_caixa.get("localSorteio", "")
    mun_str = dados_caixa.get("nomeMunicipioUFSorteio", "")
    local_completo = f"{local_str} em {mun_str}" if local_str and mun_str else local_str

    dezenas = dados_caixa.get("listaDezenas", [])

    return {
        "loteria": loteria,
        "concurso": dados_caixa.get("numero"),
        "data": dados_caixa.get("dataApuracao"),
        "local": local_completo,
        "concursoEspecial": dados_caixa.get("indicadorConcursoEspecial") == 1,
        "dezenasOrdemSorteio": dezenas, 
        "dezenas": sorted(dezenas) if dezenas else [],
        "trevos": dados_caixa.get("trevos", []),
        "timeCoracao": dados_caixa.get("nomeTimeCoracao", None),
        "mesSorte": dados_caixa.get("mesSorte", None),
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
        "valorEstimadoProximoConcurso": dados_caixa.get("valorEstimadoProximoConcurso", 0.0)
    }

def process_loteria(loteria):
    print(f"\n🔄 Sincronizando {loteria.upper()}...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    caminho_todos = os.path.join(OUTPUT_DIR, f"{loteria}_todos.json")
    caminho_ultimos = os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json")
    
    # 1. Lê o que já temos salvo localmente
    todos_resumido = []
    if os.path.exists(caminho_todos):
        with open(caminho_todos, 'r', encoding='utf-8') as f:
            try: todos_resumido = json.load(f)
            except: pass
            
    ultimo_salvo = 0
    if todos_resumido:
        ultimo_salvo = max([int(j.get("concurso", 0)) for j in todos_resumido])
    
    novos_completos = []

    # 2. TENTA O HEROKU PRIMEIRO (Para pegar em lote, se for o caso)
    dados_heroku = fetch_heroku(loteria)
    if dados_heroku:
        # Filtra apenas os concursos que o Heroku tem e nós ainda não temos
        novos_do_heroku = [j for j in dados_heroku if int(j.get("concurso", 0)) > ultimo_salvo]
        if novos_do_heroku:
            # O Heroku já vem no formato certo, então é só adicionar
            novos_completos.extend(novos_do_heroku)
            ultimo_salvo = max([int(j.get("concurso", 0)) for j in novos_do_heroku])
            print(f"📥 Heroku trouxe {len(novos_do_heroku)} concursos novos.")

    # 3. VERIFICA A CAIXA (Para ver se o Heroku está atrasado)
    resultado_recente_caixa = fetch_concurso_caixa(loteria)
    if resultado_recente_caixa:
        concurso_atual_caixa = resultado_recente_caixa.get("numero", 0)
        
        if concurso_atual_caixa > ultimo_salvo:
            print(f"⚡ Caixa tem atualização! Faltam do {ultimo_salvo + 1} ao {concurso_atual_caixa}.")
            
            # Se a base estiver vazia e o Heroku falhou, puxa só os últimos 20 para não travar
            if ultimo_salvo == 0:
                ultimo_salvo = max(1, concurso_atual_caixa - 20)
            
            for num in range(ultimo_salvo + 1, concurso_atual_caixa + 1):
                print(f"Baixando {num} da Caixa...")
                dados_brutos = fetch_concurso_caixa(loteria, num)
                if dados_brutos:
                    formatado = formatar_para_padrao_antigo(dados_brutos, loteria)
                    novos_completos.append(formatado)
                time.sleep(0.3)
        else:
            print("✅ 100% Atualizado.")
    else:
        if not novos_completos:
            print("⚠️ Falha ao conectar em ambas as APIs.")

    # 4. SALVAR ARQUIVOS SE HOUVER NOVIDADES
    if novos_completos:
        # Salva o resumo (_todos.json)
        novos_resumidos = [{"concurso": j["concurso"], "data": j["data"], "dezenas": j["dezenas"]} for j in novos_completos]
        todos_resumido.extend(novos_resumidos)
        todos_ordenados = sorted(todos_resumido, key=lambda x: int(x.get("concurso", 0)), reverse=True)
        with open(caminho_todos, 'w', encoding='utf-8') as f:
            json.dump(todos_ordenados, f, ensure_ascii=False)
            
        # Salva os detalhes (_ultimos_10.json)
        ultimos_10 = []
        if os.path.exists(caminho_ultimos):
            with open(caminho_ultimos, 'r', encoding='utf-8') as f:
                try: ultimos_10 = json.load(f)
                except: pass
                
        ultimos_10.extend(novos_completos)
        ultimos_10 = sorted(ultimos_10, key=lambda x: int(x.get("concurso", 0)), reverse=True)[:10]
        
        with open(caminho_ultimos, 'w', encoding='utf-8') as f:
            json.dump(ultimos_10, f, ensure_ascii=False, indent=2)
            
        print(f"💾 Foram salvos {len(novos_completos)} concursos.")

def main():
    for loteria in LOTERIAS:
        process_loteria(loteria)

if __name__ == "__main__":
    main()
