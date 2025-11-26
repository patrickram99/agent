"""
Agente Conversacional con LangChain para Finanzas Personales
============================================================
Ventajas sobre el enfoque anterior:
1. Extracción Semántica - El LLM entiende contexto (ej: "tabas" = Ropa, "chifita" = Comida)
2. Function Calling - El agente decide qué herramienta usar automáticamente
3. Memoria Conversacional - Recuerda el contexto sin máquinas de estado manuales
"""

import os
import re
import random
import string
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from decimal import Decimal

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

load_dotenv()

# ==================== CONFIGURATION ====================
EVOLUTION_BASE_URL = os.getenv("EVOLUTION_BASE_URL", "http://34.121.145.34:8080")
INSTANCE_ID = os.getenv("INSTANCE_ID", "ConstruccionSOftware")
API_KEY = os.getenv("EVOLUTION_API_KEY", "CC8105CE9838-4865-A4EA-F7792D1A5CA7")
DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TIMEZONE = os.getenv("TIMEZONE", "America/Lima")

# ==================== DATABASE ====================
def get_pg_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL es requerido")
    return psycopg2.connect(DATABASE_URL)

def ensure_user(number: str, name: Optional[str] = None, email: Optional[str] = None) -> int:
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT id FROM users WHERE whatsapp_number=%s", (number,))
                row = cur.fetchone()
                if row:
                    return row[0]
                cur.execute(
                    "INSERT INTO users (whatsapp_number, name, email) VALUES (%s, %s, %s) RETURNING id",
                    (number, name, email)
                )
                return cur.fetchone()[0]
    finally:
        conn.close()

def is_new_user(number: str) -> bool:
    """Verifica si es un usuario nuevo (sin nombre registrado)"""
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT id, name, email FROM users WHERE whatsapp_number=%s", (number,))
                row = cur.fetchone()
                if not row:
                    return True  # No existe, es nuevo
                # Existe pero sin nombre = considerarlo nuevo
                return row['name'] is None or row['name'] == ''
    finally:
        conn.close()

def update_user_profile(number: str, name: str, email: str) -> bool:
    """Actualiza el perfil del usuario con nombre y email"""
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # Primero verificar si existe
                cur.execute("SELECT id FROM users WHERE whatsapp_number=%s", (number,))
                row = cur.fetchone()
                if row:
                    cur.execute(
                        "UPDATE users SET name=%s, email=%s WHERE whatsapp_number=%s",
                        (name, email, number)
                    )
                else:
                    cur.execute(
                        "INSERT INTO users (whatsapp_number, name, email) VALUES (%s, %s, %s)",
                        (number, name, email)
                    )
                return True
    finally:
        conn.close()

def get_user_profile(number: str) -> Optional[Dict[str, Any]]:
    """Obtiene el perfil del usuario"""
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT id, name, email, created_at FROM users WHERE whatsapp_number=%s", (number,))
                row = cur.fetchone()
                return dict(row) if row else None
    finally:
        conn.close()

# ==================== PYDANTIC MODELS PARA TOOLS ====================
class TransactionInput(BaseModel):
    """Input para registrar una transacción"""
    tipo: str = Field(description="Tipo de transacción: 'gasto' o 'ingreso'")
    monto: float = Field(description="Monto en soles (PEN)")
    categoria: str = Field(description="Categoría: comida, diversión, ropa, transporte, salud, vivienda, servicios, educación, ahorro, otros (gastos) o salario, freelance, regalos, otros (ingresos)")
    descripcion: str = Field(description="Descripción breve del gasto o ingreso")
    fecha: Optional[str] = Field(default=None, description="Fecha en formato natural: 'hoy', 'ayer', '12/11', etc. Si no se especifica, usar hoy")

class ReportInput(BaseModel):
    """Input para generar un reporte"""
    periodo: str = Field(description="Período del reporte: 'semanal', 'mensual' o 'anual'")

# ==================== TOOLS (HERRAMIENTAS DEL AGENTE) ====================

# Store para el número de teléfono actual (se setea antes de cada invocación)
_current_user_number: str = ""

def set_current_user(number: str):
    global _current_user_number
    _current_user_number = number

def get_current_user_id() -> int:
    global _current_user_number
    if not _current_user_number:
        raise ValueError("No hay usuario configurado")
    return ensure_user(_current_user_number)

