import os
from google import genai  # Nova biblioteca
from pydub import AudioSegment

# CONFIGURAÇÕES
API_KEY = os.getenv("GOOGLE_API_KEY") or "AIzaSyD-Jw5wuTceNtaOzk4Rv3zayr4wrJKqW2w"
VIDEO_PATH = "C:\\Users\\Dell\\Downloads\\gravacao_20250722_143528.mp3"
AUDIO_PATH = "temp_audio.mp3"
OUTPUT_TXT = "transcricao_com_falantes.txt"

# 1. Extrai Áudio do Vídeo
def extract_audio_from_video(video_path, audio_path):
    audio = AudioSegment.from_file(video_path)
    audio.export(audio_path, format="mp3")

# 2. Transcreve Áudio usando Gemini com Diarização
def transcribe_audio(audio_path):
    client = genai.Client(api_key=API_KEY)
    myfile = client.files.upload(file=audio_path)
    
    # Aguarda o arquivo ficar ACTIVE
    import time
    for _ in range(15):
        try:
            file_info = client.files.get(name=myfile.name)
            if file_info.state == "ACTIVE":
                break
        except Exception as e:
            pass
        time.sleep(2)
    else:
        raise RuntimeError("Arquivo não ficou ACTIVE a tempo.")
    
    # Prompt para transcrição com identificação de falantes
    prompt = """Generate a detailed transcript of the speech with speaker diarization. 
    Please:
    1. Identify different speakers in the audio
    2. Label each speaker as "Falante 1:", "Falante 2:", etc.
    3. Separate each speaker's speech clearly
    4. Include natural pauses and conversation flow
    5. If you can determine characteristics about the speakers (gender, tone), mention it briefly
    
    Format the output like this:
    Falante 1: [texto da fala]
    Falante 2: [texto da fala]
    Falante 1: [continuação da fala]
    
    Provide the complete transcript in Portuguese."""
    
    response = client.models.generate_content(
        model="gemini-2.0-flash-001",
        contents=[prompt, myfile]
    )
    return response.text

# 3. Salva a transcrição em .txt
def save_transcription(text, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)

def main():
    print("Extraindo áudio do arquivo...")
    # Se for vídeo, extrai áudio; se já for áudio, só copia
    if VIDEO_PATH.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
        extract_audio_from_video(VIDEO_PATH, AUDIO_PATH)
    else:
        # Se já é áudio, usa diretamente
        AUDIO_PATH = VIDEO_PATH
    
    print("Transcrevendo áudio com identificação de falantes usando Gemini...")
    transcription = transcribe_audio(AUDIO_PATH)
    print("Salvando transcrição com diarização...")
    save_transcription(transcription, OUTPUT_TXT)
    print(f"Transcrição com identificação de falantes salva em {OUTPUT_TXT}")
    
    # Remove áudio temporário apenas se foi extraído de vídeo
    if VIDEO_PATH.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')) and os.path.exists(AUDIO_PATH):
        os.remove(AUDIO_PATH)

if __name__ == "__main__":
    main()
