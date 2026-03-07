import requests
import json
import os

# Lista com todas as outras loterias do Brasil (exatamente como no App)
LOTERIAS = [
    'megasena', 
    'quina', 
    'lotomania', 
    'timemania', 
    'duplasena', 
    'diadesorte', 
    'supersete', 
    'maismilionaria',
    'loteca' # <-- ADICIONAMOS A LOTECA AQUI!
]

BASE_URL = "https://loteriascaixa-api.herokuapp.com/api"
OUTPUT_DIR = "dados_loterias"

def fetch_data(loteria):
    url = f"{BASE_URL}/{loteria}"
    try:
        response = requests.get(url, timeout=20) # Timeout alto para aguentar as 9 loterias
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Erro ao buscar dados da {loteria}: {e}")
        return None

def process_loteria(loteria):
    print(f"Processando {loteria.upper()}...")
    dados = fetch_data(loteria)
    
    if not dados:
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Ordena do mais recente para o mais antigo (garantia de consistência)
    dados_ordenados = sorted(dados, key=lambda x: x.get('concurso', 0), reverse=True)
    
    # 1. Os 10 últimos com rateio completo
    ultimos_10 = dados_ordenados[:10]

    # 2. Resumo de todos os concursos para o banco interno e backtest
    todos_resumido = []
    for concurso in dados_ordenados:
        todos_resumido.append({
            "concurso": concurso.get("concurso"),
            "data": concurso.get("data"),
            "dezenas": concurso.get("dezenas")
        })

    # 3. Salvar os arquivos JSON
    caminho_ultimos = os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json")
    caminho_todos = os.path.join(OUTPUT_DIR, f"{loteria}_todos.json")

    with open(caminho_ultimos, 'w', encoding='utf-8') as f:
        json.dump(ultimos_10, f, ensure_ascii=False, indent=2)

    with open(caminho_todos, 'w', encoding='utf-8') as f:
        json.dump(todos_resumido, f, ensure_ascii=False)

    print(f"✅ {loteria.upper()} salva com sucesso!")

def main():
    for loteria in LOTERIAS:
        process_loteria(loteria)

if __name__ == "__main__":
    main()