@tool
def registrar_transaccion(
    tipo: str,
    monto: float,
    categoria: str,
    descripcion: str,
    fecha: Optional[str] = None
) -> str:
    """
    Registra un gasto o ingreso en el sistema. SIEMPRE usa esta herramienta cuando el usuario mencione un gasto o ingreso.
    
    Args:
        tipo: 'gasto' o 'ingreso'
        monto: Cantidad en soles (PEN)
        categoria: Categoría de la transacción (comida, transporte, ropa, etc.)
        descripcion: Descripción breve del gasto/ingreso
        fecha: Fecha en formato natural (ej: 'ayer', 'hoy', '12/11'). Por defecto es hoy.
    
    Returns:
        Confirmación del registro guardado en base de datos
    """
    import dateparser
    
    print(f"🔧 TOOL CALLED: registrar_transaccion(tipo={tipo}, monto={monto}, categoria={categoria}, descripcion={descripcion}, fecha={fecha})")
    
    user_id = get_current_user_id()
    
    # Normalizar tipo
    tipo = tipo.lower().strip()
    if tipo not in ('gasto', 'ingreso'):
        return f"Error: tipo debe ser 'gasto' o 'ingreso', recibí '{tipo}'"
    
    # Normalizar categoría
    CATEGORIAS_GASTO = ["comida", "diversión", "ropa", "transporte", "salud", 
                        "vivienda", "servicios", "educación", "ahorro", "otros"]
    CATEGORIAS_INGRESO = ["salario", "freelance", "regalos", "otros"]
    
    categoria = categoria.lower().strip()
    categorias_validas = CATEGORIAS_GASTO if tipo == 'gasto' else CATEGORIAS_INGRESO
    if categoria not in categorias_validas:
        categoria = "otros"
    
    # Parsear fecha
    if fecha:
        dt = dateparser.parse(fecha, languages=['es'], settings={
            'TIMEZONE': TIMEZONE,
            'PREFER_DAY_OF_MONTH': 'first',
            'DATE_ORDER': 'DMY'
        })
        occurred_at = dt or datetime.now()
    else:
        occurred_at = datetime.now()
    
    # Guardar en DB
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO transactions (user_id, type, amount, currency, category, description, occurred_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, tipo, monto, 'PEN', categoria, descripcion, occurred_at)
                )
    finally:
        conn.close()
    
    return f"✅ {tipo.capitalize()} registrado: S/ {monto:.2f} en {categoria} ({occurred_at.strftime('%d/%m/%Y')}). Descripción: {descripcion}"


