#!/usr/bin/env python3
"""
Script de teste para validar a integração com persistência no Supabase.
"""

import asyncio
import json
import os
import requests
from datetime import datetime

# Configurações para teste
API_BASE_URL = "http://localhost:8000"
API_KEY = os.getenv("API_KEY", "test-key-123")

# Dados de exemplo para teste
TEST_MEETING_SESSION_ID = "meeting-test-123"
TEST_TEAMS_URL = "https://teams.microsoft.com/l/meetup-join/test"

async def test_persistence_integration():
    """
    Testa a integração completa do sistema de persistência.
    """
    print("🧪 Testando integração com persistência Supabase...")
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 1. Testar criação de sessão com persistência
    print("\n1️⃣ Testando criação de sessão com persistência...")
    
    session_data = {
        "meeting_session_id": TEST_MEETING_SESSION_ID,
        "teams_url": TEST_TEAMS_URL,
        "segment_time": 30,
        "upload_dest": "test-segments",
        "record_video": False
    }
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/feedback/start",
            headers=headers,
            json=session_data,
            timeout=10
        )
        
        if response.status_code == 200:
            result = response.json()
            session_id = result.get("session_id")
            recording_session_id = result.get("recording_session_id")
            
            print(f"✅ Sessão criada com sucesso!")
            print(f"   Session ID: {session_id}")
            print(f"   Recording Session ID: {recording_session_id}")
            print(f"   Meeting Session ID: {result.get('meeting_session_id')}")
            print(f"   Status: {result.get('status')}")
            print(f"   Mensagem: {result.get('message')}")
            
            # 2. Testar consulta dos dados da sessão
            print(f"\n2️⃣ Testando consulta dos dados da sessão...")
            await asyncio.sleep(2)  # Dar tempo para persistir
            
            session_response = requests.get(
                f"{API_BASE_URL}/api/feedback/session/{session_id}/recording",
                headers=headers,
                timeout=10
            )
            
            if session_response.status_code == 200:
                session_data = session_response.json()
                print(f"✅ Dados da sessão recuperados:")
                print(f"   Recording Session ID: {session_data.get('recording_session_id')}")
                print(f"   Meeting Session ID: {session_data.get('meeting_session_id')}")
                print(f"   Status: {session_data.get('status')}")
                print(f"   Teams URL: {session_data.get('teams_url')}")
                print(f"   Criado em: {session_data.get('created_at')}")
            else:
                print(f"❌ Erro ao consultar dados da sessão: {session_response.status_code}")
                print(f"   Resposta: {session_response.text}")
            
            # 3. Testar consulta por meeting_session_id
            print(f"\n3️⃣ Testando consulta por meeting_session_id...")
            
            meeting_response = requests.get(
                f"{API_BASE_URL}/api/feedback/meeting/{TEST_MEETING_SESSION_ID}/recordings",
                headers=headers,
                timeout=10
            )
            
            if meeting_response.status_code == 200:
                meeting_data = meeting_response.json()
                print(f"✅ Gravações da reunião recuperadas:")
                print(f"   Meeting Session ID: {meeting_data.get('meeting_session_id')}")
                print(f"   Total de gravações: {meeting_data.get('total')}")
                
                recordings = meeting_data.get('recordings', [])
                for i, recording in enumerate(recordings):
                    print(f"   Gravação {i+1}: ID={recording.get('id')}, Status={recording.get('status')}")
            else:
                print(f"❌ Erro ao consultar gravações da reunião: {meeting_response.status_code}")
                print(f"   Resposta: {meeting_response.text}")
            
            # 4. Parar a sessão para testar cleanup
            print(f"\n4️⃣ Testando parada da sessão...")
            await asyncio.sleep(1)
            
            stop_response = requests.delete(
                f"{API_BASE_URL}/api/feedback/session/{session_id}",
                headers=headers,
                timeout=10
            )
            
            if stop_response.status_code == 200:
                stop_data = stop_response.json()
                print(f"✅ Sessão parada com sucesso:")
                print(f"   Mensagem: {stop_data.get('message')}")
                print(f"   Status: {stop_data.get('status')}")
            else:
                print(f"❌ Erro ao parar sessão: {stop_response.status_code}")
                print(f"   Resposta: {stop_response.text}")
            
        else:
            print(f"❌ Erro ao criar sessão: {response.status_code}")
            print(f"   Resposta: {response.text}")
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Erro de conexão: {e}")
        print("   Certifique-se de que o servidor está rodando em http://localhost:8000")
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")

def test_database_connection():
    """
    Testa a conexão com o banco de dados.
    """
    print("\n🔌 Testando conexão com banco de dados...")
    
    required_env_vars = [
        "SUPABASE_DB_HOST",
        "SUPABASE_DB_PORT", 
        "SUPABASE_DB_NAME",
        "SUPABASE_DB_USER",
        "SUPABASE_DB_PASSWORD"
    ]
    
    missing_vars = []
    for var in required_env_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Variáveis de ambiente faltando: {', '.join(missing_vars)}")
        print("   Configure as variáveis de ambiente do Supabase antes do teste.")
        return False
    else:
        print("✅ Todas as variáveis de ambiente estão configuradas")
        for var in required_env_vars:
            value = os.getenv(var)
            if var == "SUPABASE_DB_PASSWORD":
                value = "*" * len(value) if value else ""
            print(f"   {var}: {value}")
        return True

if __name__ == "__main__":
    print("🚀 Iniciando testes de integração com persistência...")
    print(f"   Data/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   API Base URL: {API_BASE_URL}")
    
    # Testar conexão com banco
    db_ok = test_database_connection()
    
    if db_ok:
        # Executar testes da API
        asyncio.run(test_persistence_integration())
    else:
        print("\n❌ Não foi possível executar os testes sem a configuração do banco.")
    
    print("\n🏁 Testes finalizados!")
