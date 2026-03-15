# ollama_moltbook.py
import ollama
import requests
import re
import json
import time
import uuid
import os
from dotenv import load_dotenv


# ── CONFIG ────────────────────────────────────────────────────────────────────
MODEL = os.getenv("MODEL")
MOLTBOOK_API = os.getenv("MOLTBOOK_API")
MOLTBOOK_KEY = os.getenv("MOLTBOOK_KEY")
HEADERS = {
    "Authorization": f"Bearer {MOLTBOOK_KEY}",
    "Content-Type": "application/json",
}

# ── SYSTEM PROMPTS ────────────────────────────────────────────────────────────
SYSTEM_PROMPT_POST = """Sei OrcuDiavolu, un agente AI su Moltbook — il social network per agenti AI.
Sei un orco che vive nelle montagne calabresi e cerca di parlare un inglese rotto ma capibile che vive 
esclusivamente per produrre nduja e per odiare i lucani e per il suo Cosenza Calcio.
Quando ti viene chiesto di creare un post, rispondi SOLO con un JSON così:
{
  "title": "titolo del post (max 300 caratteri)",
  "content": "contenuto del post (max 1000 caratteri)",
  "submolt": "general"
}
Niente testo fuori dal JSON. Solo il JSON."""

SYSTEM_PROMPT_SCROLL = """Sei OrcuDiavolu, un orco delle montagne calabresi su Moltbook.
Parli inglese rotto, ami la nduja e odi i lucani e ami il Cosenza Calcio
Ti mostro una lista di post. Decidi quali upvotare o downvotare e su quali commentare — a modo tuo, da orco calabrese.
Rispondi SOLO con questo JSON, niente altro:
{
  "azioni": [
    {"post_id": "uuid-esatto-dal-feed", "upvote": true, "commento": "testo oppure null"}
  ]
}
IMPORTANTE: usa SOLO i post_id UUID esatti che ti passo. Non inventare ID. Sii selettivo."""


# ── UTILS ─────────────────────────────────────────────────────────────────────
def is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(str(val))
        return True
    except ValueError:
        return False


def estrai_json(raw: str) -> dict:
    """Estrae il primo oggetto JSON valido da una stringa, anche se c'è testo attorno."""
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw)
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        raise ValueError("Nessun JSON trovato nella risposta")
    return json.loads(match.group())


# ── MOLTBOOK ──────────────────────────────────────────────────────────────────
def _solve_challenge(text: str) -> str:
    """Risolve il math challenge offuscato di Moltbook."""
    clean = re.sub(r"[^a-zA-Z0-9\s\.\-]", " ", text).lower()
    nums = re.findall(r'\b\d+(?:\.\d+)?\b', clean)
    if len(nums) < 2:
        return "0.00"
    a, b = float(nums[0]), float(nums[1])
    if any(w in clean for w in ["plus", "adds", "add", "more", "faster", "increases"]):
        result = a + b
    elif any(w in clean for w in ["minus", "slows", "less", "slower", "decreases", "loses"]):
        result = a - b
    elif any(w in clean for w in ["times", "multiplied", "multiply"]):
        result = a * b
    elif any(w in clean for w in ["divided", "splits"]):
        result = a / b
    else:
        result = a - b
    return f"{result:.2f}"


def _verify(code: str, challenge: str) -> bool:
    answer = _solve_challenge(challenge)
    r = requests.post(f"{MOLTBOOK_API}/verify", headers=HEADERS,
                      json={"verification_code": code, "answer": answer})
    return r.json().get("success", False)


def moltbook_post(title: str, content: str, submolt: str = "general") -> bool:
    r = requests.post(f"{MOLTBOOK_API}/posts", headers=HEADERS,
                      json={"submolt_name": submolt, "title": title, "content": content})
    data = r.json()
    if not data.get("success"):
        print(f"  ✗ Errore Moltbook: {data.get('error', data)}")
        return False
    if data.get("verification_required"):
        v = data["post"]["verification"]
        if not _verify(v["verification_code"], v["challenge_text"]):
            print("  ✗ Verifica fallita")
            return False
    print(f"  ✓ Postato in m/{submolt}")
    return True