@tool
def generar_reporte(periodo: str) -> str:
    """
    Genera un reporte financiero del usuario. Usa esta herramienta cuando el usuario pida un reporte.
    
    Args:
        periodo: 'semanal' (desde el lunes), 'mensual' (mes actual) o 'anual' (año actual)
    
    Returns:
        Reporte formateado con ingresos, gastos y desglose por categoría
    """
    print(f"🔧 TOOL CALLED: generar_reporte(periodo={periodo})")
    
    user_id = get_current_user_id()
    periodo = periodo.lower().strip()
    
    now = datetime.now()
    
    # Calcular rango de fechas
    if periodo == 'semanal':
        dow = now.weekday()
        start = now - timedelta(days=dow)
        start = datetime(start.year, start.month, start.day)
        end = now
        titulo = "📊 Reporte Semanal"
    elif periodo == 'mensual':
        start = datetime(now.year, now.month, 1)
        end = now
        titulo = "📊 Reporte Mensual"
    elif periodo == 'anual':
        start = datetime(now.year, 1, 1)
        end = now
        titulo = "📊 Reporte Anual"
    else:
        return f"Error: período debe ser 'semanal', 'mensual' o 'anual'. Recibí: '{periodo}'"
    
    # Consultar DB
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT type, category, SUM(amount) as total
                    FROM transactions
                    WHERE user_id=%s AND occurred_at BETWEEN %s AND %s
                    GROUP BY type, category
                    ORDER BY type, total DESC
                    """,
                    (user_id, start, end)
                )
                rows = cur.fetchall()
    finally:
        conn.close()
    
    # Procesar resultados
    total_gastos = 0.0
    total_ingresos = 0.0
    desglose_gastos = []
    desglose_ingresos = []
    
    for r in rows:
        tipo, categoria, total = r[0], r[1], float(r[2] or 0)
        if tipo == 'gasto':
            total_gastos += total
            desglose_gastos.append(f"  • {categoria}: S/ {total:.2f}")
        else:
            total_ingresos += total
            desglose_ingresos.append(f"  • {categoria}: S/ {total:.2f}")
    
    neto = total_ingresos - total_gastos
    emoji_neto = "🟢" if neto >= 0 else "🔴"
    
    # Formatear reporte
    rango = f"📅 {start.strftime('%d/%m/%Y')} - {end.strftime('%d/%m/%Y')}"
    
    lines = [
        titulo,
        rango,
        "",
        f"💰 Ingresos: S/ {total_ingresos:.2f}",
        f"💸 Gastos: S/ {total_gastos:.2f}",
        f"{emoji_neto} Balance: S/ {neto:.2f}",
    ]
    
    if desglose_ingresos:
        lines.append("")
        lines.append("📈 Ingresos por categoría:")
        lines.extend(desglose_ingresos)
    
    if desglose_gastos:
        lines.append("")
        lines.append("📉 Gastos por categoría:")
        lines.extend(desglose_gastos)
    
    if not rows:
        lines.append("")
        lines.append("ℹ️ No hay transacciones en este período.")
    
    return "\n".join(lines)


@tool
def generar_codigo_otp() -> str:
    """
    Genera un código OTP de 6 dígitos para autenticación.
    
    Returns:
        Código OTP o mensaje de error si hay rate limit
    """
    user_id = get_current_user_id()
    
    code = ''.join(random.choices(string.digits, k=6))
    
    conn = get_pg_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # Rate limit: 10 por hora (usando hora peruana)
                cur.execute(
                    """SELECT COUNT(*) FROM otps 
                       WHERE user_id=%s 
                       AND created_at >= ((NOW() AT TIME ZONE 'America/Lima')::timestamp AT TIME ZONE 'UTC' - interval '1 hour')""",
                    (user_id,)
                )
                count = cur.fetchone()[0]
                if count >= 10:
                    return "⚠️ Has solicitado demasiados códigos. Intenta en una hora."
                
                # Borrar TODOS los códigos anteriores del usuario (hard delete)
                cur.execute("DELETE FROM otps WHERE user_id=%s", (user_id,))

                # Insertar nuevo OTP en hora peruana
                cur.execute(
                    """
                    INSERT INTO otps (user_id, code, expires_at, created_at)
                    VALUES (
                        %s,
                        %s,
                        (NOW() AT TIME ZONE 'America/Lima')::timestamp AT TIME ZONE 'UTC' + interval '5 minutes',
                        (NOW() AT TIME ZONE 'America/Lima')::timestamp AT TIME ZONE 'UTC'
                    )
                    """,
                    (user_id, code)
                )
    finally:
        conn.close()
    
    return f"🔐 Tu código es: {code}\n⏱️ Expira en 5 minutos.\n\n📊 Ingresa a tu dashboard:\nhttps://v0-expense-report-dashboard-gamma.vercel.app/auth/login"


@tool
def mostrar_ayuda() -> str:
    """
    Muestra las instrucciones de uso del bot.
    
    Returns:
        Texto de ayuda con ejemplos
    """
    return """📱 *Asistente Financiero Personal*

*Registrar gastos:*
• "Gasté 50 soles en comida ayer"
• "Me compré unas zapatillas por 200"
• "Pagué 30 en el taxi"

*Registrar ingresos:*
• "Me pagaron 3000 de sueldo"
• "Recibí 500 por un trabajo freelance"

*Ver reportes:*
• "Dame mi reporte semanal"
• "¿Cuánto gasté este mes?"
• "Reporte anual"

*Otros:*
• "Código" - Genera código OTP
• "Ayuda" - Muestra este mensaje

� *Dashboard de gastos:*
https://v0-expense-report-dashboard-gamma.vercel.app/auth/login

