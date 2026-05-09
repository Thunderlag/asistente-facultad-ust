# Archivo: app_test.py (V5.1.1 - QA Passed - Corrección QR)
import streamlit as st
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types 
import re
import qrcode
from io import BytesIO
import threading
import json 

# ==========================================
# 1. CONFIGURACIÓN Y CONEXIONES
# ==========================================
st.set_page_config(page_title="Asistente UST - DEV", page_icon="🧪", layout="centered")

@st.cache_resource
def iniciar_conexiones():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)
    g_client = gspread.authorize(creds)
    URL_SHEET = "https://docs.google.com/spreadsheets/d/19HASmg1y4V1S4_9gP1hY1tKT8JdRxL1ZlGKawJPLWEY/edit"
    sh = g_client.open_by_url(URL_SHEET)
    
    ai_client = genai.Client(api_key=st.secrets["gemini"]["api_key"])
    return sh, ai_client

try: sh, client = iniciar_conexiones()
except Exception as e:
    st.error(f"❌ Error crítico de conexión: {e}")
    st.stop()

pd.set_option('display.max_colwidth', None)
pd.set_option('display.max_rows', None)

# Caché de lectura de Google Sheets (180s = 3 min). Evita saturar la API. NO afecta el historial del chat.
@st.cache_data(ttl=180) 
def obtener_datos_sheet(nombre_pestana):
    try:
        data = sh.worksheet(nombre_pestana).get_all_records()
        if not data: return "Sin datos."
        return pd.DataFrame(data).to_csv(index=False)
    except: return "Sin datos disponibles."

# ==========================================
# 2. ESCRITORES RÁPIDOS (Hilos en 2do Plano)
# ==========================================
def escribir_analitica_bg(usuario, intencion, detalle):
    def tarea():
        try:
            ahora = datetime.now()
            sh.worksheet("Analiticas").append_row([ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M:%S"), usuario, intencion, detalle])
        except: pass
    threading.Thread(target=tarea).start()

def escribir_pregunta_bg(mensaje):
    def tarea():
        try: sh.worksheet("Preguntas_Pendientes").append_row([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), mensaje])
        except: pass
    threading.Thread(target=tarea).start()

# ==========================================
# 3. RESERVA SEGURA (Sincrónica y Bloqueante)
# ==========================================
def realizar_reserva_segura(datos):
    try:
        pestaña = sh.worksheet("Reservas")
        reservas_actuales = pestaña.get_all_values()
        for fila in reservas_actuales[1:]: 
            if len(fila) >= 3 and fila[0].lower() == datos[0].lower() and fila[1].lower() == datos[1].lower() and fila[2] == datos[2]:
                return False, "⚠️ ¡Lo siento! Ese bloque de horario acaba de ser tomado hace unos segundos."
        pestaña.append_row(datos)
        return True, "Ticket confirmado."
    except Exception as e:
        return False, f"Error de conexión al reservar: {e}"

# ==========================================
# 4. FUNCIONES AUXILIARES
# ==========================================
def validar_rut(rut_raw):
    rut = str(rut_raw).upper().replace(".", "").replace("-", "").strip()
    if len(rut) < 8: return False, ""
    cuerpo, dv_usuario = rut[:-1], rut[-1]
    if not cuerpo.isdigit(): return False, ""
    suma, multiplo = 0, 2
    for c in reversed(cuerpo):
        suma += int(c) * multiplo
        multiplo = 2 if multiplo == 7 else multiplo + 1
    dv_esperado = str(11 - (suma % 11))
    if dv_esperado == "11": dv_esperado = "0"
    elif dv_esperado == "10": dv_esperado = "K"
    return (dv_usuario == dv_esperado), f"{cuerpo}-{dv_usuario}"

def enmascarar_rut(rut_fmt):
    c, d = rut_fmt.split("-")
    return f"{c[:-6]}.XXX.XXX-{d}" if len(c) >= 7 else f"X.XXX.XXX-{d}"

def obtener_historial():
    historial = ""
    for m in st.session_state.mensajes[-6:]:
        rol = "ASISTENTE" if m['role'] == 'assistant' else "ALUMNO"
        historial += f"{rol}: {m['content']}\n"
    return historial

# ==========================================
# 5. EL MOTOR V5.1 (SÚPER PROMPT JSON)
# ==========================================
def procesar_mensaje_v5(mensaje, historial):
    ctx_faq = obtener_datos_sheet("FAQ")
    ctx_horarios = obtener_datos_sheet("Horarios_Ocupados")
    ctx_reservas = obtener_datos_sheet("Reservas")
    ctx_directorio = obtener_datos_sheet("Directorio") 
    
    prompt_maestro = f"""
    Eres el Asistente Virtual UST (L a V, 08:00 a 17:30). Eres amable y directo.
    
    DATOS DE LA UNIVERSIDAD:
    - Directorio Docente: {ctx_directorio}
    - FAQs: {ctx_faq}
    - Horarios Ocupados: {ctx_horarios}
    - Reservas Actuales: {ctx_reservas}
    
    HISTORIAL DE CHAT:
    {historial}
    
    MENSAJE ACTUAL DEL ALUMNO: "{mensaje}"
    
    INSTRUCCIONES ESTRICTAS:
    1. Analiza el historial y el mensaje en un solo paso.
    2. Determina la "intencion" (FAQ, RESERVA, HORARIO, AJENO, OTRO).
    3. Genera la "respuesta" adecuada.
        - Si es FAQ y no sabes la respuesta exacta, tu respuesta DEBE contener la palabra <NO_ENCONTRADO>.
        - Si es RESERVA, pide los datos faltantes. Si ya tienes los 6 datos validados (Profesor del directorio, Día, Hora, Nombre, RUT, Motivo), tu respuesta DEBE terminar con este formato exacto: <AGENDAR: Profesor | Día | Hora | Nombre | RUT | Motivo>
    
    Responde ÚNICAMENTE en formato JSON usando esta estructura:
    {{
        "intencion": "...",
        "respuesta": "..."
    }}
    """
    
    try:
        respuesta_ia = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt_maestro,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            )
        )
        
        texto_limpio = respuesta_ia.text.strip()
        if texto_limpio.startswith("```json"):
            texto_limpio = texto_limpio[7:-3].strip()
            
        datos = json.loads(texto_limpio)
        return datos.get("intencion", "OTRO").upper(), datos.get("respuesta", "Lo siento, tuve un problema interno.")
        
    except json.JSONDecodeError as e:
        print(f"❌ Error Formato JSON: {e} -> Recibido: {respuesta_ia.text}")
        return "ERROR_FORMATO", "Tuve un pequeño cruce de cables con el servidor. ¿Me repites la pregunta?"
    except Exception as e:
        print(f"❌ Error de red o cuota: {e}")
        return "ERROR_RED", "⚠️ El sistema está recibiendo muchas consultas. Por favor, dame 30 segundos y vuelve a intentarlo."

