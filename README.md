# Asistente financiero por WhatsApp (MVP)

Este MVP usa Evolution como proveedor de WhatsApp y FastAPI para exponer el webhook.
Permite:
- Registrar gastos/ingresos en español (PEN – soles).
- Generar reportes (semanal, mensual, anual) vía WhatsApp.
- Solicitar un código OTP (6 dígitos) para autenticación en la web.
- Endpoint de verificación de OTP para la web.

## Variables de entorno

- `EVOLUTION_BASE_URL` (default: `http://34.121.145.34:8080`)
- `INSTANCE_ID` (default: `ConstruccionSOftware`)
- `EVOLUTION_API_KEY`
- `WEBHOOK_URL` (informativo)
- `DATABASE_URL` (default: `sqlite:///./agent.db`). Para producción: `postgres://user:pass@host:port/db`.
- `TIMEZONE` (default: `America/Lima`)

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn evolution_bot:app --host 0.0.0.0 --port 8000
```

## Inicializar Base de Datos (PostgreSQL)

Exporta tu `DATABASE_URL` (URI de libpq) y ejecuta:

```bash
chmod +x scripts/init_db.sh
DATABASE_URL=postgresql://USER:PASS@HOST[:PORT]/DB ./scripts/init_db.sh
```

Nota: Se admiten ambos esquemas `postgresql://` y `postgres://`.

En este MVP, el runtime usa PostgreSQL. Aporta tu `DATABASE_URL` para operar.

## Webhook

- `POST /webhook`: Mensajes entrantes desde Evolution. Responde en español.
- Soporta:
  - Registrar gastos/ingresos: "gasto S/25 en comida ayer", "ingreso S/150 por freelance hoy".
  - `reporte semanal|mensual|anual`: totales y desglose por categoría.
  - `ayuda`: guía rápida y ejemplos.
  - `código`: genera OTP (6 dígitos, TTL 5 min, rate limit 3/h).

## OTP

- `POST /otp/verify` con body `{ "number": "519xxxxxxxx", "code": "123456" }`.
- Respuesta `{ valid: true, user_id: N }` y marca el OTP como usado.

## Notas de producto

- Idioma: solo español. Si el texto no es español, responde: "No soportamos este idioma todavía, gracias por elegirnos."
- Moneda: soles (PEN). Si se detecta otra moneda, responde: "Ingresa el valor aproximado en soles (PEN) para poder guardarlo."
- Reporte semanal: desde el último lunes hasta hoy.
- Reporte mensual: mes anterior completo.
- Reporte anual: año en curso.
- Los reportes por WhatsApp son resumidos; para detalle, usar la web en Vercel.

## Gemini AI Integration

El bot puede usar Gemini para procesamiento de lenguaje natural avanzado:
- Configura `USE_GEMINI=true` y `GEMINI_API_KEY` para habilitar
- Usa `GeminiParser` de `gemini_parser.py` para extracción semántica
- Fallback a reglas determinísticas si Gemini falla

---

# 🚀 Despliegue en Google Cloud Run

## Prerequisitos

1. **Google Cloud SDK**: Instalar desde https://cloud.google.com/sdk/docs/install
2. **Proyecto GCP**: Crear un proyecto en Google Cloud Console
3. **PostgreSQL**: Una base de datos PostgreSQL accesible (Cloud SQL, Supabase, etc.)
4. **API Keys**: 
   - Gemini API Key: https://makersuite.google.com/app/apikey
   - Evolution API Key (ya configurado)

## Archivos de Deployment

| Archivo | Descripción |
|---------|-------------|
| `Dockerfile` | Imagen Docker optimizada para Cloud Run |
| `.dockerignore` | Excluye archivos innecesarios del build |
| `cloudbuild.yaml` | Configuración CI/CD para Cloud Build |
| `deploy.sh` | Script automatizado de deployment |

## Dependencias del Proyecto

El bot depende de los siguientes módulos:

```
evolution_bot.py    # Aplicación principal FastAPI + webhook
├── gemini_parser.py    # Parser con Gemini AI (opcional)
├── langchain_agent.py  # Agente conversacional (alternativo)
└── parse_cli.py        # CLI para testing local
```

## Deployment Rápido

### Opción 1: Script Automatizado (Recomendado)

```bash
# Hacer ejecutable el script
chmod +x deploy.sh

# Ejecutar deployment
./deploy.sh TU_PROJECT_ID us-central1
```

