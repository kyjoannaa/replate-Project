from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import listings, beneficiaries, agent, users, deliveries, impact

app = FastAPI(
    title="rePlate API Gateway",
    description="Backend Service & Multi-Agent AI Integration Engine",
    version="1.0.0"
)

# Enable CORS for React Native mobile client integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Semua Routers
app.include_router(listings.router)
app.include_router(beneficiaries.router)
app.include_router(agent.router)
app.include_router(users.router)
app.include_router(users.auth_router)
app.include_router(deliveries.router)
app.include_router(impact.router)

@app.get("/")
def read_root():
    return {
        "status": "online",
        "message": "rePlate Backend Service connected to Supabase PostgreSQL"
    }