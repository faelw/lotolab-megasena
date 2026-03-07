import requests
import json
import os
import time
import urllib3

# Desativa avisos de segurança SSL (O certificado do Governo/Caixa às vezes dá conflito no Python)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

LOTERIAS = [
    'megasena', 
    'quina', 
    'lotomania', 
    'timemania', 
    'duplasena', 
    'diadesorte', 
    'supersete', 
    'maismilionaria',
    'loteca' 
]

# Mudamos para a API OFICIAL DA CAIXA
BASE_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api"
OUTPUT_DIR = "dados_loterias"

# Simula um navegador para a Caixa não bloquear o nosso script
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_concurso_caixa(loteria, concurso=""):
    url = f"{BASE_URL}/{loteria}/{concurso}" if concurso else f"{BASE_URL}/{loteria}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Erro ao buscar {loteria} ({concurso}): {e}")
    return None

def process_loteria(loteria):
    print(f"\n🚀 Sincronizando {loteria.upper()}...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    caminho_todos = os.path.join(OUTPUT_DIR, f"{loteria}_todos.json")
    
    # 1. Carrega os dados antigos
    dados_existentes = []
    if os.path.exists(caminho_todos):
        with open(caminho_todos, 'r', encoding='utf-8') as f:
            try:
                dados_existentes = json.load(f)
            except json.JSONDecodeError:
                dados_existentes = []

    # 2. Descobre o último concurso que você já tem salvo
    ultimo_salvo = 0
    if dados_existentes:
        ultimo_salvo = max([int(jogo.get("concurso", 0)) for jogo in dados_existentes])
    print(f"Último salvo localmente: {ultimo_salvo}")

    # 3. Consulta a Caixa para ver qual é o resultado mais recente
    resultado_recente = fetch_concurso_caixa(loteria)
    if not resultado_recente:
        print(f"Falha ao conectar na Caixa para {loteria}.")
        return

    # A API da caixa usa 'numero' em vez de 'concurso'
    concurso_atual_caixa = resultado_recente.get("numero", 0)
    print(f"Mais recente na Caixa: {concurso_atual_caixa}")

    if ultimo_salvo >= concurso_atual_caixa:
        print("✅ Já está 100% atualizado!")
        return

    # Se for a primeira vez rodando (sem arquivo), baixa apenas os últimos 20 para não travar
    if ultimo_salvo == 0:
        ultimo_salvo = max(1, concurso_atual_caixa - 20)
        print("Criando base inicial. Baixando apenas os últimos 20...")

    # 4. Baixa apenas os concursos que estão faltando
    novos_jogos = []
    for num in range(ultimo_salvo + 1, concurso_atual_caixa + 1):
        print(f"Baixando concurso faltando: {num}...")
        dados_falta = fetch_concurso_caixa(loteria, num)
        
        if dados_falta:
            # Traduz o formato da Caixa para o formato do seu App
            jogo_formatado = {
                "concurso": dados_falta.get("numero"),
                "data": dados_falta.get("dataApuracao"),
                # A Loteca não retorna dezenas, por isso usamos o .get com lista vazia de fallback
                "dezenas": dados_falta.get("listaDezenas", [])
            }
            novos_jogos.append(jogo_formatado)
            
        time.sleep(0.3) # Pequena pausa pro Firewall da Caixa não nos banir

    # 5. Junta os novos dados com os antigos
    dados_existentes.extend(novos_jogos)
    
    # Ordena do mais recente para o mais antigo (como o seu app exige)
    dados_ordenados = sorted(dados_existentes, key=lambda x: int(x.get('concurso', 0)), reverse=True)

    # 6. Salva os arquivos JSON
    ultimos_10 = dados_ordenados[:10]
    caminho_ultimos = os.path.join(OUTPUT_DIR, f"{loteria}_ultimos_10.json")
    
    with open(caminho_ultimos, 'w', encoding='utf-8') as f:
        json.dump(ultimos_10, f, ensure_ascii=False, indent=2)

    with open(caminho_todos, 'w', encoding='utf-8') as f:
        json.dump(dados_ordenados, f, ensure_ascii=False)

    print(f"✅ {loteria.upper()} sincronizada com sucesso! (+{len(novos_jogos)} novos jogos)")

def main():
    for loteria in LOTERIAS:
        process_loteria(loteria)

if __name__ == "__main__":
    main()
