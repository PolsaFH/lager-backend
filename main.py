from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Optional
import uvicorn

from database import init_db, get_all_components, add_component, update_component_quantity, delete_component, get_component_by_id
from ai import analyze_image

app = FastAPI(title="Lager API", version="1.0.0")

# CORS — tillat kall fra React Native appen og Home Assistant
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    init_db()

# --- Helsesjekk ---
@app.get("/")
def root():
    return {"status": "ok", "message": "Lager API kjører"}

# --- Analyser bilde med Claude AI ---
@app.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Filen må være et bilde")

    image_data = await file.read()
    result = analyze_image(image_data, file.content_type)
    return result

# --- Hent alle komponenter ---
@app.get("/components")
def get_components():
    components = get_all_components()
    return {"components": components}

# --- Hent én komponent ---
@app.get("/components/{component_id}")
def get_component(component_id: int):
    component = get_component_by_id(component_id)
    if not component:
        raise HTTPException(status_code=404, detail="Komponent ikke funnet")
    return component

# --- Legg til ny komponent ---
@app.post("/components")
async def create_component(
    name: str = Form(...),
    category: str = Form(...),
    quantity: int = Form(...),
    location: str = Form(""),
    description: str = Form(""),
    image: Optional[UploadFile] = File(None)
):
    image_data = None
    image_content_type = None

    if image:
        image_data = await image.read()
        image_content_type = image.content_type

    component_id = add_component(
        name=name,
        category=category,
        quantity=quantity,
        location=location,
        description=description,
        image_data=image_data,
        image_content_type=image_content_type
    )
    return {"id": component_id, "message": "Komponent lagt til"}

# --- Oppdater antall ---
@app.patch("/components/{component_id}/quantity")
def update_quantity(component_id: int, quantity: int):
    success = update_component_quantity(component_id, quantity)
    if not success:
        raise HTTPException(status_code=404, detail="Komponent ikke funnet")
    return {"message": "Antall oppdatert"}

# --- Slett komponent ---
@app.delete("/components/{component_id}")
def remove_component(component_id: int):
    success = delete_component(component_id)
    if not success:
        raise HTTPException(status_code=404, detail="Komponent ikke funnet")
    return {"message": "Komponent slettet"}

# --- Home Assistant endepunkt — enkel JSON-oversikt ---
@app.get("/ha/summary")
def ha_summary():
    components = get_all_components()
    summary = {}
    for c in components:
        summary[c["name"]] = c["quantity"]
    return {
        "total_components": len(components),
        "inventory": summary
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