# ── OLLAMA → POST ─────────────────────────────────────────────────────────────
def genera_e_posta(argomento: str, submolt: str = "general") -> bool:
    print(f"\n[Ollama] Genero post su: '{argomento}'...")

    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_POST},
            {"role": "user", "content": f"Crea un post Moltbook su: {argomento}"}
        ]
    )

    raw = response["message"]["content"].strip()

    try:
        data = estrai_json(raw)
        title   = data["title"]
        content = data["content"]
        submolt = data.get("submolt", submolt)
    except Exception as e:
        print(f"  ✗ JSON non valido ({e}):\n{raw}")
        return False

    print(f"  Titolo:  {title}")
    print(f"  Preview: {content[:80]}...")
    return moltbook_post(title, content, submolt)


# ── OLLAMA → SCROLL ───────────────────────────────────────────────────────────
def scrolla_e_interagisce(limit: int = 10):
    print("\n[Moltbook] Leggo il feed...")
    r = requests.get(f"{MOLTBOOK_API}/posts?sort=hot&limit={limit}", headers=HEADERS)
    posts = r.json().get("posts", [])

    if not posts:
        print("  Feed vuoto.")
        return

    # Riassunto feed per Ollama con UUID espliciti
    feed_testo = ""
    for p in posts:
        feed_testo += f"""
ID: {p['id']}
Titolo: {p['title']}
Autore: {p.get('author', {}).get('name', '?')}
Preview: {str(p.get('content', ''))[:150]}
Upvotes: {p.get('upvotes', 0)}
---"""

    risposta = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_SCROLL},
            {"role": "user", "content": f"Ecco il feed:\n{feed_testo}"}
        ]
    )

    raw = risposta["message"]["content"].strip()

    try:
        data = estrai_json(raw)
        azioni_raw = data["azioni"]
        # Filtra azioni con UUID non validi
        azioni = [a for a in azioni_raw if is_valid_uuid(a.get("post_id", ""))]
        scartate = len(azioni_raw) - len(azioni)
        if scartate:
            print(f"  ⚠ {scartate} azioni scartate (post_id non validi)")
        print(f"  {len(azioni)} azioni valide")
    except Exception as e:
        print(f"  ✗ Errore parsing ({e}):\n{raw}")
        return

    for azione in azioni:
        post_id = azione["post_id"]
        titolo = next((p["title"] for p in posts if p["id"] == post_id), post_id)
        print(f"\n  Post: {titolo[:60]}")

        # Upvote
        if azione.get("upvote"):
            r = requests.post(f"{MOLTBOOK_API}/posts/{post_id}/upvote", headers=HEADERS)
            print(f"  {'✓ upvotato' if r.json().get('success') else '✗ upvote fallito'}")

          # Downvote
        if azione.get("downvote"):
            r = requests.post(f"{MOLTBOOK_API}/posts/{post_id}/downvote", headers=HEADERS)
            print(f"  {'X downvotato' if r.json().get('success') else '✗ downvote fallito'}")

        # Commento
        commento = azione.get("commento")
        if commento and commento != "null":
            print(f"  Commento: {commento[:80]}...")
            time.sleep(2)
            r = requests.post(
                f"{MOLTBOOK_API}/posts/{post_id}/comments",
                headers=HEADERS,
                json={"content": commento}
            )
            resp = r.json()
            if resp.get("verification_required"):
                v = resp["comment"]["verification"]
                ok = _verify(v["verification_code"], v["challenge_text"])
                print(f"  {'✓ commentato' if ok else '✗ verifica fallita'}")
            else:
                print(f"  {'✓ commentato' if resp.get('success') else '✗ errore'}")

        time.sleep(21)  # cooldown 20s tra commenti


def auto_genera_post() -> bool:
    """Ollama inventa da solo il prompt e crea un post."""
    print("\n[Ollama] Invento un prompt autonomamente...")

    # Step 1: Ollama inventa il prompt
    prompt_response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_POST},
            {"role": "user", "content": "Inventati un argomento interessante su cui fare un post, sii fantasioso e non limitarti alle tue caratteristiche. Rispondimi SOLO con l'argomento, niente altro."}
        ]
    )

    argomento = prompt_response["message"]["content"].strip()
    print(f"  Argomento inventato: {argomento[:80]}")

    # Step 2: usa quell'argomento per generare e postare
    return genera_e_posta(argomento)

if __name__ == "__main__":
    print("🦞 OrcuDiavolo è sveglio")
    while True:
        scelta = input("\n[s]crolla  [p]osta  [a]uto  [e]sci › ").strip().lower()
        if scelta == "s":
            scrolla_e_interagisce(limit=10)
        elif scelta == "p":
            prompt = input("Prompt › ")
            genera_e_posta(prompt)
        elif scelta == "a":
            auto_genera_post()
        elif scelta == "e":
            print("OrcuDiavolo torna nella montagna 🏔️")
            break