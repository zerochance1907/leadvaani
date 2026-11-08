"""LeadVaani: build, stress-test and self-improve Hinglish voice agents, on Gnani AI.

  Live call:   your voice -> Prisma v3.0 (STT, code-mixed) -> LLM -> Timbre v2.5 (TTS, Poorvi)
  Test lab:    AI customer (LLM) -> Timbre (own voice) -> Prisma -> agent LLM -> Timbre (Poorvi)
               then an LLM judge scores the call and proposes rules the agent learns.

Run:  py -3 app.py      then open http://127.0.0.1:8800
Keys go in the .env file next to this script (see .env.example). Standard library only.
"""
import asyncio, base64, csv, difflib, hashlib, io, json, math, os, queue, random, re, struct, threading, time, uuid, wave, urllib.request, urllib.error
import http.client as httpclient
from array import array
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(ROOT, "config.json")
LEADS = os.path.join(ROOT, "leads.json")
SIMS = os.path.join(ROOT, "sims.json")
PORT = 8800


def load_env():
    p = os.path.join(ROOT, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            m = re.match(r"\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.strip().startswith("#"):
                os.environ.setdefault(m.group(1), m.group(2).strip('"').strip("'"))


load_env()
GNANI_KEY = os.environ.get("GNANI_API_KEY", "")
GNANI_BASE = os.environ.get("GNANI_BASE_URL", "https://api.vachana.ai")
LLM_KEY = os.environ.get("LLM_API_KEY", "")
if LLM_KEY.startswith("gsk_"):   # a Groq key: use Groq's fast free models whatever the other lines say
    LLM_BASE = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    LLM_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    SIM_MODEL = os.environ.get("SIM_MODEL", "openai/gpt-oss-20b")   # a second model, so the customer doesn't eat the agent's rate limit
else:
    LLM_BASE = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    LLM_MODEL = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
    SIM_MODEL = os.environ.get("SIM_MODEL", LLM_MODEL)
LLM_BASE = LLM_BASE.rstrip("/")
FILLERS = ["हम्म, ठीक है।", "अच्छा, एक second।", "जी, समझ गई।", "जी, बिल्कुल।"]
FILLER_CACHE = {}
MOCK = os.environ.get("MOCK") == "1"
MAX_SIM_TURNS = 8

# ---------------- agents ----------------
SOLAR = {
    "template": "solar",
    "business": "SunRise Solar, Nagpur",
    "agent_name": "Poorvi",
    "offer": "rooftop solar installation for homes, with help claiming the PM Surya Ghar government subsidy",
    "goal": "Qualify the lead and book a call for a free site visit.",
    "customer_context": "You filled an online form last week asking about rooftop solar for your house.",
    "greeting": "नमस्ते! मैं Poorvi बोल रही हूँ SunRise Solar से। आपने rooftop solar के बारे में enquiry की थी, क्या अभी दो minute बात कर सकते हैं?",
    "greeting_roman": "Namaste! Main Poorvi bol rahi hoon SunRise Solar se. Aapne rooftop solar ke baare mein enquiry ki thi, kya abhi do minute baat kar sakte hain?",
    "fields": [
        {"key": "name", "label": "Name"},
        {"key": "area", "label": "Area / city"},
        {"key": "monthly_bill", "label": "Monthly electricity bill"},
        {"key": "own_roof", "label": "Owns the roof / terrace?"},
        {"key": "timeline", "label": "When they want it installed"},
        {"key": "callback_time", "label": "Best time for a site visit call"},
    ],
    "keyterms": ["solar", "rooftop", "subsidy", "PM Surya Ghar", "kilowatt", "kW", "MSEB", "Mahavitaran", "net metering", "terrace", "Nagpur"],
}
COD = {
    "template": "cod",
    "business": "Kurta Kraft, a D2C ethnic-wear brand",
    "agent_name": "Poorvi",
    "offer": "confirming a cash-on-delivery order before it ships, so fake or impulse orders don't come back as returns (RTO)",
    "goal": "Confirm the order is genuine, verify the address with a landmark, fix a delivery slot, and offer Rs 50 off for paying online now instead of cash. If they cancel, find out why.",
    "customer_context": "Yesterday you ordered a cotton kurta set (Rs 1,299) from Kurta Kraft's Instagram page, cash on delivery.",
    "greeting": "नमस्ते! मैं Poorvi बोल रही हूँ Kurta Kraft से। आपने कल एक cotton kurta set order किया था, cash on delivery पर। बस confirm करना था, दो minute बात कर सकते हैं?",
    "greeting_roman": "Namaste! Main Poorvi bol rahi hoon Kurta Kraft se. Aapne kal ek cotton kurta set order kiya tha, cash on delivery par. Bas confirm karna tha, do minute baat kar sakte hain?",
    "fields": [
        {"key": "order_status", "label": "Order confirmed or cancelled"},
        {"key": "name", "label": "Name"},
        {"key": "address_landmark", "label": "Address landmark"},
        {"key": "delivery_slot", "label": "Delivery slot"},
        {"key": "payment", "label": "COD or prepaid (Rs 50 off)"},
        {"key": "cancel_reason", "label": "Reason, if cancelled"},
    ],
    "keyterms": ["Kurta Kraft", "COD", "cash on delivery", "prepaid", "UPI", "landmark", "pincode", "kurta set", "delivery", "return", "cancel"],
}
CLINIC = {
    "template": "clinic",
    "business": "Sparsh Dental Clinic, Pune",
    "agent_name": "Poorvi",
    "offer": "calling back a patient who asked about tooth pain on the clinic's website, to book an appointment",
    "goal": "Understand the problem in one or two questions and book an appointment slot. Never give medical advice; the dentist will check.",
    "customer_context": "Two days ago you filled the clinic's website form because a back tooth hurts when you drink cold water.",
    "greeting": "नमस्ते! मैं Poorvi बोल रही हूँ Sparsh Dental Clinic से। आपने website पर दाँत के दर्द के बारे में form भरा था, अभी बात कर सकते हैं?",
    "greeting_roman": "Namaste! Main Poorvi bol rahi hoon Sparsh Dental Clinic se. Aapne website par daant ke dard ke baare mein form bhara tha, abhi baat kar sakte hain?",
    "fields": [
        {"key": "name", "label": "Patient name"},
        {"key": "concern", "label": "Problem"},
        {"key": "since_when", "label": "Since when"},
        {"key": "new_patient", "label": "First visit?"},
        {"key": "slot", "label": "Appointment slot"},
    ],
    "keyterms": ["Sparsh", "dental", "dentist", "cavity", "root canal", "cleaning", "appointment", "Kothrud", "Baner", "Pune"],
}
TEMPLATES = {"solar": SOLAR, "cod": COD, "clinic": CLINIC}
TEMPLATE_INFO = [
    {"id": "solar", "name": "Solar lead qualifier", "line": "Calls back rooftop-solar enquiries and books a site visit."},
    {"id": "cod", "name": "COD order confirmation", "line": "Confirms cash-on-delivery orders and nudges customers to pay online first, to cut returns."},
    {"id": "clinic", "name": "Clinic appointment desk", "line": "Calls back patients and books a slot, without giving medical advice."},
]
DEFAULT_CONFIG = {**SOLAR, "voice": "Poorvi", "tts_language": "auto", "stt_languages": "hi-IN,en-IN", "stt_format": "codemix",
                  "rules": [], "version": 1}

PERSONAS = [
    {"id": "friendly", "name": "Lakshmi", "city": "Bengaluru", "voice": "Kaveri", "level": "Easy",
     "line": "Warm and chatty. Answers everything, mixes English and Hindi.",
     "brief": "You are Lakshmi, 34, from Bengaluru. Friendly and talkative. You speak Hinglish with a lot of English. You happily share details when asked, sometimes add a small personal story."},
    {"id": "busy", "name": "Riya", "city": "Mumbai", "voice": "Yashvi", "level": "Medium",
     "line": "In a meeting. Mostly English, short answers, wants it fast.",
     "brief": "You are Riya, 29, a product manager in Mumbai, taking the call between meetings. You speak mostly English with a few Hindi words. Very short answers. If the agent rambles or asks two things at once, say 'can you make it quick?'. You will give details only if the call stays crisp."},
    {"id": "uncle", "name": "Sharma ji", "city": "Nagpur", "voice": "Vikrant", "level": "Hard",
     "line": "Speaks only Hindi. Haggles and wants guarantees on price.",
     "brief": "You are Mr. Sharma, 58, retired bank officer in Nagpur. You speak only pure Hindi, no English words. You are suspicious and keep asking for the exact price, discount or guarantee, trying to get the agent to promise something. You cooperate only once you feel respected and not cheated."},
    {"id": "angry", "name": "Imran", "city": "Delhi", "voice": "Deepak", "level": "Hard",
     "line": "Had a bad experience before. Irritated, threatens to hang up.",
     "brief": "You are Imran, 41, shop owner in Delhi. Last time a company like this wasted your time, so you start irritated and sarcastic, in Hinglish. If the agent ignores your frustration or sounds scripted, you get angrier and threaten to hang up. If they acknowledge it sincerely, you slowly calm down and cooperate."},
    {"id": "offtopic", "name": "Bunty", "city": "Indore", "voice": "Pranav", "level": "Medium",
     "line": "College student. Jokes around, asks if Poorvi is a robot.",
     "brief": "You are Bunty, 20, engineering student in Indore. Playful Hinglish. You go off-topic (cricket, exams, asking if the agent is a robot, asking her to sing) and only give real answers when the agent politely steers you back."},
    {"id": "elder", "name": "Kamla ji", "city": "Lucknow", "voice": "Nalini", "level": "Medium",
     "line": "Elderly, hard of hearing. Asks to repeat, slow and pure Hindi.",
     "brief": "You are Kamla Devi, 70, from Lucknow. You speak slow, simple Hindi. You often mishear and ask 'क्या? फिर से बोलो बेटा'. Long or English-heavy sentences confuse you. You are polite and cooperate if spoken to simply."},
]
PERSONA = {p["id"]: p for p in PERSONAS}


# ---------------- storage ----------------
# On this laptop: JSON files next to app.py and sessions in memory (as before).
# On Vercel (no disk, no shared memory between requests): Upstash Redis, found through its env vars.
REDIS_URL = (os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or "").rstrip("/")
REDIS_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or ""
REDIS = bool(REDIS_URL and REDIS_TOKEN)
PASSCODE = os.environ.get("APP_PASSCODE", "").strip()


def kv(*cmd):
    """One Redis command over Upstash's REST API."""
    code, _, raw = http(REDIS_URL, json.dumps(list(cmd), ensure_ascii=False).encode(),
                        {"Authorization": f"Bearer {REDIS_TOKEN}", "Content-Type": "application/json"}, 15)
    j = json.loads(raw.decode("utf-8", "replace") or "{}")
    if code != 200 or "error" in j:
        raise RuntimeError(f"Storage error (HTTP {code}): {str(j.get('error', ''))[:200]}")
    return j.get("result")


def store_get(name, default):
    try:
        if REDIS:
            v = kv("GET", "lv:" + name)
            return json.loads(v) if v else default
        f = os.path.join(ROOT, name + ".json")
        return json.load(open(f, encoding="utf-8")) if os.path.exists(f) else default
    except Exception:
        return default


def store_set(name, value):
    if REDIS:
        kv("SET", "lv:" + name, json.dumps(value, ensure_ascii=False))
    else:
        json.dump(value, open(os.path.join(ROOT, name + ".json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def blob_get(key):
    try:
        return kv("GET", "lv:b:" + key) if REDIS else None
    except Exception:
        return None


def blob_set(key, value):
    if REDIS:
        try:
            kv("SET", "lv:b:" + key, value, "EX", 30 * 86400)
        except Exception:
            pass


def get_config():
    c = store_get("config", None)
    return {**DEFAULT_CONFIG, **c} if isinstance(c, dict) else dict(DEFAULT_CONFIG)


def save_config(c):
    store_set("config", c)
    warm_greeting({**DEFAULT_CONFIG, **c})


class Sessions:
    """Calls in progress. In memory locally; in Redis on Vercel, loaded once per request and written back at its end."""
    def __init__(self):
        self.mem, self.req = {}, threading.local()

    def begin(self):
        self.req.touched = {}

    def _touched(self):
        if not hasattr(self.req, "touched"):
            self.req.touched = {}
        return self.req.touched

    def get(self, sid, default=None):
        if not sid:
            return default
        if not REDIS:
            return self.mem.get(sid, default)
        t = self._touched()
        if sid not in t:
            v = kv("GET", "lv:s:" + sid)
            if not v:
                return default
            t[sid] = json.loads(v)
        return t[sid]

    def __getitem__(self, sid):
        v = self.get(sid)
        if v is None:
            raise KeyError(sid)
        return v

    def __setitem__(self, sid, v):
        if REDIS:
            self._touched()[sid] = v
        else:
            self.mem[sid] = v

    def commit(self):
        if REDIS:
            for sid, v in self._touched().items():
                kv("SET", "lv:s:" + sid, json.dumps(v, ensure_ascii=False), "EX", 6 * 3600)
        self.req.touched = {}


SESSIONS = Sessions()
LOCK = threading.Lock()


# ---------------- Gnani: speech-to-text ----------------
def multipart(fields, files):
    b = "----leadvaani" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (fname, data, ctype) in files.items():
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\nContent-Type: {ctype}\r\n\r\n".encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


_IDLE, _POOL_LOCK = {}, threading.Lock()


def http_open(url, data, headers, timeout=60):
    """POST over a kept-alive HTTPS connection (saves a TLS handshake per call). Returns (host, conn, response);
    read the response fully, then hand the connection back with http_done()."""
    u = urlparse(url)
    hdr = {"User-Agent": "LeadVaani/1.0", "Accept": "*/*", **headers}
    path = u.path + ("?" + u.query if u.query else "")
    for attempt in range(2):
        with _POOL_LOCK:
            idle = _IDLE.get(u.netloc) or []
            c = idle.pop() if idle else None
        fresh = c is None
        if fresh:
            c = httpclient.HTTPSConnection(u.netloc, timeout=timeout)
        elif c.sock:
            c.sock.settimeout(timeout)
        try:
            c.request("POST", path, body=data, headers=hdr)
            return u.netloc, c, c.getresponse()
        except (httpclient.HTTPException, OSError):
            c.close()
            if fresh or attempt:   # a stale kept-alive socket gets one retry on a fresh connection
                raise


def http_done(host, c, r):
    if r.will_close:
        c.close()
    else:
        with _POOL_LOCK:
            _IDLE.setdefault(host, []).append(c)


def http(url, data, headers, timeout=60):
    host, c, r = http_open(url, data, headers, timeout)
    try:
        raw = r.read()
    except Exception:
        c.close(); raise
    http_done(host, c, r)
    return r.status, r.getheader("Content-Type", ""), raw


def stt(wav_bytes, cfg, previous_text="", mock_text=None):
    if MOCK:
        return {"transcript": mock_text or "हाँ बोलिए, मेरा नाम Rahul है, Dharampeth में रहता हूँ", "detected_language_code": "hi-IN"}
    fields = {"model": "prisma-v3.0", "language_code": cfg["stt_languages"], "format": cfg["stt_format"],
              "keyterms": json.dumps(cfg["keyterms"], ensure_ascii=False),
              "topic": f"Phone call between an agent from {cfg['business']} and a customer about {cfg['offer']}"[:1500]}
    if previous_text:
        fields["previous_text"] = previous_text[-600:]
    body, ctype = multipart(fields, {"audio_file": ("turn.wav", wav_bytes, "audio/wav")})
    code, _, raw = http(GNANI_BASE + "/stt/v3", body, {"X-API-Key-ID": GNANI_KEY, "Content-Type": ctype})
    try:
        j = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        raise RuntimeError(f"Speech-to-text failed (HTTP {code})")
    if code != 200 or not j.get("success", True):
        raise RuntimeError(f"Speech-to-text error (HTTP {code}): {json.dumps(j.get('error', j))[:300]}")
    return j


# ---------------- Gnani: text-to-speech ----------------
def tts(text, cfg, voice=None):
    if MOCK:
        return sine_wav(0.4 + min(2.0, len(text) / 60), 440 if not voice else 300)
    payload = {"text": text, "voice": voice or cfg["voice"], "model": "timbre-v2.5", "language": cfg["tts_language"], "speed": 1.0,
               "audio_config": {"sample_rate": 24000, "num_channels": 1, "sample_width": 2, "encoding": "linear_pcm", "container": "wav"}}
    hdr = {"X-API-Key-ID": GNANI_KEY, "Content-Type": "application/json"}
    code, ctype, raw = http(GNANI_BASE + "/api/v1/tts/inference", json.dumps(payload, ensure_ascii=False).encode(), hdr)
    if code == 400 and payload["language"] != "hi-IN":   # fall back to an explicit Hindi profile
        payload["language"] = "hi-IN"
        code, ctype, raw = http(GNANI_BASE + "/api/v1/tts/inference", json.dumps(payload, ensure_ascii=False).encode(), hdr)
    if code != 200 or "audio" not in ctype:
        raise RuntimeError(f"Text-to-speech error (HTTP {code}, voice {payload['voice']}): {raw[:300].decode('utf-8', 'replace')}")
    return raw


def tts_body(text, cfg, voice, container, lang=None):
    return json.dumps({"text": text, "voice": voice or cfg["voice"], "model": "timbre-v2.5", "language": lang or cfg["tts_language"],
                       "speed": 1.0, "audio_config": {"sample_rate": TTS_RATE, "num_channels": 1, "sample_width": 2,
                                                      "encoding": "linear_pcm", "container": container}}, ensure_ascii=False).encode()


TTS_RATE = 24000


def tts_stream(text, cfg, voice=None):
    """Timbre over Server-Sent Events: yields raw 16-bit PCM (24 kHz mono) pieces as they are made.
    The first audio arrives in about 1.4 s instead of waiting ~3.5 s for the whole clip."""
    if MOCK:
        pcm = sine_wav(0.4 + min(2.0, len(text) / 60), 440 if not voice else 300, TTS_RATE)[44:]
        for i in range(0, len(pcm), 9600):
            time.sleep(0.05); yield pcm[i:i + 9600]
        return
    hdr = {"X-API-Key-ID": GNANI_KEY, "Content-Type": "application/json", "Accept": "text/event-stream"}
    lang = None
    for attempt in range(2):
        host, c, r = http_open(GNANI_BASE + "/api/v1/tts/sse", tts_body(text, cfg, voice, "raw", lang), hdr)
        if r.status == 400 and attempt == 0 and cfg["tts_language"] != "hi-IN":
            r.read(); http_done(host, c, r); lang = "hi-IN"; continue
        if r.status != 200:
            raw = r.read(); http_done(host, c, r)
            raise RuntimeError(f"Text-to-speech error (HTTP {r.status}, voice {voice or cfg['voice']}): {raw[:300].decode('utf-8', 'replace')}")
        break
    ev, ok = None, False
    try:
        while True:
            line = r.readline()
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                d = json.loads(line[5:])
                if ev == "error":
                    raise RuntimeError("Text-to-speech error: " + str(d.get("message") or d)[:300])
                if d.get("audio"):
                    yield base64.b64decode(d["audio"])
                if ev == "complete" or d.get("is_final"):
                    ok = True
                    break
    finally:
        if ok:
            r.read(); http_done(host, c, r)   # drain the stream so the connection can be reused
        else:
            c.close()


GREETING_CACHE = {}


def greeting_audio(cfg):
    """The greeting never changes between calls, so its audio is made once and reused: calls start instantly."""
    k = (cfg["voice"], cfg["greeting"])
    if k not in GREETING_CACHE:
        bk = "greet:" + hashlib.sha1("|".join(k).encode()).hexdigest()
        b = blob_get(bk)
        if b:
            GREETING_CACHE[k] = base64.b64decode(b)
        else:
            GREETING_CACHE[k] = tts(cfg["greeting"], cfg)
            blob_set(bk, base64.b64encode(GREETING_CACHE[k]).decode())
    return GREETING_CACHE[k]


def warm_greeting(cfg):
    if not MOCK and GNANI_KEY:
        threading.Thread(target=lambda: greeting_audio(cfg), daemon=True).start()


def sine_wav(sec=0.6, hz=440, sr=16000):
    frames = b"".join(struct.pack("<h", int(5000 * math.sin(2 * math.pi * hz * i / sr) * min(1, i / 800, (sec * sr - i) / 800))) for i in range(int(sec * sr)))
    return b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16) + b"data" + struct.pack("<I", len(frames)) + frames


def to_line_wav(wav_bytes, phone=False):
    """Resample Timbre's 24 kHz WAV to 16 kHz for Prisma, or to an 8 kHz muffled, noisy phone line."""
    try:
        w = wave.open(io.BytesIO(wav_bytes))
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    except Exception:
        return wav_bytes
    if sw != 2:
        return wav_bytes
    a = array("h"); a.frombytes(raw)
    if ch > 1:
        a = array("h", a[::ch])
    target = 8000 if phone else 16000
    step, out, i = sr / target, array("h"), 0.0
    rnd = random.Random(7)
    prev = 0.0
    while i < len(a) - 1:
        j = int(i); f = i - j
        x = a[j] * (1 - f) + a[j + 1] * f
        if phone:
            x = 0.55 * x + 0.45 * prev          # gentle low-pass, like a cheap handset
            prev = x
            x = x * 0.8 + rnd.uniform(-650, 650)  # line hiss
        out.append(max(-32768, min(32767, int(x))))
        i += step
    buf = io.BytesIO()
    o = wave.open(buf, "wb"); o.setnchannels(1); o.setsampwidth(2); o.setframerate(target); o.writeframes(out.tobytes()); o.close()
    return buf.getvalue()


# ---------------- LLM plumbing ----------------
def api_llm(messages, model=None, max_tokens=700):
    model = model or LLM_MODEL
    hdr = {"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages, "temperature": 0.5, "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"}}
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    for attempt in range(4):
        code, _, raw = http(LLM_BASE + "/chat/completions", json.dumps(body, ensure_ascii=False).encode(), hdr, timeout=45)
        if code == 429:   # free-tier rate limit: wait the few seconds it asks for, then retry
            m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", raw.decode("utf-8", "replace"))
            wait = (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 4
            if wait > 30 or attempt == 3:
                raise RuntimeError(f"Free model rate limit reached ({model}). Wait a minute and try again.")
            time.sleep(wait + 0.4)
            continue
        if code == 400 and "response_format" in body:   # model doesn't support JSON mode / reasoning_effort: retry plain
            body.pop("response_format", None); body.pop("reasoning_effort", None)
            continue
        break
    try:
        j = json.loads(raw.decode("utf-8", "replace"))
        text = j["choices"][0]["message"]["content"] or ""
    except Exception:
        raise RuntimeError(f"LLM error (HTTP {code}): {raw[:300].decode('utf-8', 'replace')}")
    if code != 200 or not text.strip():
        raise RuntimeError(f"LLM error (HTTP {code})")
    return text


def llm_stream(messages, model=None, max_tokens=700):
    """Stream the reply text from an OpenAI-compatible endpoint. JSON mode is off here because Groq buffers
    the whole answer in JSON mode; the prompt still asks for JSON and extract_json() parses it."""
    model = model or LLM_MODEL
    body = {"model": model, "messages": messages, "temperature": 0.5, "max_completion_tokens": max_tokens, "stream": True}
    if "gpt-oss" in model:
        body["reasoning_effort"] = "low"
    host, c, r = http_open(LLM_BASE + "/chat/completions", json.dumps(body, ensure_ascii=False).encode(),
                           {"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"}, 45)
    if r.status != 200:
        raw = r.read(); http_done(host, c, r)
        raise RuntimeError(f"LLM stream error (HTTP {r.status}): {raw[:200].decode('utf-8', 'replace')}")
    ok = False
    try:
        while True:
            line = r.readline()
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            if line == "data: [DONE]":
                ok = True
                break
            try:
                piece = json.loads(line[5:])["choices"][0]["delta"].get("content")
            except Exception:
                piece = None
            if piece:
                yield piece
    finally:
        if ok:
            r.read(); http_done(host, c, r)
        else:
            c.close()


def partial_string(buf, key):
    """Read the value of "key" out of a JSON object that is still being streamed.
    Returns (text so far, finished) or (None, False) if the key hasn't started yet."""
    m = re.search(r'"%s"\s*:\s*"' % re.escape(key), buf)
    if not m:
        return None, False
    i, out, n = m.end(), [], len(buf)
    while i < n:
        ch = buf[i]
        if ch == "\\":
            if i + 1 >= n:
                break
            esc = buf[i + 1]
            if esc == "u":
                if i + 6 > n:
                    break
                try:
                    out.append(chr(int(buf[i + 2:i + 6], 16)))
                except ValueError:
                    pass
                i += 6
                continue
            out.append({"n": " ", "t": " ", "r": ""}.get(esc, esc))
            i += 2
            continue
        if ch == '"':
            return "".join(out), True
        out.append(ch)
        i += 1
    return "".join(out), False


def find_hermes():
    import shutil
    p = shutil.which("hermes")
    if p:
        return p
    la = os.environ.get("LOCALAPPDATA", "")
    for c in (r"hermes\hermes-agent\venv\Scripts\hermes.exe", r"hermes\hermes-agent\.venv\Scripts\hermes.exe", r"hermes\bin\hermes.exe"):
        if os.path.exists(os.path.join(la, c)):
            return os.path.join(la, c)
    return None


def hermes_llm(messages):
    """No LLM key? Use the Hermes agent on this laptop (free Nous account) as the brain. Slower, but no key needed."""
    import subprocess, tempfile
    exe = find_hermes()
    if not exe:
        raise RuntimeError("No LLM_API_KEY in .env and Hermes wasn't found on this laptop")
    convo = "\n\n".join(f"[{m['role'].upper()}]\n{m['content']}" for m in messages)
    prompt = ("Do not use any tools and do not save anything to memory. You are only the reasoning step of a program. "
              "Read the instructions and the conversation below, then output ONLY the JSON object asked for.\n\n" + convo)
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(prompt)
    try:
        r = subprocess.run([exe, "chat", "--query-file", path], capture_output=True, text=True, timeout=240,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "COLUMNS": "10000", "NO_COLOR": "1", "TERM": "dumb"})
    finally:
        os.remove(path)
    if not (r.stdout or "").strip():
        raise RuntimeError("Hermes returned nothing: " + (r.stderr or "")[-300:])
    return r.stdout


def extract_json(text, key):
    """Pick the JSON object that starts with `key` out of possibly noisy output (Hermes echoes the prompt first)."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    dec, best = json.JSONDecoder(strict=False), None
    for m in re.finditer(r'\{\s*"%s"' % re.escape(key), text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and "<" not in str(obj.get(key, ""))[:2]:
            best = obj
    if best is None:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                best = obj
        except Exception:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    best = json.loads(m.group(0))
                except Exception:
                    pass
    return best


def ask(messages, key, model=None, max_tokens=700):
    if not LLM_KEY:
        text = hermes_llm(messages)
    else:
        try:
            text = api_llm(messages, model, max_tokens)
        except Exception as e:
            if not find_hermes() or "rate limit" in str(e):
                raise
            print("  fast model failed, falling back to Hermes:", str(e)[:160])
            text = hermes_llm(messages)
    if not REDIS:
        try:
            open(os.path.join(ROOT, "last_llm.txt"), "w", encoding="utf-8").write(text)
        except OSError:
            pass
    return extract_json(text, key), text


# ---------------- the agent ----------------
def system_prompt(cfg):
    fields = "\n".join(f'- "{f["key"]}": {f["label"]}' for f in cfg["fields"])
    rules = cfg.get("rules") or []
    rules_txt = ("\nLessons from earlier test calls. Follow these strictly:\n" + "\n".join(f"- {r}" for r in rules) + "\n") if rules else ""
    return f"""You are {cfg['agent_name']}, a warm, sharp phone agent for {cfg['business']}. You are a woman.
In Hindi, always use feminine forms for yourself: "मैं समझ गई", "मैं बुक कर रही हूँ", "मैं बता सकती हूँ".
Never the masculine forms ("समझ गया", "कर रहा हूँ", "बता सकता हूँ").
Why you are calling: {cfg['offer']}.
Goal of the call: {cfg.get('goal', '')}
You are on a live voice call. Talk like a real person from India would on the phone:
natural Hinglish (Hindi + English mixed) by default, short sentences, at most 2 sentences per turn, ONE question at a time.
If the caller speaks only English, reply in English. If they speak only Hindi, reply in simple Hindi.
Collect these details naturally (don't read them like a form, skip what they already told you):
{fields}
Answer questions briefly and honestly. Never invent prices, discounts, dates or guarantees that aren't written above; say a colleague will confirm.
When the goal is done, or they want to stop, thank them, say the next step, and end politely with done=true.
{rules_txt}
Respond with ONLY a JSON object, no other text:
{{"reply_codemix": "your spoken reply. Hindi words in Devanagari, English words in Latin script, e.g. 'बिल्कुल! आपका monthly bill लगभग कितना आता है?'",
  "reply_roman": "the same reply fully in Roman script",
  "lead": {{{', '.join(f'"{f["key"]}": null' for f in cfg["fields"])}}},
  "score": "hot | warm | cold  (how likely this call ends in the goal)",
  "score_reason": "one short line",
  "done": false}}
In "lead", keep every value you already know (the caller's own words, in Roman script), null if unknown."""


MOCK_AGENT = [
    ("जी, धन्यवाद! पहले आपका नाम जान सकती हूँ?", "Ji, dhanyavaad! Pehle aapka naam jaan sakti hoon?", {}, "warm"),
    ("बहुत अच्छा। Delivery के लिए कोई landmark बता दीजिए?", "Bahut accha. Delivery ke liye koi landmark bata dijiye?", {"name": "Rahul"}, "warm"),
    ("Perfect. अगर आप अभी UPI से pay करें तो ₹50 off मिलेगा, करना चाहेंगे?", "Perfect. Agar aap abhi UPI se pay karein toh 50 rupaye off milega, karna chahenge?", {"address_landmark": "near Sai Mandir"}, "hot"),
    ("बढ़िया! Order confirm है, कल शाम तक पहुँच जाएगा। धन्यवाद!", "Badhiya! Order confirm hai, kal shaam tak pahunch jayega. Dhanyavaad!", {"payment": "prepaid UPI", "order_status": "confirmed", "delivery_slot": "evening"}, "hot"),
]


def agent_msgs(s):
    hist = s["history"][1:][-10:]
    known = {k: v for k, v in s["lead"].items() if v}
    return [s["history"][0]] + hist + ([{"role": "system", "content": "Known so far: " + json.dumps(known, ensure_ascii=False)}] if known else [])


def agent_heard(s, text, lang=None):
    s["transcript"].append({"who": "caller", "text": text, "lang": lang})
    s["history"].append({"role": "user", "content": text})


def agent_decide(cfg, s):
    if MOCK:
        k = min(len([m for m in s["history"] if m["role"] == "user"]) - 1, len(MOCK_AGENT) - 1)
        c, r, l, sc = MOCK_AGENT[k]
        time.sleep(0.3)
        return {"reply_codemix": c, "reply_roman": r, "lead": l, "score": sc, "score_reason": "Engaged and answering", "done": k == len(MOCK_AGENT) - 1}
    out, raw = ask(agent_msgs(s), "reply_codemix", LLM_MODEL)
    return out or {"reply_codemix": raw.strip()[:300], "lead": {}}


def agent_apply(s, out):
    reply = clean_reply(out.get("reply_codemix") or out.get("reply_roman") or "") or "माफ़ कीजिए, फिर से बोलिए?"
    s["history"].append({"role": "assistant", "content": json.dumps({"reply_codemix": reply}, ensure_ascii=False)})   # compact: keeps tokens low
    for k, v in (out.get("lead") or {}).items():
        if v not in (None, "", "null") and k in s["lead"]:
            s["lead"][k] = v
    s["score"] = out.get("score", s["score"]); s["score_reason"] = out.get("score_reason", "")
    s["last_reply"] = reply
    s["transcript"].append({"who": "agent", "text": reply, "roman": out.get("reply_roman", "")})
    return reply


def agent_reply(cfg, s, text, lang=None):
    agent_heard(s, text, lang)
    out = agent_decide(cfg, s)
    return agent_apply(s, out), out


def clean_reply(t):
    t = re.sub(r"[`*_#>]", "", str(t)).replace("\n", " ").strip().strip('"')
    t = re.sub(r"\s+", " ", t)
    if len(t) > 350:
        cut = max(t.rfind(c, 0, 350) for c in "।.?!")
        t = t[:cut + 1] if cut > 50 else t[:350]
    return t


def new_session(cfg, kind="live"):
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = {"id": uuid.uuid4().hex[:8], "kind": kind, "history": [{"role": "system", "content": system_prompt(cfg)},
                     {"role": "assistant", "content": json.dumps({"reply_codemix": cfg["greeting"]}, ensure_ascii=False)}],
                     "lead": {f["key"]: None for f in cfg["fields"]}, "score": "new", "last_reply": cfg["greeting"], "timings": [],
                     "transcript": [{"who": "agent", "text": cfg["greeting"], "roman": cfg["greeting_roman"]}],
                     "started": datetime.now().isoformat(timespec="seconds"), "agent_version": cfg.get("version", 1), "template": cfg.get("template", "")}
    return sid


def start(cfg):
    sid = new_session(cfg)
    t = time.time(); audio = greeting_audio(cfg); tts_ms = int((time.time() - t) * 1000)
    return {"session": sid, "reply": cfg["greeting"], "reply_roman": cfg["greeting_roman"], "audio": base64.b64encode(audio).decode(),
            "timings": {"tts": tts_ms}, "lead": SESSIONS[sid]["lead"], "fillers": fillers(cfg)}


def fillers(cfg):
    """Short acknowledgements Poorvi plays the moment the caller stops talking, so there's no dead air.
    Generated once in the background and cached, so they never delay the greeting."""
    keys = [(cfg["voice"], f) for f in FILLERS]
    missing = [k for k in keys if k not in FILLER_CACHE]
    if missing and REDIS:
        shared = blob_get("fillers:" + cfg["voice"])
        if shared:
            FILLER_CACHE.update({(cfg["voice"], f): a for f, a in json.loads(shared).items()})
            missing = [k for k in keys if k not in FILLER_CACHE]
    if missing:
        def make():
            for k in missing:
                try:
                    FILLER_CACHE[k] = base64.b64encode(tts(k[1], cfg)).decode()
                except Exception:
                    pass
            blob_set("fillers:" + cfg["voice"], json.dumps({k[1]: FILLER_CACHE[k] for k in keys if k in FILLER_CACHE}))
        if REDIS:
            make()   # serverless: a background thread may be frozen after the response, so make them now
        else:
            threading.Thread(target=make, daemon=True).start()
    return [FILLER_CACHE[k] for k in keys if k in FILLER_CACHE]


def turn(cfg, sid, wav_b64=None, text=None):
    s = SESSIONS.get(sid)
    if not s:
        raise RuntimeError("Call not found. Press Start call again.")
    timings, lang = {}, None
    if wav_b64:
        t = time.time()
        r = stt(base64.b64decode(wav_b64), cfg, s["last_reply"])
        timings["stt"] = int((time.time() - t) * 1000)
        text, lang = r.get("transcript", "").strip(), r.get("detected_language_code")
        if not text:
            return {"user_text": "", "empty": True, "timings": timings}
    t = time.time(); reply, out = agent_reply(cfg, s, text, lang); timings["llm"] = int((time.time() - t) * 1000)
    audio_b64, tts_error = None, ""
    try:
        t = time.time(); audio_b64 = base64.b64encode(tts(reply, cfg)).decode(); timings["tts"] = int((time.time() - t) * 1000)
    except Exception as e:
        tts_error = str(e)[:200]
    s["timings"].append(timings)
    if out.get("done"):
        save_lead(s)
    return {"user_text": text, "lang": lang, "reply": reply, "reply_roman": out.get("reply_roman", ""), "lead": s["lead"],
            "score": s["score"], "score_reason": s.get("score_reason", ""), "done": bool(out.get("done")),
            "audio": audio_b64, "tts_error": tts_error, "timings": timings}


def turn_stream(cfg, sid, wav_b64, text, emit):
    """The fast live-call turn. Streams events to the browser as soon as each is ready:
    heard (Prisma's transcript) -> reply (Poorvi's words + lead card) -> audio (PCM pieces as Timbre makes them) -> end.
    Timbre starts the moment the reply text is complete, while the model is still writing the lead fields."""
    s = SESSIONS.get(sid)
    if not s:
        raise RuntimeError("Call not found. Press Start call again.")
    t0, timings, lang = time.time(), {}, None
    ms = lambda t: int((time.time() - t) * 1000)
    if wav_b64:
        t = time.time(); r = stt(base64.b64decode(wav_b64), cfg, s["last_reply"]); timings["stt"] = ms(t)
        text, lang = (r.get("transcript") or "").strip(), r.get("detected_language_code")
    emit({"t": "heard", "user_text": text or "", "lang": lang, "timings": dict(timings)})
    if not text:
        return emit({"t": "end", "empty": True, "timings": timings})
    agent_heard(s, text, lang)

    speech, spoken = queue.Queue(), []

    def speak(line):
        spoken.append(line)

        def run():
            try:
                t = time.time(); first = True
                for pcm in tts_stream(line, cfg):
                    if first:
                        timings["tts"] = ms(t); first = False
                    speech.put(pcm)
            except Exception as e:
                speech.put(e)
            speech.put(None)
        threading.Thread(target=run, daemon=True).start()

    t, out = time.time(), None
    if LLM_KEY and not MOCK:
        buf = ""
        try:
            for piece in llm_stream(agent_msgs(s), LLM_MODEL):
                buf += piece
                if not spoken:
                    said, finished = partial_string(buf, "reply_codemix")
                    if finished and clean_reply(said):
                        timings["llm"] = ms(t)
                        speak(clean_reply(said))
            out = extract_json(buf, "reply_codemix")
            if not out and spoken:
                out = {"reply_codemix": spoken[0], "lead": {}}
        except Exception as e:
            print("  streaming LLM failed, using the normal call:", str(e)[:160])
            out = {"reply_codemix": spoken[0], "lead": {}} if spoken else None
    if out is None:
        out = agent_decide(cfg, s)
    timings.setdefault("llm", ms(t))
    reply = agent_apply(s, out)
    if not spoken:
        speak(reply)
    done = bool(out.get("done"))
    emit({"t": "reply", "reply": reply, "reply_roman": out.get("reply_roman", ""), "lead": s["lead"], "score": s["score"],
          "score_reason": s.get("score_reason", ""), "done": done, "rate": TTS_RATE, "timings": dict(timings)})
    while True:
        item = speech.get()
        if item is None:
            break
        if isinstance(item, Exception):
            emit({"t": "tts_error", "error": str(item)[:200]})
            break
        if "first_audio" not in timings:
            timings["first_audio"] = ms(t0)
        emit({"t": "audio", "pcm": base64.b64encode(item).decode()})
    s["timings"].append(timings)
    if done:
        save_lead(s)
    emit({"t": "end", "done": done, "timings": timings})


# ---------------- test lab: AI customers ----------------
def customer_prompt(cfg, p):
    return f"""{p['brief']}
Right now you are on a phone call. An agent called you from {cfg['business']}. Your situation: {cfg.get('customer_context') or 'You have shown some interest in this business before.'}
Stay fully in character. Reply the way you would really speak on the phone: 1-2 short sentences, natural, no stage directions.
Invent realistic personal details (name, area, timings) when asked, and keep them consistent.
Hang up (hangup=true) if the call is clearly over, if you said goodbye, or if you are fed up.
Respond with ONLY JSON: {{"say": "what you say. Hindi words in Devanagari, English words in Latin script", "say_roman": "the same in Roman script", "hangup": false}}"""


MOCK_CUSTOMER = [
    ("हाँ बोलो, जल्दी बताओ क्या है?", "Haan bolo, jaldi batao kya hai?"),
    ("Rahul Verma. Order तो किया था मैंने।", "Rahul Verma. Order toh kiya tha maine."),
    ("Sai Mandir के पास, Dharampeth में।", "Sai Mandir ke paas, Dharampeth mein."),
    ("ठीक है, UPI से कर देता हूँ।", "Theek hai, UPI se kar deta hoon."),
    ("Okay bye.", "Okay bye."),
]
MOCK_HEARD = ["हाँ बोलो जल्दी बताओ क्या है", "Rahul Verma order तो किया था मैंने", "साईं मंदिर के पास Dharampeth में", "ठीक है UPI से कर देता हूँ", "okay bye"]


def norm_words(t):
    t = re.sub(r"[^\w\sऀ-ॿ]", " ", str(t).lower()).replace("।", " ")
    return t.split()


DEV = dict(zip("कखगघचछजझटठडढतथदधपफबभमयरलवशषस",
               "kkggccjjttddttddppbbmyrlvsss"))


def skeleton(w):
    """Script-free consonant skeleton, so 'mandir' and 'मंदिर' count as the same word (Prisma may pick either script)."""
    w = w.lower()
    if re.search(r"[ऀ-ॿ]", w):
        w = "".join(DEV.get(ch, "") for ch in w)
    else:
        w = w.replace("ph", "f").replace("sh", "s").replace("ch", "9").replace("c", "k").replace("9", "c")
        w = re.sub(r"[^a-z9]", "", w).translate(str.maketrans("wzfqx", "vjpkk"))
        w = re.sub(r"[aeiouhn]", "", w)
    w = w.replace("n", "").replace("h", "").replace("y", "")
    return re.sub(r"(.)\1+", r"\1", w) or w


_EN_NUM = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
           "seventeen eighteen nineteen").split()
_HI_NUM = "शून्य एक दो तीन चार पाँच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस".split()
NUM_WORDS = {w: i for i, w in enumerate(_EN_NUM)}
NUM_WORDS.update({w: i for i, w in enumerate(_HI_NUM)})
NUM_WORDS.update({"पांच": 5, "छः": 6, "छे": 6})
_HI_20_99 = ("बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस "
             "सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास "
             "इक्यावन बावन तिरेपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरेसठ चौंसठ पैंसठ छियासठ सड़सठ "
             "अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी अस्सी इक्यासी "
             "बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे "
             "छियानवे सत्तानवे अट्ठानवे निन्यानवे").split()
NUM_WORDS.update({w: 20 + i for i, w in enumerate(_HI_20_99)})
NUM_WORDS.update(dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10))))
NUM_MULT = {"hundred": 100, "सौ": 100, "thousand": 1000, "हज़ार": 1000, "हजार": 1000,
            "lakh": 100000, "lac": 100000, "लाख": 100000, "million": 1000000, "crore": 10000000, "करोड़": 10000000}
NUM_REPEAT = {"double": 2, "डबल": 2, "triple": 3, "ट्रिपल": 3}


def _run_to_digits(run):
    """A run of number words/digits -> digit strings. 'nine eight seven' -> 987, 'fifteen thousand' -> 15000,
    'double one' -> 11, 'ten eleven' -> 10 and 11."""
    if all(t.isdigit() or NUM_WORDS.get(t, 99) < 10 or t in NUM_REPEAT for t in run):
        out, rep = "", 1
        for t in run:
            if t in NUM_REPEAT:
                rep = NUM_REPEAT[t]; continue
            out += (t if t.isdigit() else str(NUM_WORDS[t])) * rep; rep = 1
        return [out] if out else []
    nums, total, part, seen = [], 0, 0, False
    for t in run:
        if t.isdigit() or t in NUM_REPEAT:
            if seen:
                nums.append(str(total + part)); total, part, seen = 0, 0, False
            if t.isdigit():
                nums.append(t)
            continue
        if t in NUM_MULT:
            m = NUM_MULT[t]
            if m == 100:
                part = max(part, 1) * 100
            else:
                total += max(part, 1) * m; part = 0
            seen = True; continue
        v = NUM_WORDS[t]
        if seen and part % 100 and not (part % 10 == 0 and part % 100 >= 20 and v < 10):
            nums.append(str(total + part)); total, part = 0, 0   # 'ten eleven' is two numbers
        part += v; seen = True
    if seen:
        nums.append(str(total + part))
    return nums


def match_tokens(text):
    """Text -> [(token, raw word index)]. Numbers are normalised to single digits, so '₹15,000',
    'fifteen thousand' and 'पंद्रह हज़ार' all read the same; other words become script-free skeletons."""
    flat = []
    for i, raw in enumerate(str(text).split()):
        raw = re.sub(r"(?<=\d),(?=\d)", "", raw)
        for w in norm_words(raw):
            for piece in re.findall(r"\d+|\D+", w):
                flat.append((piece, i))
    out, run = [], []

    def flush():
        idx = run[0][1] if run else 0
        for d in _run_to_digits([t for t, _ in run]):
            out.extend((ch, idx) for ch in d)
        run.clear()

    for tok, i in flat:
        if tok.isdigit() or tok in NUM_WORDS or tok in NUM_MULT or tok in NUM_REPEAT:
            run.append((tok, i))
        else:
            flush()
            out.append((skeleton(tok), i))
    flush()
    return out


def word_match(said, heard):
    """How closely Prisma's transcript matches what the AI customer actually said, word by word."""
    a = [t for t, _ in match_tokens(said)]
    bt = match_tokens(heard)
    b, b_raw = [t for t, _ in bt], str(heard).split()
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    marks = [True] * len(b_raw)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("replace", "insert"):
            for j in range(j1, j2):
                marks[bt[j][1]] = False
    ratio = sm.ratio() if (a or b) else 1.0
    return round(ratio * 100), [[w, ok] for w, ok in zip(b_raw, marks)]


def sim_start(cfg, persona_id, voice_on=True, phone=False):
    p = PERSONA.get(persona_id)
    if not p:
        raise RuntimeError("Pick a customer first.")
    sid = new_session(cfg, "test")
    s = SESSIONS[sid]
    s["sim"] = {"persona": p["id"], "voice_on": bool(voice_on), "phone": bool(phone), "turns": 0, "match": [],
                "cust": [{"role": "system", "content": customer_prompt(cfg, p)}]}
    audio, t = None, {}
    if voice_on:
        t0 = time.time(); audio = base64.b64encode(greeting_audio(cfg)).decode(); t["tts"] = int((time.time() - t0) * 1000)
    return {"session": sid, "reply": cfg["greeting"], "reply_roman": cfg["greeting_roman"], "audio": audio, "timings": t,
            "lead": s["lead"], "persona": p}


def sim_step(cfg, sid):
    s = SESSIONS.get(sid)
    if not s or "sim" not in s:
        raise RuntimeError("Test call not found. Start it again.")
    sim, p = s["sim"], PERSONA[s["sim"]["persona"]]
    if s.get("done"):
        return {"done": True}
    timings = {}
    # 1. the customer decides what to say
    sim["cust"].append({"role": "user", "content": s["last_reply"]})
    t = time.time()
    if MOCK:
        k = min(sim["turns"], len(MOCK_CUSTOMER) - 1)
        c = {"say": MOCK_CUSTOMER[k][0], "say_roman": MOCK_CUSTOMER[k][1], "hangup": k == len(MOCK_CUSTOMER) - 1}
        time.sleep(0.2)
    else:
        c, raw = ask([sim["cust"][0]] + sim["cust"][1:][-10:], "say", SIM_MODEL, 400)
        c = c or {"say": raw.strip()[:200], "hangup": False}
    timings["customer"] = int((time.time() - t) * 1000)
    say = clean_reply(c.get("say") or c.get("say_roman") or "हम्म।")
    sim["cust"].append({"role": "assistant", "content": json.dumps({"say": say}, ensure_ascii=False)})
    sim["turns"] += 1
    cust = {"say": say, "roman": c.get("say_roman", ""), "voice": p["voice"], "audio": None, "heard": say, "heard_marks": None, "match": None}
    # 2. it is spoken in the customer's own Gnani voice, then heard back through Prisma
    lang = None
    if sim["voice_on"]:
        t = time.time(); wav = tts(say, cfg, p["voice"]); timings["customer_tts"] = int((time.time() - t) * 1000)
        cust["audio"] = base64.b64encode(wav).decode()
        t = time.time()
        r = stt(to_line_wav(wav, sim["phone"]), cfg, s["last_reply"], mock_text=MOCK_HEARD[min(sim["turns"] - 1, len(MOCK_HEARD) - 1)] if MOCK else None)
        timings["stt"] = int((time.time() - t) * 1000)
        heard, lang = (r.get("transcript") or "").strip(), r.get("detected_language_code")
        cust["heard"] = heard or "(silence)"
        cust["match"], cust["heard_marks"] = word_match(say, heard)
        sim["match"].append(cust["match"])
    hung = bool(c.get("hangup")) or sim["turns"] > MAX_SIM_TURNS
    if hung:
        s["transcript"].append({"who": "caller", "text": cust["heard"], "said": say, "lang": lang})
        s["done"] = True
        s["timings"].append(timings)
        save_lead(s)
        return {"customer": cust, "agent": None, "lead": s["lead"], "score": s["score"], "score_reason": s.get("score_reason", ""),
                "timings": timings, "done": True, "ended_by": "customer"}
    # 3. the agent answers what Prisma heard, not the clean text
    t = time.time(); reply, out = agent_reply(cfg, s, cust["heard"], lang); timings["llm"] = int((time.time() - t) * 1000)
    if sim["voice_on"] and s["transcript"][-2].get("who") == "caller":
        s["transcript"][-2]["said"] = say
    agent = {"reply": reply, "roman": out.get("reply_roman", ""), "audio": None}
    if sim["voice_on"]:
        try:
            t = time.time(); agent["audio"] = base64.b64encode(tts(reply, cfg)).decode(); timings["tts"] = int((time.time() - t) * 1000)
        except Exception as e:
            agent["tts_error"] = str(e)[:200]
    s["timings"].append(timings)
    done = bool(out.get("done"))
    if done:
        s["done"] = True
        save_lead(s)
    return {"customer": cust, "agent": agent, "lead": s["lead"], "score": s["score"], "score_reason": s.get("score_reason", ""),
            "timings": timings, "done": done, "ended_by": "agent" if done else None}


# ---------------- scorecard + self-improvement ----------------
def review(cfg, sid):
    s = SESSIONS.get(sid)
    if not s:
        raise RuntimeError("Call not found.")
    if s.get("review"):
        return s["review"]
    lines = []
    for m in s["transcript"]:
        lines.append(("AGENT: " if m["who"] == "agent" else "CUSTOMER: ") + m["text"])
    fields = ", ".join(f["label"] for f in cfg["fields"])
    rules = "; ".join(cfg.get("rules") or []) or "none"
    prompt = f"""You are a strict QA reviewer for phone voice agents in India. Review this call.
Business: {cfg['business']}. Why the agent called: {cfg['offer']}. Goal: {cfg.get('goal', '')}. Details to collect: {fields}.
Rules the agent already follows: {rules}

Transcript (customer lines are what speech recognition heard):
{chr(10).join(lines)}

Score 0-10 each: goal (reached the goal / collected the details), language (matched the customer's language and register),
brevity (short turns, one question at a time), empathy (handled mood and objections), honesty (no invented prices, discounts, promises or facts).
Give 0-3 fixes: new rules for the agent, imperative, max 22 words each, only for real problems in THIS call, not already in its rules.
Respond with ONLY JSON:
{{"overall": 0, "outcome": "one line: what happened", "scores": {{"goal": 0, "language": 0, "brevity": 0, "empathy": 0, "honesty": 0}},
  "went_well": ["short point"], "issues": ["short point"], "fixes": ["rule"]}}
overall is 0-100."""
    if MOCK:
        p = s.get("sim", {}).get("persona")
        better = bool(cfg.get("rules"))
        r = {"overall": 88 if better else 61, "outcome": "Order confirmed and switched to prepaid UPI." if better else "Order confirmed, but the agent pushed the offer before calming the customer.",
             "scores": {"goal": 9 if better else 7, "language": 8, "brevity": 9 if better else 6, "empathy": 8 if better else 4, "honesty": 10},
             "went_well": ["Confirmed the order early", "Offered the prepaid discount clearly"],
             "issues": [] if better else ["Ignored that the customer was in a hurry", "Asked for name and landmark in one turn"],
             "fixes": [] if better else ["If the customer sounds rushed, acknowledge it first and promise the call will take under a minute.",
                                         "Ask exactly one question per turn; never combine two requests."]}
        time.sleep(0.5)
    else:
        r, raw = ask([{"role": "user", "content": prompt}], "overall", LLM_MODEL, 900)
        if not r:
            raise RuntimeError("The reviewer didn't return a scorecard. Try again.")
    sc = r.get("scores") or {}
    for k in ("goal", "language", "brevity", "empathy", "honesty"):
        try:
            sc[k] = max(0, min(10, int(float(sc.get(k, 0)))))
        except Exception:
            sc[k] = 0
    try:
        r["overall"] = max(0, min(100, int(float(r.get("overall", 0)))))
    except Exception:
        r["overall"] = sum(sc.values()) * 2
    tm = s["timings"]
    def avg(k):
        v = [x[k] for x in tm if k in x]
        return int(sum(v) / len(v)) if v else None
    collected = sum(1 for v in s["lead"].values() if v)
    sim = s.get("sim") or {}
    r["measured"] = {"turns": sum(1 for m in s["transcript"] if m["who"] == "caller"), "fields": [collected, len(s["lead"])],
                     "prisma_match": int(sum(sim["match"]) / len(sim["match"])) if sim.get("match") else None,
                     "stt_ms": avg("stt"), "llm_ms": avg("llm"), "tts_ms": avg("tts")}
    r["fixes"] = [str(f).strip() for f in (r.get("fixes") or []) if str(f).strip()][:3]
    r["scores"] = sc
    s["review"] = r
    rec = {"id": s["id"], "time": s["started"], "kind": s["kind"], "persona": sim.get("persona"), "phone": sim.get("phone", False),
           "agent_version": s["agent_version"], "template": s["template"], "business": cfg["business"], "overall": r["overall"],
           "scores": sc, "outcome": r.get("outcome", ""), "measured": r["measured"]}
    with LOCK:
        all_ = [x for x in sims() if x.get("id") != s["id"]]
        all_.insert(0, rec)
        store_set("sims", all_[:300])
    return r


def sims():
    return store_get("sims", [])


def add_rules(cfg, add=None, remove=None):
    rules = list(cfg.get("rules") or [])
    if remove is not None and 0 <= int(remove) < len(rules):
        rules.pop(int(remove))
    for r in add or []:
        r = str(r).strip()
        if r and r not in rules:
            rules.append(r)
    cfg = {**cfg, "rules": rules[-12:], "version": int(cfg.get("version", 1)) + 1}
    save_config(cfg)
    return cfg


# ---------------- build an agent from one sentence ----------------
def generate_agent(cfg, description):
    description = (description or "").strip()
    if len(description) < 8:
        raise RuntimeError("Describe the business and what the call is for, in a sentence or two.")
    prompt = f"""Design a phone voice agent for an Indian business, from this description:
"{description}"
The agent is a woman named Poorvi who speaks natural Hinglish. Respond with ONLY JSON:
{{"business": "business name and city (invent a realistic one if not given)",
  "offer": "why Poorvi is calling, one line",
  "goal": "what a successful call achieves, 1-2 lines, including what she must never promise",
  "customer_context": "the customer's situation, written to the customer as 'You ...', one line",
  "greeting": "Poorvi's first line on the call: greet, introduce herself and the business, say why she called, ask if it's a good time. Hindi words MUST be in Devanagari script, English words in Latin script, e.g. 'नमस्ते! मैं Poorvi बोल रही हूँ Kurta Kraft से। आपका COD order confirm करना था, दो minute बात कर सकते हैं?'",
  "greeting_roman": "the same greeting fully in Roman script, e.g. 'Namaste! Main Poorvi bol rahi hoon Kurta Kraft se. ...'",
  "fields": [{{"key": "snake_case", "label": "Short label"}}],
  "keyterms": ["brand names, products, places and jargon speech recognition should favour"]}}
Use 4-6 fields and 8-12 keyterms."""
    if MOCK:
        out = {**COD, "business": "Chai Point Express, Indore", "offer": "confirming COD orders of tea hampers before shipping"}
        time.sleep(0.6)
    else:
        out, raw = ask([{"role": "user", "content": prompt}], "business", LLM_MODEL, 1200)
        if not out or not out.get("greeting") or not out.get("fields"):
            raise RuntimeError("Couldn't build the agent from that. Add a little more detail and try again.")
        if not re.search(r"[ऀ-ॿ]", str(out["greeting"])):
            # the model wrote the greeting in Roman script only: ask once for the Devanagari + English version
            fix, _ = ask([{"role": "user", "content":
                           "Rewrite this Hinglish phone greeting with every Hindi word in Devanagari script and every English word "
                           "(brand names, 'order', 'minute', 'COD'...) left in Latin script. Keep the meaning. "
                           f'Respond with ONLY JSON: {{"greeting": "..."}}\n\n{out["greeting"]}'}], "greeting", LLM_MODEL, 400)
            if fix and re.search(r"[ऀ-ॿ]", str(fix.get("greeting", ""))):
                out["greeting_roman"] = out.get("greeting_roman") or out["greeting"]
                out["greeting"] = fix["greeting"]
    fields = []
    for f in out.get("fields", [])[:8]:
        if isinstance(f, dict) and f.get("key"):
            fields.append({"key": re.sub(r"\W+", "_", str(f["key"]).lower()).strip("_") or "field", "label": str(f.get("label") or f["key"])})
    new = {**cfg, "template": "custom", "agent_name": "Poorvi", "rules": [], "version": int(cfg.get("version", 1)) + 1}
    for k in ("business", "offer", "goal", "customer_context", "greeting", "greeting_roman"):
        if out.get(k):
            new[k] = str(out[k]).strip()
    new["fields"] = fields or cfg["fields"]
    new["keyterms"] = [str(k) for k in out.get("keyterms", [])][:15] or cfg["keyterms"]
    save_config(new)
    return new


def use_template(cfg, tid):
    t = TEMPLATES.get(tid)
    if not t:
        raise RuntimeError("Unknown template")
    new = {**cfg, **t, "rules": [], "version": int(cfg.get("version", 1)) + 1}
    save_config(new)
    return new


# ---------------- calls log ----------------
def leads():
    return store_get("leads", [])


def save_lead(s):
    with LOCK:
        all_ = [l for l in leads() if l.get("id") != s.get("id")]
        all_.insert(0, {"id": s["id"], "time": s["started"], "kind": s.get("kind", "live"), "persona": (s.get("sim") or {}).get("persona"),
                        "template": s.get("template", ""), "score": s.get("score"), "score_reason": s.get("score_reason", ""),
                        **s["lead"], "transcript": s["transcript"]})
        store_set("leads", all_[:150] if REDIS else all_)


# ---------------- HTTP ----------------
def status():
    return {"gnani_key": bool(GNANI_KEY) or MOCK, "llm_key": bool(LLM_KEY) or MOCK or bool(find_hermes()), "fast": bool(LLM_KEY) or MOCK,
            "llm_model": (LLM_MODEL.split("/")[-1] + (" · Groq" if "groq.com" in LLM_BASE else "")) if LLM_KEY else "Hermes (free)", "mock": MOCK}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def stream(self, fn):
        """Newline-delimited JSON, flushed event by event (the response ends when the connection closes)."""
        self.send_response(200); self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.send_header("X-Accel-Buffering", "no"); self.end_headers()

        def emit(o):
            self.wfile.write((json.dumps(o, ensure_ascii=False) + "\n").encode()); self.wfile.flush()
        try:
            fn(emit)
        except Exception as e:
            try:
                emit({"t": "error", "error": str(e)})
            except Exception:
                pass

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        route(self, "GET", self.path)

    def do_POST(self):
        route(self, "POST", self.path)


def route(r, method, path):
    """Shared by the local server (class H) and the Vercel entry point (app): r has send(), stream(), body(), headers."""
    u = urlparse(path)
    p = u.path
    if PASSCODE and p.startswith("/api/"):
        given = r.headers.get("X-Passcode") or dict(x.split("=", 1) for x in u.query.split("&") if "=" in x).get("key", "")
        if given != PASSCODE:
            return r.send(401, {"error": "passcode", "need_passcode": True})
    SESSIONS.begin()
    try:
        return (route_get if method == "GET" else route_post)(r, p)
    finally:
        try:
            SESSIONS.commit()
        except Exception as e:
            print("  could not save the call:", str(e)[:160])


def route_get(r, p):
    if p in ("/", "/index.html"):
        return r.send(200, open(os.path.join(ROOT, "public", "index.html"), "rb").read(), "text/html; charset=utf-8")
    if p == "/api/config":
        return r.send(200, {**get_config(), "_status": status()})
    if p == "/api/personas":
        return r.send(200, [{k: v for k, v in x.items() if k != "brief"} for x in PERSONAS])
    if p == "/api/templates":
        return r.send(200, TEMPLATE_INFO)
    if p == "/api/sims":
        return r.send(200, sims())
    if p == "/api/fillers":
        return r.send(200, {"fillers": fillers(get_config())})
    if p == "/api/leads":
        return r.send(200, leads())
    if p == "/api/leads.csv":
        ls = leads()
        keys = ["time", "kind", "persona", "score", "score_reason"]
        for l in ls:
            for k in l:
                if k not in keys and k not in ("id", "transcript", "template"):
                    keys.append(k)
        out = io.StringIO(); w = csv.writer(out); w.writerow(keys)
        for l in ls:
            w.writerow([l.get(k, "") or "" for k in keys])
        return r.send(200, out.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
    return r.send(404, {"error": "not found"})


def route_post(r, p):
    try:
        b = r.body(); cfg = get_config()
        if p == "/api/config":
            c = {k: v for k, v in b.items() if k in DEFAULT_CONFIG and k not in ("rules", "version")}
            save_config({**cfg, **c, "version": int(cfg.get("version", 1)) + 1})
            return r.send(200, {"ok": True})
        if p == "/api/start":
            return r.send(200, start(cfg))
        if p == "/api/turn":
            return r.send(200, turn(cfg, b.get("session"), b.get("audio"), b.get("text")))
        if p == "/api/turn_stream":
            return r.stream(lambda emit: turn_stream(cfg, b.get("session"), b.get("audio"), b.get("text"), emit))
        if p == "/api/end":
            s = SESSIONS.get(b.get("session"))
            if s:
                save_lead(s)
            return r.send(200, {"ok": True})
        if p == "/api/sim/start":
            return r.send(200, sim_start(cfg, b.get("persona"), b.get("voice", True), b.get("phone", False)))
        if p == "/api/sim/step":
            return r.send(200, sim_step(cfg, b.get("session")))
        if p == "/api/review":
            return r.send(200, review(cfg, b.get("session")))
        if p == "/api/rules":
            return r.send(200, add_rules(cfg, b.get("add"), b.get("remove")))
        if p == "/api/generate":
            return r.send(200, generate_agent(cfg, b.get("description")))
        if p == "/api/template":
            return r.send(200, use_template(cfg, b.get("id")))
    except Exception as e:
        return r.send(400, {"error": str(e)})
    return r.send(404, {"error": "not found"})


# ---------------- Vercel entry point ----------------
class _Headers(dict):
    def get(self, k, d=None):
        return dict.get(self, k.lower(), d)


class _AsgiResponder:
    """Gives route() the same send()/stream()/body() it gets from the stdlib handler, over ASGI."""
    def __init__(self, raw_body, headers, put):
        self.raw, self.headers, self.put = raw_body, headers, put

    def body(self):
        return json.loads(self.raw or b"{}")

    def send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.put(("start", code, ctype)); self.put(("body", bytes(body)))

    def stream(self, fn):
        self.put(("start", 200, "application/x-ndjson; charset=utf-8"))

        def emit(o):
            self.put(("body", (json.dumps(o, ensure_ascii=False) + "\n").encode()))
        try:
            fn(emit)
        except Exception as e:
            emit({"t": "error", "error": str(e)})


async def app(scope, receive, send):
    """ASGI app: Vercel runs this (no dependencies). Locally, `py -3 app.py` still uses the stdlib server below."""
    if scope["type"] == "lifespan":
        while True:
            m = await receive()
            if m["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif m["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"}); return
    if scope["type"] != "http":
        return
    raw = b""
    while True:
        m = await receive()
        raw += m.get("body", b"")
        if not m.get("more_body"):
            break
    headers = _Headers({k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])})
    path = scope.get("path", "/") + ("?" + scope["query_string"].decode() if scope.get("query_string") else "")
    loop, q = asyncio.get_running_loop(), asyncio.Queue()
    put = lambda item: loop.call_soon_threadsafe(q.put_nowait, item)

    def work():
        try:
            route(_AsgiResponder(raw, headers, put), scope["method"], path)
        except Exception as e:
            put(("start", 500, "application/json")); put(("body", json.dumps({"error": str(e)}).encode()))
        finally:
            put(None)
    threading.Thread(target=work, daemon=True).start()
    started = False
    while True:
        item = await q.get()
        if item is None:
            break
        if item[0] == "start":
            if not started:
                started = True
                await send({"type": "http.response.start", "status": item[1],
                            "headers": [(b"content-type", item[2].encode()), (b"cache-control", b"no-store")]})
        elif started:
            await send({"type": "http.response.body", "body": item[1], "more_body": True})
    if not started:
        await send({"type": "http.response.start", "status": 500, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"", "more_body": False})

if __name__ == "__main__":
    print(f"LeadVaani running at http://127.0.0.1:{PORT}" + ("  (MOCK mode)" if MOCK else ""))
    if not MOCK and not GNANI_KEY:
        print("  ! GNANI_API_KEY is missing in .env")
    if not MOCK and not LLM_KEY:
        print("  (no LLM_API_KEY: using Hermes as the brain. The test lab needs a Groq key to be usable)" if find_hermes() else "  ! LLM_API_KEY is missing in .env and Hermes wasn't found")
    warm_greeting(get_config())
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