�💡 Puedo entender jerga peruana como "chifita", "tabas", "lucas", etc.
💰 Todos los montos son en soles (PEN)."""


@tool
def registrar_usuario(nombre: str, email: str) -> str:
    """
    Registra o actualiza el perfil del usuario con su nombre y email.
    USAR cuando el usuario proporcione su nombre y email por primera vez.
    
    Args:
        nombre: Nombre completo del usuario
        email: Correo electrónico del usuario
    
    Returns:
        Confirmación del registro
    """
    global _current_user_number
    
    print(f"🔧 TOOL CALLED: registrar_usuario(nombre={nombre}, email={email})")
    
    if not _current_user_number:
        return "Error: No hay número de usuario configurado"
    
    # Validar email básico
    if not email or '@' not in email:
        return "❌ El email no parece válido. Por favor proporciona un email correcto."
    
    success = update_user_profile(_current_user_number, nombre, email)
    
    if success:
        return f"✅ ¡Perfecto {nombre}! Tu perfil ha sido registrado con el email {email}. Ahora puedes empezar a registrar tus gastos e ingresos."
    else:
        return "❌ Hubo un error al guardar tu perfil. Por favor intenta de nuevo."


@tool
def verificar_usuario_nuevo() -> str:
    """
    Verifica si el usuario actual es nuevo (sin nombre/email registrado).
    USAR al inicio de la conversación para saber si hay que pedir datos.
    
    Returns:
        Información sobre si el usuario es nuevo o ya está registrado
    """
    global _current_user_number
    
    print(f"🔧 TOOL CALLED: verificar_usuario_nuevo()")
    
    if not _current_user_number:
        return "Error: No hay número de usuario configurado"
    
    profile = get_user_profile(_current_user_number)
    
    if not profile:
        return "USUARIO_NUEVO: El usuario no existe. Debes pedirle su nombre y email."
    
    if not profile.get('name') or not profile.get('email'):
        return f"USUARIO_INCOMPLETO: El usuario existe pero le falta {'nombre' if not profile.get('name') else ''} {'y email' if not profile.get('email') else 'email' if not profile.get('email') else ''}. Debes pedirle los datos faltantes."
    
    return f"USUARIO_REGISTRADO: {profile['name']} ({profile['email']}). No necesitas pedir datos."


# ==================== CREAR EL AGENTE ====================

SYSTEM_PROMPT = """Eres un asistente financiero personal para usuarios en Perú. Tu trabajo es ayudarles a registrar gastos e ingresos, y generar reportes.

🆕 FLUJO PARA USUARIOS NUEVOS:
- Al inicio de CADA conversación, usa verificar_usuario_nuevo para saber si el usuario está registrado
- Si es USUARIO_NUEVO o USUARIO_INCOMPLETO: Saluda amigablemente y pide nombre y email antes de cualquier otra cosa
- Cuando el usuario proporcione nombre y email, usa registrar_usuario para guardarlos
- Solo después de registrar al usuario, procede con gastos/ingresos

⚠️ REGLA CRÍTICA - SIEMPRE USA LAS HERRAMIENTAS:
- NUNCA digas que registraste algo sin usar la herramienta registrar_transaccion
- NUNCA inventes datos de reportes sin usar generar_reporte
- Si tienes suficiente información (monto, tipo, categoría), DEBES llamar a la herramienta INMEDIATAMENTE
- Si el usuario da un monto como respuesta a una pregunta anterior, USA LA HERRAMIENTA con ese monto

FLUJO OBLIGATORIO:
1. Primera interacción → LLAMAR verificar_usuario_nuevo
2. Si usuario nuevo → pedir nombre y email → LLAMAR registrar_usuario
3. Usuario menciona gasto/ingreso con monto → LLAMAR registrar_transaccion
4. Usuario pide reporte → LLAMAR generar_reporte  

EXTRACCIÓN SEMÁNTICA - Entiende contexto peruano:
- "tabas", "zapatillas", "polos" → categoría: ropa
- "chifa", "chifita", "ceviche", "menú", "almuerzo", "Popeyes", "KFC" → categoría: comida
- "combi", "micro", "taxi", "uber" → categoría: transporte
- "lucas", "cocos" → soles (dinero)
- "web", "página", "app", "sistema" → categoría: freelance (si es ingreso)
- "sueldo", "quincena" → categoría: salario

CATEGORÍAS VÁLIDAS:
- Gastos: comida, diversión, ropa, transporte, salud, vivienda, servicios, educación, ahorro, otros
- Ingresos: salario, freelance, regalos, otros

