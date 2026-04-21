# Archivo: app.py
import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import re
import qrcode
from io import BytesIO

# ==========================================
# CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="Asistente UST", page_icon="🎓", layout="centered")

# BARRA LATERAL (Integración de Opciones + QR Code)
with st.sidebar:
    st.subheader("⚙️ Opciones")
    if st.button("🔄 Nueva Consulta / Limpiar Memoria"):
        st.session_state.mensajes = [{"role": "assistant", "content": "Hola, memoria reiniciada. ¿En qué te ayudo?"}]
        st.rerun()
    
    st.divider()
    
    st.subheader("📱 Comparte este Asistente")
    st.write("Escanea este código para usarlo en tu celular:")
    
    # URL de la aplicación subida
    url_app = "https://asistente-facultad-ust-phy64glcydnx9v6q93sxqp.streamlit.app/" 
    qr = qrcode.make(url_app)
    img_buffer = BytesIO()
    qr.save(img_buffer, format="PNG")
    
    st.image(img_buffer.getvalue(), use_container_width=True)

st.title("🤖 Asistente Virtual UST")
st.markdown("Consulta horarios, trámites de secretaría o agenda citas con los docentes.")

# ==========================================
# CONEXIONES Y CACHÉ (V3.4-Lite)
# ==========================================
@st.cache_resource
def iniciar_conexiones():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    client = gspread.authorize(creds)
    URL_SHEET = "https://docs.google.com/spreadsheets/d/13OgPxlOJ6-XbnonBJMCUj05x7lw9aaqN-54gzZZXqog/edit"
    sh = client.open_by_url(URL_SHEET)
    
    genai.configure(api_key=st.secrets["gemini"]["api_key"])
    model = genai.GenerativeModel('gemini-3.1-flash-lite-preview')
    
    return sh, model

try:
    sh, model = iniciar_conexiones()
except Exception as e:
    st.error(f"❌ Error crítico: {e}")
    st.stop()

@st.cache_data(ttl=60) 
def obtener_datos_sheet(nombre_pestana):
    try:
        data = sh.worksheet(nombre_pestana).get_all_records()
        if not data: return "Sin datos."
        return pd.DataFrame(data).to_csv(index=False)
    except:
        return "Sin datos disponibles."

# ==========================================
# FUNCIONES DE SEGURIDAD (Sprint 1)
# ==========================================
def limpiar_nombre(nombre_raw):
    nombre_limpio = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', '', nombre_raw)
    return nombre_limpio.strip().title()

def validar_rut(rut_raw):
    rut = str(rut_raw).upper().replace(".", "").replace("-", "").strip()
    if len(rut) < 8: return False, ""
    cuerpo = rut[:-1]
    dv_usuario = rut[-1]
    if not cuerpo.isdigit(): return False, ""
    suma = 0
    multiplo = 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplo
        multiplo += 1
        if multiplo == 8: multiplo = 2
    resto = suma % 11
    dv_esperado = 11 - resto
    if dv_esperado == 11: dv_esperado = "0"
    elif dv_esperado == 10: dv_esperado = "K"
    else: dv_esperado = str(dv_esperado)
    return (dv_usuario == dv_esperado), f"{cuerpo}-{dv_usuario}"

def enmascarar_rut(rut_formateado):
    cuerpo, dv = rut_formateado.split("-")
    if len(cuerpo) >= 7: return f"{cuerpo[:-6]}.XXX.XXX-{dv}"
    return f"X.XXX.XXX-{dv}"

# ==========================================
# LÓGICA DEL NEGOCIO Y MEMORIA (Sprint 3.2)
# ==========================================
pd.set_option('display.max_colwidth', None)
pd.set_option('display.max_rows', None)

def obtener_historial():
    historial = ""
    for m in st.session_state.mensajes[-6:]:
        rol = "🤖 ASISTENTE" if m['role'] == 'assistant' else "👤 USUARIO"
        historial += f"{rol}: {m['content']}\n"
    return historial

def clasificar_intencion(mensaje, historial):
    print("📡 [DEBUG] -> Enrutando...")
    msg_lower = mensaje.lower()
    
    if re.search(r'\b\d{1,2}\.?\d{3}\.?\d{3}-?[0-9Kk]\b', mensaje):
        print("📡 [DEBUG] -> RUT detectado. Forzando RESERVA.")
        return "RESERVA"
        
    if any(frase in msg_lower for frase in ["me llamo", "mi nombre", "soy ", "es para", "el motivo es"]):
        print("📡 [DEBUG] -> Datos personales detectados. Forzando RESERVA.")
        return "RESERVA"

    prompt = f"""
    Lee este historial:
    {historial}
    
    Clasifica el ÚLTIMO mensaje: "{mensaje}"
    REGLAS:
    1. RESERVA: Pide agendar o da datos sueltos (Nombre, RUT, Motivo).
    2. FAQ: Trámites, casino, biblioteca.
    3. HORARIO: Cuándo atiende un docente.
    4. AJENO: Temas externos.
    5. OTRO: Saludos o agradecimientos cortos.
    Palabra:
    """
    try: 
        resultado = model.generate_content(prompt).text.strip().upper()
        return resultado
    except: return "ERROR_RED"