El script automáticamente:
- Habilita las APIs necesarias
- Configura secretos en Secret Manager
- Construye la imagen Docker
- Despliega en Cloud Run

### Opción 2: Deployment Manual

```bash
# 1. Configurar proyecto
gcloud config set project TU_PROJECT_ID
PROJECT_ID=$(gcloud config get-value project)
REGION=us-central1

# 2. Habilitar APIs
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    containerregistry.googleapis.com \
    secretmanager.googleapis.com

# 3. Crear secretos (una sola vez)
echo -n "postgresql://user:pass@host:5432/db" | \
    gcloud secrets create DATABASE_URL --data-file=-

echo -n "tu-gemini-api-key" | \
    gcloud secrets create GEMINI_API_KEY --data-file=-

echo -n "tu-evolution-api-key" | \
    gcloud secrets create EVOLUTION_API_KEY --data-file=-

# 4. Construir imagen
gcloud builds submit --tag gcr.io/$PROJECT_ID/whatsapp-finance-agent

# 5. Desplegar
gcloud run deploy whatsapp-finance-agent \
    --image gcr.io/$PROJECT_ID/whatsapp-finance-agent:latest \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --memory 512Mi \
    --set-env-vars "USE_GEMINI=true,GEMINI_MODEL=gemini-1.5-flash,TIMEZONE=America/Lima" \
    --set-secrets "DATABASE_URL=DATABASE_URL:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,EVOLUTION_API_KEY=EVOLUTION_API_KEY:latest"
```

### Opción 3: CI/CD con Cloud Build

Conecta tu repositorio y usa `cloudbuild.yaml`:

```bash
# Trigger manual
gcloud builds submit --config cloudbuild.yaml \
    --substitutions=_REGION=us-central1
```

## Configuración Post-Deployment

### 1. Obtener URL del Servicio

```bash
gcloud run services describe whatsapp-finance-agent \
    --region us-central1 \
    --format='value(status.url)'
```

### 2. Configurar Webhook en Evolution

Actualiza el webhook en Evolution API a:
```
https://TU-SERVICIO-URL.run.app/webhook
```

### 3. Verificar Deployment

```bash
# Health check
curl https://TU-SERVICIO-URL.run.app/

# Ver logs
gcloud run logs read --service=whatsapp-finance-agent --region=us-central1
```

## Variables de Entorno

| Variable | Descripción | Valor Default |
|----------|-------------|---------------|
| `DATABASE_URL` | URI PostgreSQL | (requerido) |
| `GEMINI_API_KEY` | API Key de Gemini | (requerido para AI) |
| `EVOLUTION_API_KEY` | API Key de Evolution | (requerido) |
| `USE_GEMINI` | Habilitar Gemini AI | `false` |
| `GEMINI_MODEL` | Modelo de Gemini | `gemini-1.5-flash` |
| `EVOLUTION_BASE_URL` | URL de Evolution | `http://34.121.145.34:8080` |
| `INSTANCE_ID` | ID de instancia Evolution | `ConstruccionSOftware` |
| `TIMEZONE` | Zona horaria | `America/Lima` |
| `OTP_TTL_MINUTES` | Minutos de validez del OTP | `5` |

## Costos Estimados

Cloud Run cobra por uso:
- **Gratis**: 2 millones de requests/mes, 360,000 GB-segundos
- **Mínimo recomendado**: ~$5-10/mes para tráfico moderado

## Troubleshooting

### Error: "Secret not found"
```bash
# Verificar secretos existentes
gcloud secrets list

# Crear secreto faltante
echo -n "valor" | gcloud secrets create NOMBRE_SECRETO --data-file=-
```

### Error: "Permission denied"
```bash
# Dar permisos al service account
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding DATABASE_URL \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
```

### Ver logs de errores
```bash
gcloud run logs read --service=whatsapp-finance-agent --region=us-central1 --limit=50
```

## Testing Local con Docker

```bash
# Construir imagen localmente
docker build -t whatsapp-agent .

# Ejecutar con variables de entorno
docker run -p 8080:8080 \
    -e DATABASE_URL="postgresql://..." \
    -e GEMINI_API_KEY="..." \
    -e EVOLUTION_API_KEY="..." \
    -e USE_GEMINI=true \
    whatsapp-agent
```