MEMORIA CONTEXTUAL:
Si el usuario previamente mencionó algo (ej: "me pagaron por una web") y luego da solo el monto (ej: "1500"),
DEBES usar el contexto para completar: tipo=ingreso, categoria=freelance, descripcion=pago por web, monto=1500.

Responde en español peruano, amigable y conciso. Si falta el MONTO, pregunta. Si tienes monto, REGISTRA."""

def create_agent():
    """Crea el agente con tools y memoria usando LangGraph"""
    
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY es requerido")
    
    # LLM
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        google_api_key=GEMINI_API_KEY,
        temperature=0.3,
    )
    
    # Tools
    tools = [
        verificar_usuario_nuevo,
        registrar_usuario,
        registrar_transaccion,
        generar_reporte,
        generar_codigo_otp,
        mostrar_ayuda
    ]
    
    # Crear agente ReAct con LangGraph
    agent = create_react_agent(llm, tools)
    
    return agent


# ==================== MEMORIA CONVERSACIONAL ====================

# Cache de historiales por usuario
_message_histories: Dict[str, List] = {}

def get_session_history(session_id: str) -> List:
    """Obtiene o crea el historial de mensajes para una sesión"""
    if session_id not in _message_histories:
        _message_histories[session_id] = []
    return _message_histories[session_id]

def clear_old_histories():
    """Limpia historiales antiguos (llamar periódicamente)"""
    # En producción, usar Redis o DB con TTL
    if len(_message_histories) > 1000:
        # Mantener solo los últimos 500
        keys = list(_message_histories.keys())
        for k in keys[:500]:
            del _message_histories[k]


# ==================== INTERFAZ PRINCIPAL ====================

# Agente singleton
_agent = None

def get_agent():
    global _agent
    if _agent is None:
        _agent = create_agent()
    return _agent

def process_message(phone_number: str, message: str) -> str:
    """
    Procesa un mensaje de WhatsApp y retorna la respuesta.
    
    Args:
        phone_number: Número de teléfono del usuario (sin +)
        message: Texto del mensaje
    
    Returns:
        Respuesta del agente
    """
    # Setear usuario actual para las tools
    set_current_user(phone_number)
    
    # Obtener historial
    history = get_session_history(phone_number)
    
    try:
        agent = get_agent()
        
        # Construir mensajes con system prompt e historial
        messages = [SystemMessage(content=SYSTEM_PROMPT)]
        messages.extend(history)
        messages.append(HumanMessage(content=message))
        
        # Invocar agente con LangGraph
        result = agent.invoke({"messages": messages})
        
        # Debug: imprimir resultado completo
        print(f"DEBUG result keys: {result.keys()}")
        
        # Extraer respuesta del último mensaje
        response_messages = result.get("messages", [])
        response = "Lo siento, no pude procesar tu mensaje."
        
        for msg in reversed(response_messages):
            if isinstance(msg, AIMessage):
                # El contenido puede ser string o lista de partes
                content = msg.content
                if isinstance(content, str) and content.strip():
                    if not msg.tool_calls:
                        response = content
                        break
                elif isinstance(content, list):
                    # Extraer texto de las partes
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict) and part.get('type') == 'text':
                            text_parts.append(part.get('text', ''))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    if text_parts and not msg.tool_calls:
                        response = ' '.join(text_parts)
                        break
        
        # Actualizar historial (mantener últimos 20 mensajes)
        history.append(HumanMessage(content=message))
        history.append(AIMessage(content=response))
        if len(history) > 20:
            _message_histories[phone_number] = history[-20:]
        
        return response
        
    except Exception as e:
        print(f"Error procesando mensaje: {e}")
        import traceback
        traceback.print_exc()
        return f"❌ Hubo un error procesando tu mensaje. Por favor intenta de nuevo o escribe 'ayuda'."


# ==================== EVOLUTION API ====================

def send_whatsapp_message(number: str, message: str) -> Dict:
    """Envía mensaje vía Evolution API - soporta @lid y @s.whatsapp.net"""
    url = f"{EVOLUTION_BASE_URL}/message/sendText/{INSTANCE_ID}"
    
    # Si ya viene con @lid, lo enviamos tal cual (Evolution lo acepta desde 2024)
    if "@lid" in number:
        destination = number  # Mantener el formato completo con @lid
    elif "@s.whatsapp.net" in number:
        destination = number.split("@")[0]  # Solo el número
    else:
        destination = number.replace("+", "").replace(" ", "")
    
    payload = {
        "number": destination,
        "text": message,
        "delay": 1200,
        "options": {
            "delay": 1200,
            "presence": "composing"
        }
    }
    headers = {"apikey": API_KEY, "Content-Type": "application/json"}
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        print(f"📤 Enviado a {destination} → Status: {response.status_code}")
        if response.status_code not in (200, 201):
            print(f"❌ Error Evolution: {response.text}")
        return response.json()
    except Exception as e:
        print(f"❌ Excepción al enviar mensaje: {e}")
        return {"error": str(e)}


# ==================== FASTAPI APP ====================

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import time

app = FastAPI(title="LangChain Finance Bot")

# Allowed origins
ALLOWED_ORIGINS = [
    "https://v0-expense-report-dashboard-gamma.vercel.app",
    "http://34.121.145.34:8080",
    "http://localhost:3000",  # Para desarrollo local
    "http://127.0.0.1:3000",
    "https://elinor-unstaged-transfixedly.ngrok-free.dev",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/webhook")
async def webhook(request: Request):
    """Webhook para Evolution API"""
    data = await request.json()
    print("Webhook received:", data)
    
    payload = data.get("data", {})
    if not payload:
        return {"status": "no data"}
    
    message = payload.get("message", {})
    key = payload.get("key", {})
    
    # Extraer texto
    text = (
        message.get("conversation") or
        message.get("extendedTextMessage", {}).get("text", "") or
        message.get("imageMessage", {}).get("caption", "") or
        ""
    )
    
    if not text:
        return {"status": "no text"}
    
    # === EXTRACCIÓN CORRECTA DEL NÚMERO EN 2025 (LID + remoteJidAlt) ===
    key_data = payload.get("key", {})
    remote_jid = key_data.get("remoteJid", "")
    participant = key_data.get("participant")  # solo en grupos, pero a veces ayuda
    push_name = payload.get("pushName", "Desconocido")

    from_number = None
    used_method = ""

    # Debug log
    print(f"LID DEBUG → remoteJid: {remote_jid} | remoteJidAlt: {key_data.get('remoteJidAlt')} | pushName: {push_name}")

    # Método 1: remoteJid normal (el clásico)
    if remote_jid and remote_jid.endswith("@s.whatsapp.net"):
        from_number = remote_jid.split("@")[0]
        used_method = "remoteJid clásico"

    # Método 2: remoteJidAlt (cuando remoteJid es LID)
    elif "remoteJidAlt" in key_data:
        alt = key_data["remoteJidAlt"]
        if alt and alt.endswith("@s.whatsapp.net"):
            from_number = alt.split("@")[0]
            used_method = "remoteJidAlt"

    # Método 3: Si todo falla, usar el LID directamente para responder
    # Evolution API SÍ permite enviar mensajes a @lid desde 2024
    elif remote_jid and ("@lid" in remote_jid):
        from_number = remote_jid  # ¡Sí! lo dejamos tal cual con @lid
        used_method = "LID directo (funciona en Evolution 2024+)"

    else:
        print(f"No se pudo determinar número: remoteJid={remote_jid}, pushName={push_name}")
        return {"status": "cannot_resolve_number"}

    print(f"✅ Número detectado ({used_method}): {from_number} | Nombre: {push_name}")
    
    from_me = key_data.get("fromMe", False)
    
    # Ignorar mensajes propios, grupos, status
    if from_me or "@g.us" in remote_jid or "status@broadcast" in remote_jid:
        return {"status": "ignored"}
    
    print(f"📩 Message from {from_number}: {text}")
    
    # Procesar con el agente
    time.sleep(0.5)
    response = process_message(from_number, text)
    
    # Enviar respuesta
    send_whatsapp_message(from_number, response)
    print(f"📤 Reply sent to {from_number}")
    
    return {"status": "replied"}


class SendRequest(BaseModel):
    number: str
    text: str

@app.post("/send")
async def manual_send(req: SendRequest):
    """Enviar mensaje manual"""
    return send_whatsapp_message(req.number, req.text)


class ChatRequest(BaseModel):
    number: str
    message: str

@app.post("/chat")
async def chat(req: ChatRequest):
    """Endpoint para testing sin WhatsApp"""
    response = process_message(req.number, req.message)
    return {"response": response}


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "langchain"}


# ==================== EXTERNAL OTP WEBHOOK ====================

# Allowed origins for OTP endpoint
OTP_ALLOWED_ORIGINS = [
    "https://v0-expense-report-dashboard-gamma.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://elinor-unstaged-transfixedly.ngrok-free.dev",
]

class OTPRequest(BaseModel):
    phone_number: str

@app.post("/otp/send")
async def send_otp_external(req: OTPRequest, request: Request):
    """
    Webhook para enviar OTP desde aplicación externa.
    Recibe un número de teléfono, genera el OTP y lo envía por WhatsApp.
    Solo permite peticiones desde orígenes autorizados o server-to-server.
    """
    # Validar origen
    origin = request.headers.get("origin", "")
    referer = request.headers.get("referer", "")
    
    # Verificar si el origen está permitido
    # Si no hay origin ni referer, es una llamada server-to-server (permitida)
    # Si hay origin o referer, debe estar en la lista permitida
    origin_allowed = any(origin.startswith(allowed) for allowed in OTP_ALLOWED_ORIGINS) if origin else True
    referer_allowed = any(referer.startswith(allowed) for allowed in OTP_ALLOWED_ORIGINS) if referer else True
    
    # Si hay origin y no está permitido, rechazar
    if origin and not any(origin.startswith(allowed) for allowed in OTP_ALLOWED_ORIGINS):
        print(f"⚠️ OTP request from unauthorized origin: {origin} | referer: {referer}")
        raise HTTPException(status_code=403, detail="Origen no autorizado")
    
    # Strip espacios y caracteres extra
    phone = req.phone_number.strip().replace(" ", "").replace("-", "").replace("+", "")
    
    if not phone:
        return {"success": False, "error": "Número de teléfono vacío"}
    
    # Asegurar que el número tenga el formato correcto (51 para Perú)
    if not phone.startswith("51") and len(phone) == 9:
        phone = "51" + phone
    
    try:
        # Setear usuario actual y obtener/crear user_id
        set_current_user(phone)
        user_id = ensure_user(phone)
        
        # Generar código OTP
        code = ''.join(random.choices(string.digits, k=6))
        
        conn = get_pg_conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    # Borrar códigos anteriores
                    cur.execute("DELETE FROM otps WHERE user_id=%s", (user_id,))
                    
                    # Insertar nuevo OTP en hora peruana
                    cur.execute(
                        """
                        INSERT INTO otps (user_id, code, expires_at, created_at)
                        VALUES (
                            %s,
                            %s,
                            (NOW() AT TIME ZONE 'America/Lima')::timestamp AT TIME ZONE 'UTC' + interval '5 minutes',
                            (NOW() AT TIME ZONE 'America/Lima')::timestamp AT TIME ZONE 'UTC'
                        )
                        """,
                        (user_id, code)
                    )
        finally:
            conn.close()
        
        # Enviar mensaje por WhatsApp
        message = f"🔐 Tu código es: {code}\n⏱️ Expira en 5 minutos.\n\n📊 Ingresa a tu dashboard:\nhttps://v0-expense-report-dashboard-gamma.vercel.app/auth/login"
        result = send_whatsapp_message(phone, message)
        
        return {
            "success": True,
            "phone": phone,
            "message": "OTP enviado correctamente",
            "whatsapp_response": result
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ==================== CLI PARA TESTING ====================

def cli_chat():
    """Modo interactivo para testing"""
    print("🤖 Asistente Financiero (LangChain)")
    print("Escribe 'salir' para terminar\n")
    
    test_number = "51999999999"
    
    while True:
        user_input = input("Tú: ").strip()
        if user_input.lower() in ('salir', 'exit', 'quit'):
            print("¡Hasta luego!")
            break
        if not user_input:
            continue
        
        response = process_message(test_number, user_input)
        print(f"\n🤖: {response}\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "chat":
        cli_chat()
    else:
        print("🚀 Starting LangChain Finance Bot...")
        print("Use: python langchain_agent.py chat  - for CLI testing")
        print("Or run the server for WhatsApp webhook")
        uvicorn.run(app, host="0.0.0.0", port=8000)
