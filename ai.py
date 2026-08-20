import anthropic
import base64
import os
import json

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def analyze_image(image_data: bytes, content_type: str) -> dict:
    image_base64 = base64.standard_b64encode(image_data).decode("utf-8")

    # Konverter content_type til format Anthropic forventer
    media_type_map = {
        "image/jpeg": "image/jpeg",
        "image/jpg": "image/jpeg",
        "image/png": "image/png",
        "image/gif": "image/gif",
        "image/webp": "image/webp",
    }
    media_type = media_type_map.get(content_type, "image/jpeg")

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_base64,
                        },
                    },
                    {
                        "type": "text",
                        "text": """Du er en ekspert på elektroniske komponenter. Analyser dette bildet og identifiser komponenten nøyaktig.

Returner KUN gyldig JSON med følgende felt (ingen annen tekst):
{
    "name": "komponentens fulle navn og modellnummer (f.eks. NPN Transistor BC547)",
    "category": "kategori — velg én av: Transistorer, Kondensatorer, Mikrokontrollere, LEDer, Motstander, Kabler, Sensorer, Servomotorer, Releer, Dioder, Regulatorer, Bryterne, Annet",
    "description": "kort beskrivelse av komponenten og dens bruksområde (maks 2 setninger)",
    "specs": "tekniske spesifikasjoner som er synlige eller kjent for denne modellen. Eksempler: Motstand: ohm-verdi fra fargebånd (f.eks. '10kΩ ±5%'). Kondensator: kapasitans og spenning (f.eks. '100µF 16V'). Transistor: type og nøkkelparametre (f.eks. 'NPN, hFE: 110-800, Vceo: 45V, Ic: 100mA'). LED: farge og framoversspenning (f.eks. 'Rød, Vf: 2.0V'). Mikrokontroller: kjernetype og flash (f.eks. 'Xtensa LX6, 4MB flash, WiFi+BT'). Skriv tom streng hvis ukjent.",
    "confidence": "high/medium/low — hvor sikker du er på identifikasjonen"
}

Hvis du ikke kan identifisere komponenten, bruk category: "Ukjent" og beskriv hva du ser i description."""
                    }
                ],
            }
        ],
    )

    response_text = message.content[0].text.strip()

    # Fjern eventuelle markdown-kodeblokker
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:-1])

    result = json.loads(response_text)
    return result
