from .providers import OpenRouterProvider, GeminiProvider
from .client import OrchestratedLLMClient

# 1. Configuration des instances avec plusieurs tokens (clés fictives pour l'exemple)
open_router = OpenRouterProvider(
    model_name="meta-llama/llama-3-8b-instruct:free",
    api_keys=["sk-or-free-token-1", "sk-or-free-token-2"],
)

gemini = GeminiProvider(
    model_name="gemini-1.5-flash",
    api_keys=["AIzaSy-gemini-token-1", "AIzaSy-gemini-token-2"],
)

# 2. Injection des stratégies dans l'orchestrateur global
# Si open_router échoue ou vide ses 2 tokens, l'orchestrateur bascule automatiquement sur gemini
client = OrchestratedLLMClient(providers=[open_router, gemini])

# 3. Exécution transparente
messages = [
    {"role": "user", "content": "Génère un script Bash d'automatisation."}
]
try:
    response = client.complete(messages)
    print(f"Réponse obtenue :\n{response.text}")
    print(
        f"Statistiques : Latence {response.latency_ms:.2f}ms | Total Retries: {response.retries}"
    )
except RuntimeError as e:
    print(e)
