#!/usr/bin/env python
"""
CLI para probar el parser (Gemini o reglas) sin WhatsApp.
Uso:
  python parse_cli.py "texto en español"
Variables:
  USE_GEMINI=true para usar Gemini.
  GEMINI_API_KEY debe estar definido.
"""
import os
import sys
from datetime import datetime

from evolution_bot import parse_type, parse_amount, parse_currency, parse_category, parse_datetime

text = " ".join(sys.argv[1:]).strip()
if not text:
    print("Provee un texto: python parse_cli.py 'gasto S/25 en comida ayer'")
    sys.exit(1)

use_gemini = os.getenv("USE_GEMINI", "false").lower() == "true"
res = {}

if use_gemini and os.getenv("GEMINI_API_KEY"):
    from gemini_parser import GeminiParser
    gp = GeminiParser()
    data = gp.parse(text)
    res.update(data)
    # occurred_at from date_text or full text
    dt_hint = data.get('date_text') or text
    res['occurred_at'] = parse_datetime(dt_hint)
else:
    low = text.lower()
    res['type'] = parse_type(low)
    res['amount'] = parse_amount(low)
    res['currency'] = parse_currency(low)
    res['category'] = parse_category(low, res['type'] or 'gasto') if res['type'] else 'otros'
    res['occurred_at'] = parse_datetime(low)

print({
    'type': res.get('type'),
    'amount': res.get('amount'),
    'currency': res.get('currency'),
    'category': res.get('category'),
    'occurred_at': res.get('occurred_at').strftime('%Y-%m-%d %H:%M:%S'),
    'description': text,
})
