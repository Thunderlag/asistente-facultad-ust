# Archivo: app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import qrcode
from io import BytesIO

# ==========================================
# CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="Asistente UST", page_icon="🎓", layout="centered")
st.title("🤖 Asistente Virtual UST")
st.markdown("Consulta horarios, trámites de secretaría o agenda citas con los docentes.")

# ==========================================
# CONEXIONES A LA NUBE
# ==========================================
@st.cache_resource
def iniciar_conexiones():
    # Google Sheets
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    client = gspread.authorize(creds)
    URL_SHEET = "https://docs.google.com/spreadsheets/d/13OgPxlOJ6-XbnonBJMCUj05x7lw9aaqN-54gzZZXqog/edit"
    sh = client.open_by_url(URL_SHEET)
    
    # Gemini
    genai.configure(api_key=st.secrets["gemini"]["api_key"])
    model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
    
    return sh, model

try:
    sh, model = iniciar_conexiones()
except Exception as e:
    st.error(f"❌ Error crítico de conexión: {e}")
    st.stop()

# ==========================================
# LÓGICA DEL NEGOCIO (BACKEND)
# ==========================================
pd.set_option('display.max_colwidth', None)
pd.set_option('display.max_rows', None)

def obtener_datos_sheet(nombre_pestana):
    try:
        data = sh.worksheet(nombre_pestana).get_all_records()
        if not data: return "Sin datos."
        return pd.DataFrame(data).to_string(index=False)
    except Exception as e:
        print(f"❌ [ERROR LECTURA {nombre_pestana}]: {e}")
        return "Sin datos disponibles."

def clasificar_intencion(mensaje):
    print("📡 [DEBUG] -> Clasificando intención con Gemini...")
    prompt = f"""
    Clasifica el siguiente mensaje en UNA sola palabra: FAQ, HORARIO, RESERVA, AJENO u OTRO.
    - FAQ: Trámites, plazos, casino, certificados, preguntas generales, y HORARIOS DE LUGARES (biblioteca, sede).
    - HORARIO: ESTRICTAMENTE SOLO disponibilidad de PROFESORES O PERSONAS (ej. a qué hora atiende Luis). NUNCA lugares.
    - RESERVA: Agendar, tomar hora (INCLUYE envío de RUT o Nombre).
    - AJENO: Temas sin relación con la universidad.
    - OTRO: Saludos básicos.
    Mensaje: "{mensaje}"
    """
    try: 
        resultado = model.generate_content(prompt).text.strip().upper()
        print(f"📡 [DEBUG] -> Intención detectada: {resultado}")
        return resultado
    except Exception as e: 
        print(f"❌ [ERROR GEMINI CLASIFICADOR]: {e}")
        return "ERROR_RED"

def modulo_faq(mensaje):
    print("📡 [DEBUG] -> Ejecutando módulo FAQ...")
    ctx_faq = obtener_datos_sheet("FAQ")
    prompt = f"Eres asistente de la Facultad. Responde usando SOLO esta información:\n{ctx_faq}\nREGLA: Si la respuesta no está, responde EXACTAMENTE: <NO_ENCONTRADO>\nPregunta: {mensaje}"
    respuesta = model.generate_content(prompt).text.strip()
    
    if "<NO_ENCONTRADO>" in respuesta:
        try:
            print("📡 [DEBUG] -> Guardando pregunta pendiente en Sheets...")
            sh.worksheet("Preguntas_Pendientes").append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mensaje])
        except Exception as e: 
            print(f"❌ [ERROR ESCRITURA SHEETS]: {e}")
        return "No tengo la respuesta oficial. He guardado tu pregunta para revisión."
    return respuesta

def modulo_horarios(mensaje):
    print("📡 [DEBUG] -> Ejecutando módulo HORARIOS...")
    ctx_horarios = obtener_datos_sheet("Horarios_Ocupados")
    ctx_directorio = obtener_datos_sheet("Directorio")
    prompt = f"Jornada L a V de 08:00 a 17:30. Ocupados: {ctx_horarios}. Directorio: {ctx_directorio}. Responde disponibilidad agrupada. Pregunta: {mensaje}"
    return model.generate_content(prompt).text.strip()