# ==========================================
# 6. INTERFAZ VISUAL BLINDADA (UI Lock)
# ==========================================
with st.sidebar:
    st.subheader("🔑 Acceso Administrativo")
    password = st.text_input("Contraseña de Director", type="password")
    
    clave_correcta = st.secrets.get("admin_password")
    es_admin = bool(password) and (password == clave_correcta) # <--- EL CANDADO CERRADO
    
    if es_admin: st.success("Modo Admin Activo")
    else: st.info("Ingresa clave para estadísticas.")

    st.divider()
    st.subheader("📱 Compartir Asistente")
    url_app = "[https://asistente-facultad-ust-phy64glcydnx9v6q93sxqp.streamlit.app/](https://asistente-facultad-ust-phy64glcydnx9v6q93sxqp.streamlit.app/)" # <-- LÍNEA CORREGIDA
    qr = qrcode.make(url_app)
    img_buffer = BytesIO()
    qr.save(img_buffer, format="PNG")
    st.image(img_buffer.getvalue(), width="stretch")

    st.divider()
    st.subheader("🔄 Control")
    if st.button("Limpiar Memoria"):
        st.session_state.mensajes = [{"role": "assistant", "content": "Hola, ¿en qué te puedo ayudar?"}]
        st.rerun()

if es_admin:
    st.title("📊 Dashboard de Gestión UST")
    st.markdown("Métricas en vivo de uso y consultas de los estudiantes.")
else:
    st.title("🤖 Asistente Virtual UST")
    st.markdown("Consulta horarios, trámites de secretaría o agenda citas con los docentes.")

