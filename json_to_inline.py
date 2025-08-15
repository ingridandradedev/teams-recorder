#!/usr/bin/env python3
"""
Script para converter arquivo JSON de conta de serviço para formato inline.
Útil para configurar GOOGLE_CREDENTIALS_JSON no Railway, Heroku, etc.
"""

import json
import sys
from pathlib import Path

def convert_json_file_to_inline(file_path: str) -> str:
    """
    Converte um arquivo JSON para uma string inline (sem quebras de linha).
    
    Args:
        file_path: Caminho para o arquivo JSON da conta de serviço
    
    Returns:
        String JSON inline para usar como variável de ambiente
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Converter para string compacta (sem espaços extras)
        inline_json = json.dumps(data, separators=(',', ':'))
        return inline_json
    
    except FileNotFoundError:
        print(f"❌ Arquivo não encontrado: {file_path}")
        return ""
    except json.JSONDecodeError as e:
        print(f"❌ Erro ao decodificar JSON: {e}")
        return ""
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return ""

def main():
    print("🔄 Conversor de JSON para Railway/Heroku")
    print("=" * 50)
    
    if len(sys.argv) != 2:
        print("📝 Uso: python json_to_inline.py <caminho-para-arquivo.json>")
        print()
        print("📋 Exemplo:")
        print("   python json_to_inline.py service-account.json")
        print("   python json_to_inline.py C:\\path\\to\\credentials.json")
        print()
        print("💡 Este script converte um arquivo JSON de conta de serviço")
        print("   para uma string inline que pode ser usada como variável")
        print("   de ambiente GOOGLE_CREDENTIALS_JSON no Railway/Heroku.")
        sys.exit(1)
    
    file_path = sys.argv[1]
    
    if not Path(file_path).exists():
        print(f"❌ Arquivo não encontrado: {file_path}")
        print()
        print("💡 Certifique-se de que:")
        print("   1. O caminho está correto")
        print("   2. O arquivo existe")
        print("   3. Você tem permissão para ler o arquivo")
        sys.exit(1)
    
    print(f"📂 Convertendo arquivo: {file_path}")
    
    inline_json = convert_json_file_to_inline(file_path)
    
    if inline_json:
        print("✅ Conversão bem-sucedida!")
        print()
        print("📋 COPY A STRING ABAIXO para a variável GOOGLE_CREDENTIALS_JSON:")
        print("=" * 70)
        print(inline_json)
        print("=" * 70)
        print()
        print("🚂 Para Railway:")
        print("   1. Vá para a aba Variables do seu projeto")
        print("   2. Adicione uma nova variável:")
        print("      Nome: GOOGLE_CREDENTIALS_JSON")
        print("      Valor: [cole a string acima]")
        print()
        print("🔺 Para Heroku:")
        print("   heroku config:set GOOGLE_CREDENTIALS_JSON='[cole a string acima]'")
        print()
        print("💻 Para desenvolvimento local (.env):")
        print("   GOOGLE_CREDENTIALS_JSON='[cole a string acima]'")
        print()
        print("🔒 IMPORTANTE: Mantenha esta string secreta!")
        
        # Salvar em arquivo temporário
        output_file = "credentials_inline.txt"
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(inline_json)
            print(f"💾 String também salva em: {output_file}")
        except Exception as e:
            print(f"⚠️ Não foi possível salvar arquivo: {e}")
    
    else:
        print("❌ Falha na conversão.")
        sys.exit(1)

if __name__ == "__main__":
    main()