def modulo_reservas(mensaje):
    print("📡 [DEBUG] -> Ejecutando módulo RESERVAS. Leyendo contexto...")
    ctx_horarios = obtener_datos_sheet("Horarios_Ocupados")
    ctx_reservas = obtener_datos_sheet("Reservas")
    ctx_directorio = obtener_datos_sheet("Directorio")
    
    print("📡 [DEBUG] -> Consultando lógica de reserva con Gemini...")
    prompt = f"""
    Eres asistente de reservas (L a V, 08:00 a 17:30). Ocupados: {ctx_horarios}. Alumnos agendados: {ctx_reservas}. Directorio: {ctx_directorio}
    REGLAS ESTRICTAS:
    1. EXIGE obligatoriamente Nombre completo y RUT para agendar.
    2. Si falta Nombre o RUT, pide los datos y NO generes la etiqueta.
    3. Solo con Nombre y RUT listos, confirma y agrega al final EXACTAMENTE:
    <AGENDAR: Profesor | DIA | HH:MM | Alumno | RUT>
    Mensaje: {mensaje}
    """
    respuesta_ia = model.generate_content(prompt).text.strip()
    print("📡 [DEBUG] -> Gemini respondió a la reserva.")

    if "<AGENDAR:" in respuesta_ia:
        try:
            print("📡 [DEBUG] -> Intentando escribir la reserva en Google Sheets...")
            inicio = respuesta_ia.find("<AGENDAR:") + 9
            fin = respuesta_ia.find(">", inicio)
            datos = [d.strip() for d in respuesta_ia[inicio:fin].split("|")]
            
            if len(datos) == 5:
                sh.worksheet("Reservas").append_row(datos)
                print("📡 [DEBUG] -> ¡Reserva escrita con éxito en Sheets!")
                ticket = f"\n\n🎫 **TICKET CONFIRMADO**\n* **Alumno:** {datos[3]} ({datos[4]})\n* **Docente:** {datos[0]}\n* **Fecha:** {datos[1].capitalize()} a las {datos[2]} hrs"
                return respuesta_ia[:respuesta_ia.find("<AGENDAR:")].strip() + ticket
        except Exception as e: 
            print(f"❌ [ERROR AL GUARDAR RESERVA EN SHEETS]: {e}")
    return respuesta_ia

# ==========================================
# INTERFAZ GRÁFICA (UI)
# ==========================================

# 1. BARRA LATERAL CON EL CÓDIGO QR
with st.sidebar:
    st.subheader("📱 Comparte este Asistente")
    st.write("Escanea este código para usarlo en tu celular:")
    
    # Reemplaza esto con tu link real si es diferente
    url_app = "https://asistente-facultad-ust-phy64glcydnx9v6q93sxqp.streamlit.app/" 
    qr = qrcode.make(url_app)
    img_buffer = BytesIO()
    qr.save(img_buffer, format="PNG")
    
    st.image(img_buffer.getvalue())

# 2. INICIALIZACIÓN DEL CHAT
if "mensajes" not in st.session_state:
    st.session_state.mensajes = [{"role": "assistant", "content": "Hola, ¿en qué puedo ayudarte hoy?"}]

for mensaje in st.session_state.mensajes:
    with st.chat_message(mensaje["role"]):
        st.markdown(mensaje["content"])

if prompt_usuario := st.chat_input("Escribe tu consulta aquí..."):
    st.session_state.mensajes.append({"role": "user", "content": prompt_usuario})
    with st.chat_message("user"):
        st.markdown(prompt_usuario)

    with st.chat_message("assistant"):
        with st.spinner("Consultando registros..."):
            try:
                intencion = clasificar_intencion(prompt_usuario)
                
                if intencion == "ERROR_RED": respuesta_final = "Experimenté un corte de red. ¿Podrías repetir tu mensaje?"
                elif "FAQ" in intencion: respuesta_final = modulo_faq(prompt_usuario)
                elif "HORARIO" in intencion: respuesta_final = modulo_horarios(prompt_usuario)
                elif "RESERVA" in intencion: respuesta_final = modulo_reservas(prompt_usuario)
                elif "AJENO" in intencion: respuesta_final = "Solo puedo ayudarte con temas académicos de la facultad."
                else: respuesta_final = "Hola, ¿en qué puedo ayudarte hoy?"
                
                st.markdown(respuesta_final)
                st.session_state.mensajes.append({"role": "assistant", "content": respuesta_final})
                
            except Exception as e:
                error_msg = "⚠️ Ocurrió un error inesperado al procesar tu solicitud."
                st.markdown(error_msg)
                st.session_state.mensajes.append({"role": "assistant", "content": error_msg})
