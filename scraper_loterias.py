import requests
import json
import os
import time
import urllib3

# Evita avisos chatos de SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOTERIAS = [
    'megasena', 'quina', 'lotomania', 'timemania', 
    'duplasena', 'diadesorte', 'supersete', 'maismilionaria', 'loteca'
]

BASE_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api"
OUTPUT_DIR = "dados_loterias"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_concurso_caixa(loteria, concurso=""):
    url = f"{BASE_URL}/{loteria}/{concurso}" if concurso else f"{BASE_URL}/{loteria}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Erro ao buscar {loteria}: {e}")
    return None

def formatar_para_padrao_antigo(dados_caixa, loteria):
    """
    Traduz o formato novo da Caixa para o formato antigo do Heroku
    que o LotoLab BR usa para renderizar os cards e prêmios.
    """
    if not dados_caixa: return None
    
    # 1. Ajusta as premiações
    premiacoes = []
    for p in dados_caixa.get("listaRateioPremio", []):
        premiacoes.append({
            "descricao": p.get("descricaoFaixa", ""),
            "faixa": p.get("faixa", 0),
            "ganhadores": p.get("numeroDeGanhadores", 0),
            "valorPremio": p.get("valorPremio", 0.0)
        })
        
    # 2. Ajusta os locais de ganhadores
    local_ganhadores = []
    for g in dados_caixa.get("listaMunicipioUFGanhadores", []):
        local_ganhadores.append({
            "ganhadores": g.get("ganhadores", 1),
            "municipio": g.get("municipio", ""),
            "nomeFatansiaUL": "",
            "serie": "",
            "posicao": g.get("posicao", 1),
            "uf": g.get("uf", "")
        })
        
    # 3. Ajusta o Local do Sorteio
    local_str = dados_caixa.get("localSorteio", "")
    mun_str = dados_caixa.get("nomeMunicipioUFSorteio", "")
    local_completo = f"{local_str} em {mun_str}" if local_str and mun_str else local_str

    dezenas = dados_caixa.get("listaDezenas", [])

    # 4. Retorna a máscara exata que você pediu
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
    print(f"\n🔄 Processando {loteria.upper()}...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    caminho_todos = os.path.join(OUTPUT_DIR, f"{loteria}_todos.json")
    caminho_ultimos = os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json")
    
    # Descobre de onde paramos baseado no _todos.json
    todos_resumido = []
    if os.path.exists(caminho_todos):
        with open(caminho_todos, 'r', encoding='utf-8') as f:
            try: todos_resumido = json.load(f)
            except: pass
            
    ultimo_salvo = 0
    if todos_resumido:
        ultimo_salvo = max([int(j.get("concurso", 0)) for j in todos_resumido])
        
    resultado_recente = fetch_concurso_caixa(loteria)
    if not resultado_recente: return
    
    concurso_atual = resultado_recente.get("numero", 0)
    
    if ultimo_salvo >= concurso_atual:
        print("✅ Já está atualizado.")
        return
        
    if ultimo_salvo == 0:
        ultimo_salvo = max(1, concurso_atual - 20)
        print("Baixando base...")
        
    # Baixa os novos concursos no formato completo
    novos_completos = []
    for num in range(ultimo_salvo + 1, concurso_atual + 1):
        print(f"Baixando: {num}...")
        dados_brutos = fetch_concurso_caixa(loteria, num)
        if dados_brutos:
            formatado = formatar_para_padrao_antigo(dados_brutos, loteria)
            novos_completos.append(formatado)
        time.sleep(0.3) # Evita bloqueio da Caixa
        
    # -- SALVAR ARQUIVO 1: _todos.json (Apenas Resumo para economizar memória) --
    novos_resumidos = [{"concurso": j["concurso"], "data": j["data"], "dezenas": j["dezenas"]} for j in novos_completos]
    todos_resumido.extend(novos_resumidos)
    todos_ordenados = sorted(todos_resumido, key=lambda x: int(x.get("concurso", 0)), reverse=True)
    with open(caminho_todos, 'w', encoding='utf-8') as f:
        json.dump(todos_ordenados, f, ensure_ascii=False)
        
    # -- SALVAR ARQUIVO 2: _ultimos_10.json (Formato Completo Rico) --
    ultimos_10 = []
    if os.path.exists(caminho_ultimos):
        with open(caminho_ultimos, 'r', encoding='utf-8') as f:
            try: ultimos_10 = json.load(f)
            except: pass
            
    ultimos_10.extend(novos_completos)
    ultimos_10 = sorted(ultimos_10, key=lambda x: int(x.get("concurso", 0)), reverse=True)[:10] # Mantém apenas os 10 últimos
    
    with open(caminho_ultimos, 'w', encoding='utf-8') as f:
        json.dump(ultimos_10, f, ensure_ascii=False, indent=2)
        
    print(f"✅ Arquivos atualizados! (+{len(novos_completos)} novos)")

def main():
    for loteria in LOTERIAS:
        process_loteria(loteria)

if __name__ == "__main__":
    main()