def modulo_faq(mensaje):
    ctx_faq = obtener_datos_sheet("FAQ")
    prompt = f"Responde usando SOLO esto:\n{ctx_faq}\nSi no está, responde: <NO_ENCONTRADO>\nPregunta: {mensaje}"
    respuesta = model.generate_content(prompt).text.strip()
    if "<NO_ENCONTRADO>" in respuesta:
        try: sh.worksheet("Preguntas_Pendientes").append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mensaje])
        except: pass
        return "No tengo la respuesta oficial. Guardé tu pregunta para revisión."
    return respuesta

def modulo_horarios(mensaje):
    ctx_horarios = obtener_datos_sheet("Horarios_Ocupados")
    ctx_directorio = obtener_datos_sheet("Directorio")
    prompt = f"Ocupados: {ctx_horarios}. Directorio: {ctx_directorio}. Responde disponibilidad. Pregunta: {mensaje}"
    return model.generate_content(prompt).text.strip()

def modulo_reservas(mensaje, historial):
    ctx_horarios = obtener_datos_sheet("Horarios_Ocupados")
    ctx_reservas = obtener_datos_sheet("Reservas")
    ctx_directorio = obtener_datos_sheet("Directorio")
    
    prompt = f"""
    Eres asistente de reservas UST. 
    HISTORIAL:
    {historial}
    
    Ocupados: {ctx_horarios} | Reservas: {ctx_reservas} | Directorio: {ctx_directorio}
    
    REGLAS:
    1. Revisa el historial para ver qué datos ya tienes (Nombre, RUT, Motivo).
    2. Pide amablemente lo que falte. No inventes.
    3. Cuando tengas los 6 datos (Profesor, Día, Hora, Nombre, RUT, Motivo) y esté libre, genera la etiqueta:
    <AGENDAR: Profesor | DIA | HH:MM | Alumno | RUT | Motivo>
    
    Mensaje actual: {mensaje}
    """
    respuesta_ia = model.generate_content(prompt).text.strip()

    if "<AGENDAR:" in respuesta_ia:
        try:
            inicio = respuesta_ia.find("<AGENDAR:") + 9
            fin = respuesta_ia.find(">", inicio)
            datos = [d.strip() for d in respuesta_ia[inicio:fin].split("|")]
            
            if len(datos) == 6:
                profesor, dia, hora, nombre_raw, rut_raw, motivo = datos
                
                if motivo.lower() in ["n/a", "no especificado", "pendiente", "reunión", "reunion"]:
                    return "⚠️ Por favor, indícame un **motivo** más específico para la reunión."

                nombre_limpio = limpiar_nombre(nombre_raw)
                es_valido, rut_limpio = validar_rut(rut_raw)
                
                if not es_valido: return "⚠️ Error: RUT inválido."
                if not nombre_limpio: return "⚠️ Error: Nombre inválido."

                # Anti-Spam
                filas_reservas = sh.worksheet("Reservas").get_all_values()
                for fila in filas_reservas[1:]:
                    if len(fila) >= 5:
                        if fila[4].strip() == rut_limpio and fila[0].lower() == profesor.lower() and fila[1].lower() == dia.lower():
                            return f"⛔ Límite Diario: Ya tienes cita hoy con {profesor}."

                sh.worksheet("Reservas").append_row([profesor, dia, hora, nombre_limpio, rut_limpio, motivo])
                
                rut_oculto = enmascarar_rut(rut_limpio)
                ticket = f"\n\n🎫 **TICKET CONFIRMADO**\n* **Alumno:** {nombre_limpio}\n* **RUT:** {rut_oculto}\n* **Docente:** {profesor}\n* **Fecha:** {dia.capitalize()} a las {hora} hrs\n* **Motivo:** {motivo}"
                
                return respuesta_ia[:respuesta_ia.find("<AGENDAR:")].strip() + ticket
        except Exception as e: print(f"❌ [ERROR]: {e}")
    return respuesta_ia

# ==========================================
# INTERFAZ GRÁFICA (UI)
# ==========================================
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
        with st.spinner("Procesando..."):
            try:
                historial_actual = obtener_historial()
                intencion = clasificar_intencion(prompt_usuario, historial_actual)
                
                if intencion == "ERROR_RED": respuesta_final = "Error de red. Intenta de nuevo."
                elif "FAQ" in intencion: respuesta_final = modulo_faq(prompt_usuario)
                elif "HORARIO" in intencion: respuesta_final = modulo_horarios(prompt_usuario)
                elif "RESERVA" in intencion: respuesta_final = modulo_reservas(prompt_usuario, historial_actual)
                elif "AJENO" in intencion: respuesta_final = "Solo puedo ayudarte con temas académicos."
                else: respuesta_final = "Hola, ¿en qué puedo ayudarte hoy?"
                
                st.markdown(respuesta_final)
                st.session_state.mensajes.append({"role": "assistant", "content": respuesta_final})
                
            except Exception as e:
                st.markdown("⚠️ Ocurrió un error inesperado.")
