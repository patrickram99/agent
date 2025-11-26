import os
import re
from datetime import datetime
from typing import Optional, Dict, Any

from dotenv import load_dotenv
import google.generativeai as genai

# Categories and constraints for consistent outputs
CATEGORIES_GASTO = [
    "comida", "diversión", "ropa", "transporte", "salud",
    "vivienda", "servicios", "educación", "ahorro", "otros"
]
CATEGORIES_INGRESO = ["salario", "freelance", "regalos", "otros"]

SYSTEM_INSTRUCTIONS = (
    "Eres un asistente financiero para usuarios en Perú. Idioma: español. "
    "Tu tarea: extraer campos estructurados desde un texto libre del usuario. "
    "Moneda preferida: soles (PEN). Si detectas otra moneda, marca currency='OTHER'. "
    "Devuelve un JSON estricto con campos: {\n"
    "  type: 'gasto'|'ingreso'|null,\n"
    "  amount: float|null,\n"
    "  currency: 'PEN'|'OTHER'|'UNKNOWN',\n"
    "  category: one of %s or %s (según type) o 'otros',\n"
    "  description: string original,\n"
    "  date_text: string breve con la referencia temporal detectada (e.g., 'ayer', '12/11'),\n"
    "} "
    "Respeta categorías fijas y nunca inventes nuevas. Usa español."
) % (CATEGORIES_GASTO, CATEGORIES_INGRESO)

load_dotenv()  # load .env if present
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

class GeminiParser:
    def __init__(self, api_key: Optional[str] = None):
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY es requerido para usar GeminiParser")
        genai.configure(api_key=api_key)
        # Validate model exists and supports generateContent; fallback if needed
        available = genai.list_models()
        target = None
        for m in available:
            # m.name like 'models/gemini-1.5-flash'
            name = m.name.split('/')[-1]
            if name == MODEL_NAME and 'generateContent' in getattr(m, 'supported_generation_methods', []):
                target = m.name
                break
        if target is None:
            # pick first model supporting generateContent
            for m in available:
                if 'generateContent' in getattr(m, 'supported_generation_methods', []):
                    target = m.name
                    break
        if target is None:
            raise RuntimeError("No hay modelos de Gemini disponibles para generateContent")
        self.model = genai.GenerativeModel(target)

    def parse(self, text: str) -> Dict[str, Any]:
        prompt = (
            SYSTEM_INSTRUCTIONS + "\n\n" +
            "Texto del usuario:" + "\n" + text + "\n\n" +
            "Responde SOLO con JSON sin comentarios ni texto adicional."
        )
        result = self.model.generate_content(prompt)
        content = result.text.strip()
        # Attempt to extract JSON safely
        # Remove markdown fences if any
        content = re.sub(r"```json|```", "", content).strip()
        import json
        try:
            data = json.loads(content)
        except Exception:
            # Fallback minimal structure
            data = {
                "type": None,
                "amount": None,
                "currency": "UNKNOWN",
                "category": "otros",
                "description": text,
                "date_text": None,
            }
        # Post-process constraints
        tx_type = data.get("type")
        cat = (data.get("category") or "otros").lower()
        if tx_type == "gasto" and cat not in CATEGORIES_GASTO:
            data["category"] = "otros"
        elif tx_type == "ingreso" and cat not in CATEGORIES_INGRESO:
            data["category"] = "otros"
        # Normalize currency
        cur = (data.get("currency") or "UNKNOWN").upper()
        if cur not in ("PEN", "OTHER", "UNKNOWN"):
            data["currency"] = "UNKNOWN"
        # Amount to float
        amt = data.get("amount")
        if isinstance(amt, str):
            try:
                data["amount"] = float(amt.replace(",", "."))
            except Exception:
                data["amount"] = None
        return data

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python gemini_parser.py 'texto en español' ")
        sys.exit(1)
    parser = GeminiParser()
    output = parser.parse(sys.argv[1])
    print(output)