# ==========================================
# 7. RENDERIZADO (DASHBOARD vs CHAT)
# ==========================================
if es_admin:
    try:
        df_logs = pd.DataFrame(sh.worksheet("Analiticas").get_all_records())
        if not df_logs.empty:
            col1, col2 = st.columns(2)
            with col1: st.metric("Total Interacciones", len(df_logs))
            with col2: st.metric("Consultas FAQ", len(df_logs[df_logs['Intencion'] == 'FAQ']))
            
            st.subheader("Distribución de Intenciones")
            st.bar_chart(df_logs['Intencion'].value_counts())
            
            st.subheader("Últimas Consultas Registradas")
            st.dataframe(df_logs.tail(10))
        else: st.info("Aún no hay datos registrados en 'Analiticas'.")
    except Exception as e: st.error(f"Error al cargar dashboard: {e}")

else:
    if "mensajes" not in st.session_state: 
        st.session_state.mensajes = [{"role": "assistant", "content": "Hola, ¿en qué te puedo ayudar?"}]
        
    for m in st.session_state.mensajes:
        with st.chat_message(m["role"]): st.markdown(m["content"])

    if prompt_usuario := st.chat_input("Escribe tu consulta aquí..."):
        st.session_state.mensajes.append({"role": "user", "content": prompt_usuario})
        with st.chat_message("user"): st.markdown(prompt_usuario)

        with st.chat_message("assistant"):
            with st.spinner("Pensando..."):
                
                historial = obtener_historial()
                intencion, respuesta_cruda = procesar_mensaje_v5(prompt_usuario, historial)
                
                usuario_log = "Anónimo"
                rut_detectado = re.search(r'\b\d{1,2}\.?\d{3}\.?\d{3}-?[0-9Kk]\b', prompt_usuario)
                if rut_detectado:
                    valido, rut_fmt = validar_rut(rut_detectado.group())
                    if valido: usuario_log = enmascarar_rut(rut_fmt)

                respuesta_final = respuesta_cruda
                
                if "<NO_ENCONTRADO>" in respuesta_cruda:
                    escribir_pregunta_bg(prompt_usuario)
                    respuesta_final = "No tengo la respuesta oficial en mis registros. He guardado tu pregunta para que secretaría la revise."
                    
                elif "<AGENDAR:" in respuesta_cruda:
                    try:
                        inicio = respuesta_cruda.find("<AGENDAR:") + 9
                        fin = respuesta_cruda.find(">", inicio)
                        datos = [d.strip() for d in respuesta_cruda[inicio:fin].split("|")]
                        
                        if len(datos) == 6:
                            profesor, dia, hora, nombre_raw, rut_raw, motivo = datos
                            nombre_limpio = re.sub(r'[^a-zA-ZáéíóúÁÉÍÓÚñÑ\s]', '', nombre_raw).strip().title()
                            es_valido, rut_limpio = validar_rut(rut_raw)
                            
                            if not es_valido: respuesta_final = "⚠️ Error: RUT inválido."
                            elif not nombre_limpio: respuesta_final = "⚠️ Error: Nombre inválido."
                            elif motivo.lower() in ["n/a", "no especificado", "pendiente", "reunion"]: 
                                respuesta_final = "⚠️ Necesito un motivo más específico para la reunión."
                            else:
                                exito, msj = realizar_reserva_segura([profesor, dia, hora, nombre_limpio, rut_limpio, motivo])
                                if exito:
                                    ticket = f"\n\n🎫 **TICKET CONFIRMADO**\n* **Alumno:** {nombre_limpio}\n* **RUT:** {enmascarar_rut(rut_limpio)}\n* **Docente:** {profesor}\n* **Fecha:** {dia.capitalize()} a las {hora} hrs"
                                    respuesta_final = respuesta_cruda[:respuesta_cruda.find("<AGENDAR:")].strip() + ticket
                                else:
                                    respuesta_final = msj
                    except Exception as e:
                        print(f"Error procesando agenda: {e}")
                        respuesta_final = "Hubo un error armando tu ticket."

                st.markdown(respuesta_final)
                st.session_state.mensajes.append({"role": "assistant", "content": respuesta_final})
                
                detalle_log = prompt_usuario[:50] if "RESERVA" not in intencion else "Proceso de reserva"
                escribir_analitica_bg(usuario_log, intencion, detalle_log)
