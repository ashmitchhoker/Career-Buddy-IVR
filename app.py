# app.py — DTMF language selection, language-first, full question set (aptitude + values)
import os
import time
import json
import re
import traceback
from flask import Flask, request, Response
from twilio.twiml.voice_response import VoiceResponse, Gather
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()
# ----- Config -----
NGROK_URL = os.getenv("NGROK_URL")
if not NGROK_URL:
    raise RuntimeError("Set NGROK_URL in .env (example: https://abc123.ngrok.io)")

# Gemini config (optional)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
genai_model = None
if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        genai_model = genai.GenerativeModel("models/gemini-2.5-pro")
        print("Gemini configured (models/gemini-2.5-pro).")
    except Exception as e:
        print("Warning: could not configure Gemini:", e)
        genai_model = None
else:
    print("GEMINI_API_KEY not set — running with rule-based fallbacks only.")

# runtime controls
GENIE_DISABLED_UNTIL = 0.0       # epoch until we avoid Gemini after an error
USE_GEMINI_FOR_ACKS = False      # keep False by default to avoid many small calls
MAX_GEMINI_RETRIES = 1

# Twilio / app state
app = Flask(__name__)
SESSIONS = {}
PROCESSED_RECORDINGS = set()

# ------------------ QUESTION FLOW (your list) ------------------
QUESTION_FLOW = [
    {"id": "q0"},  # language prompt
    {"id":"q1", "text": {
        "en":"Hello — what is your name?",
        "hi":"नमस्ते — आपका नाम क्या है?",
        "gu":"નમસ્તે — તમારું નામ શું છે?"
    }},
    {"id":"q2",  "text": { "en":"When you are given some work or homework, do you like doing it by yourself, or with friends or classmates?",
                            "hi":"जब आपको कोई काम या होमवर्क दिया जाता है, क्या आपको अकेले करना पसंद है या दोस्तों या क्लासमेट्स के साथ करना अच्छा लगता है?",
                            "gu":"જ્યારે તમને કોઈ કામ અથવા હોમવર્ક આપવામાં આવે છે, ત્યારે તમને એકલા કરવું ગમે છે કે મિત્રો અને ક્લાસમેટ્સ સાથે કરવું ગમે છે?" }},
    {"id":"q3",  "text": { "en":"Do you enjoy talking or discussing different topics with others even if they don’t agree with you?",
                            "hi":"क्या आपको दूसरों से अलग-अलग विषयों पर बात करना या चर्चा करना अच्छा लगता है — भले ही वे आपसे सहमत न हों?",
                            "gu":"શું તમને અન્ય લોકો સાથે અલગ વિષયો પર વાત કરવી કે ચર્ચા કરવી ગમે છે — ભલે તેઓ вашей સાથે સહમત ન હોય?" }},
    {"id":"q4",  "text": { "en":"If you started a small project or club with friends, what would it focus on?",
                            "hi":"अगर आप अपने दोस्तों के साथ कोई छोटा प्रोजेक्ट या क्लब शुरू करें, तो वह किस विषय पर होगा?",
                            "gu":"જો તમે મિત્રો સાથે કોઈ નાનું પ્રોજેક્ટ કે ક્લબ શરૂ કરો, તો તે કયા વિષય પર હશે?" }},
    {"id":"q5",  "text": { "en":"When you get something new — like a phone or a tool — do you like finding out how it works, or just start using it?",
                            "hi":"जब आपको कोई नई चीज़ मिलती है — जैसे मोबाइल या नया औज़ार — क्या आप जानना पसंद करते हैं कि यह कैसे चलता है, या बस इस्तेमाल करना शुरू कर देते हैं?",
                            "gu":"જ્યારે તમને નવી વસ્તુ મળે — મોબાઈલ કે સાધન — તો શું તમે જાણવું ગમે છે કે તે કઈ રીતે કામ કરે છે કે સીધા વાપરવું શરૂ કરો છો?" }},
    {"id":"q6",  "text": { "en":"Do you enjoy solving puzzles, math questions, or riddles that make you think hard? Which kind do you like most?",
                            "hi":"क्या आपको पहेलियाँ, गणित के सवाल या ऐसी चीज़ें हल करना पसंद है जो दिमाग लगवाती हैं? किस तरह की पसंद है?",
                            "gu":"શું તમને પઝલ્સ, ગણિતના પ્રશ્નો કે પહેલીઓ ઉકેલવી ગમે છે? કયો પ્રકાર ગમે છે?" }},
    {"id":"q7",  "text": { "en":"Do you find it easy or confusing to understand maps, diagrams, or visual directions?",
                            "hi":"क्या आपको नक्शे, चार्ट या चित्र देखकर समझना आसान लगता है या उलझन भरा?",
                            "gu":"શું તમને નકશા, ચાર્ટ કે ચિત્ર જોઈને સમજવું સરળ લાગે છે કે કઠિન?" }},
    {"id":"q8",  "text": { "en":"Which kind of work do you like more — creative (drawing, writing) or careful (measuring, calculating, planning)?",
                            "hi":"आपको किस तरह का काम ज़्यादा पसंद है — रचनात्मक जैसे ड्राइंग, लिखना या सावधानी वाला जैसे नापना, गणना?",
                            "gu":"તમને કયું કામ વધુ ગમે છે — સર્જનાત્મક કે ધ્યાનપૂર્વકનું?" }},
    {"id":"q9",  "text": { "en":"If your younger sibling didn’t understand something in school, how would you explain it?",
                            "hi":"अगर आपके छोटे भाई/बहन को कुछ समझ न आए, तो आप उसे कैसे बतायेंगे?",
                            "gu":"જો તમારા નાનો ભાઈ/બહેનને કંઈ સમજાતું ન હોય તો તમે કેવી રીતે સમજાવશો?" }},
    {"id":"q10", "text": { "en":"Would you rather build something with your hands, or come up with a new idea or plan for something?",
                            "hi":"क्या आप अपने हाथों से कुछ बनाना पसंद करेंगे, या नया विचार/योजना बनाना?",
                            "gu":"શું તમે હાથથી કંઈ બનાવવું ગમશે કે નવો વિચાર બનાવવો ગમશે?" }},
    {"id":"q11", "text": { "en":"What activities make you lose track of time because you enjoy them so much?",
                            "hi":"कौन-सी गतिविधियाँ करते समय आपको समय का ध्यान नहीं रहता क्योंकि आपको वो बहुत पसंद हैं?",
                            "gu":"કઈ પ્રવૃત્તિઓ કરતી વખતે તમને સમયનો ખ્યાલ નથી રહેતા?" }},
    {"id":"q12", "text": { "en":"Do you like being outdoors (playing, exploring) or indoors (reading, crafts)?",
                            "hi":"क्या आपको बाहर रहना पसंद है या अंदर रहकर focused काम करना?",
                            "gu":"શું તમને બહાર રહેવું ગમે છે કે અંદર રહીને કામ કરવું ગમે છે?" }},
    {"id":"q13", "text": { "en":"Do you often help family or friends with fixing tools, using phones, arranging events, or solving small problems?",
                            "hi":"क्या आप अक्सर परिवार या दोस्तों की मदद करते हैं जैसे चीजें ठीक करना, मोबाइल सिखाना या प्रोग्राम में मदद?",
                            "gu":"શું તમે વારંવાર પરિવાર/મિત્રોને મદદ કરો છો જેવી વસ્તુઓ ઠીક કરવી અથવા કામોમાં મદદ કરવી?" }},
    {"id":"q14", "text": { "en":"When you think about your future, what matters most: earning money, steady job, or chances to learn and grow?",
                            "hi":"जब आप अपने भविष्य के बारे में सोचते हैं, तो आपके लिए क्या सबसे ज़्यादा महत्वपूर्ण है: पैसा, स्थिरता या सीखना?",
                            "gu":"તમારા માટે ભવિષ્યમાં કયો પરિબળ વધુ મહત્વનો છે? પૈસા, સ્થિરતા કે શીખવાનાં અવસર?" }},
    {"id":"q15", "text": { "en":"Would you prefer a safe permanent job (government/school/bank) or something uncertain like starting your own business?",
                            "hi":"क्या आप सुरक्षित नौकरी पसंद करेंगे या कुछ नया और अनिश्चित (जैसे अपना व्यवसाय)?",
                            "gu":"શું તમે સુરક્ષિત નોકરી ગમશો કે અનિશ્ચિત વ્યવસાય શરુ કરવો ગમશે?" }},
    {"id":"q16", "text": { "en":"How important is it that your work helps people — e.g., teaching, health, farming?",
                            "hi":"क्या आपके लिए यह ज़रूरी है कि आपका काम लोगों की मदद करे?",
                            "gu":"તમારા માટે શું તમારું કામ લોકોની મદદ કરે એવા કામ મહત્વના છે?" }},
    {"id":"q17", "text": { "en":"When a project ends, what makes you happiest — praise, good results, or the whole team doing well together?",
                            "hi":"जब कोई प्रोजेक्ट खत्म होता है, तो आपको क्या सबसे ज़्यादा खुशी देता है?",
                            "gu":"પ્રોજેક્ટ પૂરો થૈએ તો તમને સૌથી વધુ કઈ બાબત ખુશ કરે છે?" }},
    {"id":"q18", "text": { "en":"In your dream future, would you like plenty of free time, busy active work, or a balanced life?",
                            "hi":"भविष्य में आप बहुत फुर्सत चाहते हैं, व्यस्त काम या संतुलित जीवन?",
                            "gu":"તમારા સ્વપ્નમાં શું કંઈક એવી નોકરી જોઈએ કે જે ઘણો સમય આપે કે વ્યસ્ત રાખે કે સંતુલન હોય?" }},
    {"id":"end", "text": {"en":"Thanks — preparing recommendation.", "hi":"धन्यवाद — सिफारिश तैयार कर रहे हैं।", "gu":"આભાર — ભલામણ તૈયાર કરી રહ્યા છીએ."}}
]

FALLBACK_MESSAGES = {"en": "I did not hear you. Let me ask again.", "hi": "मैंने आपको नहीं सुना। मैं फिर से पूछता हूं।", "gu": "મેં તમને સાંભળ્યું નહીં. હું ફરીથી પૂછું છું."}

VOICE_CONFIG = {
    "en": {"voice": "Google.en-IN-Wavenet-D", "language": "en-IN"},
    "hi": {"voice": "Google.hi-IN-Wavenet-D", "language": "hi-IN"},
    "gu": {"voice": "Google.gu-IN-Wavenet-D", "language": "gu-IN"}
}

# ---------------- helpers ----------------
def get_session(call_sid, caller):
    if call_sid not in SESSIONS:
        SESSIONS[call_sid] = {"call_sid": call_sid, "caller": caller, "q_index": 0, "lang": "en", "answers": [], "created": time.time()}
    return SESSIONS[call_sid]

def advance(session):
    session["q_index"] = min(session["q_index"] + 1, len(QUESTION_FLOW) - 1)

def rule_based_decision(session):
    texts = " ".join([a.get("transcript","") for a in session["answers"]]).lower()
    ENG_KEYWORDS = {"engineer","engineering","math","physics","computer","coding","electronics","mechanical","civil","electrical"}
    MED_KEYWORDS = {"medical","medicine","doctor","biology","surgery","patient","pharmacy","nurse","health"}
    eng_score = sum(1 for kw in ENG_KEYWORDS if kw in texts)
    med_score = sum(1 for kw in MED_KEYWORDS if kw in texts)
    # check explicit stream q2 (user may state stream)
    for a in session["answers"]:
        if a["question_id"] == "q2":
            t = (a.get("transcript") or "").lower()
            if "engineer" in t or "engineering" in t:
                return "Engineering", "You explicitly mentioned engineering."
            if "medical" in t or "doctor" in t or "medicine" in t:
                return "Medical", "You explicitly mentioned medical."
    if eng_score >= med_score:
        return "Engineering", f"Detected engineering-leaning answers ({eng_score} vs {med_score})."
    return "Medical", f"Detected medical-leaning answers ({med_score} vs {eng_score})."

def set_gemini_cooldown_from_exception(e):
    global GENIE_DISABLED_UNTIL
    s = str(e).lower()
    delay = 30
    try:
        # try parse numeric seconds from the error text
        m = re.search(r"seconds[:=]?\s*(\d+)", s)
        if m:
            delay = int(m.group(1)) + 2
        elif "quota" in s or "429" in s:
            delay = 60
    except Exception:
        delay = 30
    GENIE_DISABLED_UNTIL = time.time() + delay
    print(f"[gemini] disabled until {GENIE_DISABLED_UNTIL} (delay {delay}s) due to error: {e}")

def canned_ack(lang_code):
    canned = {"en":["Thanks, noted.","Got it.","Noted."], "hi":["धन्यवाद, नोट कर लिया।","ठीक है।"], "gu":["આભાર, નોંધ્યું.","બરાબર."]}
    arr = canned.get(lang_code, canned["en"])
    return arr[int(time.time()) % len(arr)]

def gemini_generate_ack(transcript, lang_code):
    """Return a short ack. Uses Gemini only if allowed and not in cooldown."""
    now = time.time()
    if not genai_model or not USE_GEMINI_FOR_ACKS or now < GENIE_DISABLED_UNTIL:
        return canned_ack(lang_code)

    prompt_map = {
        "en": f"Short acknowledgement (<=6 words) in English for: \"{transcript}\"",
        "hi": f"'{transcript}' के जवाब के लिए 6 शब्द से कम में एक संक्षिप्त स्वीकृति दें।",
        "gu": f"\"{transcript}\" માટે 6 શબ્દોથી ઓછી એક ટૂંકી સ્વીકૃતિ આપો."
    }
    prompt = prompt_map.get(lang_code, prompt_map["en"])
    try:
        r = genai_model.generate_content([{"text": prompt}])
        out = getattr(r, "text", None)
        if out:
            return out.strip().splitlines()[0]
    except Exception as e:
        print("Gemini ack error:", e)
        set_gemini_cooldown_from_exception(e)
    return canned_ack(lang_code)


# ---------- TTS helpers (paste near other helpers) ----------
def sanitize_for_tts(text: str) -> str:
    """Remove markdown/HTML and collapse whitespace for reliable TTS."""
    if not text:
        return ""
    s = text
    # remove common markdown markers (bold, italic, code fences)
    s = re.sub(r'(```[\s\S]*?```)|(`[^`]*`)', '', s)            # remove code blocks/inline code
    s = re.sub(r'(\*\*|\*|__|~~|`){1,3}', '', s)               # remove * ** _ ~~ etc
    s = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', s)                 # turn [text](url) -> text
    s = re.sub(r'<[^>]+>', '', s)                             # strip simple HTML tags
    # normalize whitespace/newlines
    s = re.sub(r'\r\n?', '\n', s)
    s = re.sub(r'\n{2,}', '\n', s)                            # collapse multiple newlines
    s = re.sub(r'[ \t]{2,}', ' ', s)                          # collapse extra spaces/tabs
    s = s.strip()
    return s

def tts_chunks(text: str, max_len: int = 160) -> list:
    """
    Split sanitized text into TTS-friendly chunks.
    Strategy: split on newlines, then sentences; chunk long sentences.
    """
    text = sanitize_for_tts(text)
    if not text:
        return []
    chunks = []
    # split into paragraphs
    for para in text.split('\n'):
        para = para.strip()
        if not para:
            continue
        # split into sentence-like pieces
        pieces = re.split(r'(?<=[.!?])\s+', para)
        for p in pieces:
            p = p.strip()
            if not p:
                continue
            if len(p) <= max_len:
                chunks.append(p)
            else:
                # chunk long sentence into smaller slices (prefer split on comma)
                parts = re.split(r',\s+', p)
                buf = ""
                for part in parts:
                    if not buf:
                        buf = part
                    elif len(buf) + 2 + len(part) <= max_len:
                        buf = buf + ", " + part
                    else:
                        chunks.append(buf.strip())
                        buf = part
                if buf:
                    # still may be long -> slice
                    if len(buf) <= max_len:
                        chunks.append(buf.strip())
                    else:
                        # final fallback: hard-slice
                        for i in range(0, len(buf), max_len):
                            chunks.append(buf[i:i+max_len].strip())
    return chunks


# ---------- New: career suggestions (Gemini + fallback) ----------

def rule_based_careers(session, lang_code):
    """
    Fallback short 2-3 career suggestions based on simple keyword heuristics.
    Returns a single string in the requested language ready for TTS.
    """
    texts = " ".join([a.get("transcript","") for a in session["answers"]]).lower()
    suggestions = []

    if any(w in texts for w in ("math", "physics", "computer", "coding", "electronics", "engineer", "mechanical", "civil", "electrical")):
        suggestions.append(("Engineering (Computer/IT/Mech)", "Good at logical thinking & maths.", "Next: focus on maths & physics in 11th; try basic coding."))
    if any(w in texts for w in ("medical", "doctor", "biology", "patient", "nurse", "pharmacy")):
        suggestions.append(("Medical / Allied Health", "Interest in life sciences and helping people.", "Next: explore Biology in 11th; talk to a local clinic/paramedical course."))
    if any(w in texts for w in ("creative", "draw", "art", "design", "writing")):
        suggestions.append(("Design / Creative fields", "Strong creative and visual interest.", "Next: build a small portfolio; try art/design classes."))
    if any(w in texts for w in ("hands", "fix", "mechanic", "tools", "practical", "build")):
        suggestions.append(("Trades / Diploma (ITI)", "Enjoys hands-on practical work.", "Next: look into local ITI or diploma courses."))

    if not suggestions:
        suggestions = [
            ("Computer/IT (incl. diploma)", "Useful technical skills for many jobs.", "Next: start a basic computer or coding course."),
            ("Business / Commerce (B.Com path)", "Good if you like practical planning & money.", "Next: consider commerce subjects in 11th.")
        ]

    suggestions = suggestions[:3]

    if lang_code == "hi":
        lines = []
        for i,(title, reason, nextstep) in enumerate(suggestions,1):
            lines.append(f"{i}) {title} — {reason} अगला कदम: {nextstep}")
        return "आपके लिए सुझाए गए विकल्प: " + " ".join(lines)
    elif lang_code == "gu":
        lines = []
        for i,(title, reason, nextstep) in enumerate(suggestions,1):
            lines.append(f"{i}) {title} — {reason} આગામી પગલું: {nextstep}")
        return "તમારા માટે સૂચિત વિકલ્પો: " + " ".join(lines)
    else:
        lines = []
        for i,(title, reason, nextstep) in enumerate(suggestions,1):
            lines.append(f"{i}) {title} — {reason} Next: {nextstep}")
        return "Suggested career options: " + " ".join(lines)

def gemini_final_recommendation(session, lang_code):
    """
    Ask Gemini to produce 2–3 career suggestions for a 10th standard student.
    Returns a single text block (language-specific) ready for TwiML say().
    If Gemini is not configured or errors, uses rule_based_careers(...) fallback.
    """
    if not genai_model or time.time() < GENIE_DISABLED_UNTIL:
        return rule_based_careers(session, lang_code)

    answers_blob = "\n".join([f"{a['question_id']}: {a['transcript']}" for a in session["answers"]])
    prompts = {
        "en": (
            "You are a friendly, concise career counselor. The student is in 10th standard. "
            "Based on the short answers below, suggest 2 to 3 concrete career paths the student could pursue after 10th. "
            "For each suggestion give: (1) career name, (2) a short reason (6-10 words), and (3) one short next step (one sentence). "
            "Respond as a numbered list, each item on its own line. Keep the output short and kid-friendly.\n\n"
            f"{answers_blob}\n\nReply now."
        ),
        "hi": (
            "आप एक संक्षिप्त करियर काउंसलर हैं। छात्र 10वीं कक्षा में है। नीचे दिए गए छोटे उत्तरों के आधार पर 2-3 करियर विकल्प सुझाएँ जो 10वीं के बाद चुने जा सकते हैं। "
            "प्रत्येक विकल्प के लिए: (1) करियर का नाम, (2) 6-10 शब्दों में कारण, और (3) एक छोटा अगला कदम (एक वाक्य)। "
            "नंबरित सूची के रूप में उत्तर दें, प्रत्येक विकल्प अलग लाइन में।\n\n"
            f"{answers_blob}\n\nउत्तर दें।"
        ),
        "gu": (
            "તમે સંક્ષિપ્ત કરિયેર સલાહકાર છો. વિદ્યાર્થી 10મા ધોરણમાં છે. નીચેના જવાબો પરથી 2-3 કારકિર્દી વિકલ્પો આપો જે 10મા પછી યોગ્ય હોય. "
            "દરેક માટે: (1) કારકિર્દીની નામ, (2) 6-10 શબ્દોમાં કારણ, અને (3) એક નમ્ર આગામી પગલું (એક વાક્ય). નંબરિત સૂચિને પ્રત્યેક લાઇનમાં આપો.\n\n"
            f"{answers_blob}\n\nજવાબ આપો."
        )
    }

    prompt = prompts.get(lang_code, prompts["en"])
    try:
        r = genai_model.generate_content([{"text": prompt}])
        out = getattr(r, "text", None)
        if out and out.strip():
            return out.strip()
    except Exception as e:
        print("Gemini final error:", e)
        set_gemini_cooldown_from_exception(e)

    return rule_based_careers(session, lang_code)

# ---------------- Twilio endpoints ----------------
@app.route("/voice", methods=["POST"])
def voice():
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From")
    session = get_session(call_sid, caller)

    resp = VoiceResponse()
    resp.say("Hello — I am Career Buddy. Please pick a language by pressing a button.", voice="Google.en-IN-Wavenet-D", language="en-IN")
    resp.pause(length=1)

    # DTMF only for language selection (press 1/2/3)
    gather = Gather(input="dtmf", num_digits=1, timeout=8, action=f"{NGROK_URL}/set_language", method="POST")
    gather.say("Press 1 for English.", voice="Google.en-IN-Wavenet-D", language="en-IN")
    gather.say("Press 2 for Hindi.", voice="Google.en-IN-Wavenet-D", language="en-IN")
    gather.say("Press 3 for Gujarati.", voice="Google.en-IN-Wavenet-D", language="en-IN")
    resp.append(gather)

    # Instead of saying "I did not hear you" and repeating,
    # redirect to /skip_question which records an empty answer and continues.
    resp.redirect(f"{NGROK_URL}/skip_question", method="POST")
    print("Outgoing TwiML /voice:\n", str(resp))
    return Response(str(resp), mimetype="application/xml")

@app.route("/set_language", methods=["POST"])
def set_language():
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From")
    session = get_session(call_sid, caller)
    digits = request.form.get("Digits", "") or ""
    chosen = None
    if digits == "1": chosen = "en"
    elif digits == "2": chosen = "hi"
    elif digits == "3": chosen = "gu"

    resp = VoiceResponse()
    if not chosen:
        resp.say("Could not detect language. Defaulting to English.", voice="Google.en-IN-Wavenet-D", language="en-IN")
        session["lang"] = "en"
    else:
        session["lang"] = chosen
        voice_cfg = VOICE_CONFIG[chosen]
        if chosen == "hi":
            resp.say("ठीक है, अब मैं हिंदी में पूछूंगा।", voice=voice_cfg["voice"], language=voice_cfg["language"])
        elif chosen == "gu":
            resp.say("સારું, હવે હું ગુજરાતી માં પૂછિશ.", voice=voice_cfg["voice"], language=voice_cfg["language"])
        else:
            resp.say("Great — continuing in English.", voice=voice_cfg["voice"], language=voice_cfg["language"])

    # ask first question (name)
    session["q_index"] = 1
    q = QUESTION_FLOW[1]
    voice_cfg = VOICE_CONFIG[session["lang"]]
    gather = Gather(input="speech", action=f"{NGROK_URL}/handle_answer", method="POST", timeout=8, speechTimeout=3, language=voice_cfg["language"])
    gather.say(q["text"][session["lang"]], voice=voice_cfg["voice"], language=voice_cfg["language"])
    resp.append(gather)

    # If no speech happens, skip_question will record empty answer and continue.
    resp.redirect(f"{NGROK_URL}/skip_question", method="POST")
    print("Outgoing TwiML /set_language:\n", str(resp))
    return Response(str(resp), mimetype="application/xml")

@app.route("/ask_question", methods=["POST"])
def ask_question():
    """
    Robust ask_question: always returns valid TwiML.
    If any exception occurs, log it and recover by advancing the session
    and redirecting to the next question.
    """
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From")
    # get session first so error handler can still use it
    session = get_session(call_sid, caller)

    try:
        # parse q_index (fallback to session value)
        try:
            q_index = int(request.args.get("q_index", session["q_index"]))
        except Exception:
            q_index = session["q_index"]

        # guard bounds
        if q_index < 0:
            q_index = 0
        if q_index >= len(QUESTION_FLOW):
            q_index = len(QUESTION_FLOW) - 1

        session["q_index"] = q_index
        q = QUESTION_FLOW[q_index]
        voice_cfg = VOICE_CONFIG.get(session.get("lang", "en"), VOICE_CONFIG["en"])
        resp = VoiceResponse()

        if q["id"] == "end":
            final_text = gemini_final_recommendation(session, session["lang"])
            # speak in safe, chunked way (uses tts_chunks if present, else a single say)
            try:
                # prefer chunked helper if available
                chunks = tts_chunks(final_text, max_len=160) if "tts_chunks" in globals() else [final_text]
                if not chunks:
                    chunks = [final_text or ("Thanks. Could not prepare a suggestion right now.")]
                for chunk in chunks:
                    try:
                        resp.say(chunk, voice=voice_cfg["voice"], language=voice_cfg["language"])
                    except Exception:
                        resp.say(chunk, voice="alice", language=voice_cfg["language"])
                    resp.pause(length=1)
            except Exception:
                # fallback if tts_chunks or speak fails
                try:
                    resp.say(final_text, voice=voice_cfg["voice"], language=voice_cfg["language"])
                except Exception:
                    resp.say(final_text or "Thanks. Unable to prepare suggestion right now.", voice="alice", language=voice_cfg["language"])
            resp.hangup()
            print("Outgoing TwiML final:\n", str(resp))
            return Response(str(resp), mimetype="application/xml")

        # Normal question flow: ask question, no retry messages; redirect to skip_question on timeout
        gather = Gather(input="speech", action=f"{NGROK_URL}/handle_answer", method="POST",
                        timeout=8, speechTimeout=3, language=voice_cfg["language"])
        gather.say(q["text"][session["lang"]], voice=voice_cfg["voice"], language=voice_cfg["language"])
        resp.append(gather)

        # If gather times out (no speech), skip_question will record "(no speech captured)" and continue
        resp.redirect(f"{NGROK_URL}/skip_question", method="POST")

        print(f"Outgoing TwiML for ask_question q_index={q_index}:\n", str(resp))
        return Response(str(resp), mimetype="application/xml")

    except Exception as e:
        # Very defensive: log full traceback and advance the session to keep the call flowing.
        print("ERROR in ask_question:", e)
        traceback.print_exc()

        # attempt to advance session and keep the caller moving forward
        try:
            advance(session)
            next_q_index = session["q_index"]
        except Exception:
            # if even that fails, reset to safe index 1
            session["q_index"] = min(1, len(QUESTION_FLOW)-1)
            next_q_index = session["q_index"]

        resp = VoiceResponse()
        # do not say "error" to the caller; just continue flow silently
        resp.redirect(f"{NGROK_URL}/ask_question?q_index={next_q_index}", method="POST")
        print("Outgoing TwiML ask_question (error recovery):\n", str(resp))
        return Response(str(resp), mimetype="application/xml")



@app.route("/skip_question", methods=["POST"])
def skip_question():
    """
    Records an empty/no-speech answer for the current question and advances the session.
    This is called via Redirect after a Gather times out with no speech.
    """
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From")
    session = get_session(call_sid, caller)

    q_index = session["q_index"]
    q = QUESTION_FLOW[q_index]
    # Save an explicit no-speech placeholder
    session["answers"].append({"question_id": q["id"], "transcript": "(no speech captured)", "confidence": "0"})
    print(f"skip_question: saved empty answer for q{q_index}")

    # advance and redirect to next question
    advance(session)
    next_q_index = session["q_index"]
    resp = VoiceResponse()
    resp.redirect(f"{NGROK_URL}/ask_question?q_index={next_q_index}", method="POST")
    return Response(str(resp), mimetype="application/xml")

@app.route("/handle_answer", methods=["POST"])
def handle_answer():
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From")
    session = get_session(call_sid, caller)
    speech = (request.form.get("SpeechResult") or "").strip()
    confidence = request.form.get("Confidence", "0")
    q_index = session["q_index"]
    q = QUESTION_FLOW[q_index]
    transcript = speech or "(no speech captured)"
    print(f"DEBUG /handle_answer q{q_index} - Speech: '{speech}' Confidence: {confidence}")

    # Save answer
    session["answers"].append({"question_id": q["id"], "transcript": transcript, "confidence": confidence})
    print(f"Saved answer q{q_index}: {transcript}")

    # Acknowledge (Gemini if allowed & not in cooldown; otherwise canned)
    ack_text = gemini_generate_ack(transcript, session["lang"])

    # advance & prepare redirect to next question
    advance(session)
    next_q_index = session["q_index"]

    resp = VoiceResponse()
    try:
        voice_cfg = VOICE_CONFIG[session["lang"]]
        resp.say(ack_text, voice=voice_cfg["voice"], language=voice_cfg["language"])
    except Exception:
        # fallback to default voice if custom voice errors
        resp.say(ack_text, voice="alice", language="en-US")
    resp.pause(length=1)
    resp.redirect(f"{NGROK_URL}/ask_question?q_index={next_q_index}", method="POST")
    print("Outgoing TwiML handle_answer (ack + redirect):\n", str(resp))
    return Response(str(resp), mimetype="application/xml")

@app.route("/handle_recording_fallback", methods=["POST"])
def handle_recording_fallback():
    call_sid = request.form.get("CallSid")
    caller = request.form.get("From")
    recording_url = request.form.get("RecordingUrl")
    recording_sid = request.form.get("RecordingSid")
    session = get_session(call_sid, caller)

    if recording_sid and recording_sid in PROCESSED_RECORDINGS:
        print("Already processed recording:", recording_sid)
    else:
        if recording_sid:
            PROCESSED_RECORDINGS.add(recording_sid)
        q_index = session["q_index"]
        q = QUESTION_FLOW[q_index]
        session["answers"].append({"question_id": q["id"], "transcript": f"(recording: {recording_url})", "recording_sid": recording_sid})
        print("Fallback recording saved:", recording_url)

    advance(session)
    next_q_index = session["q_index"]
    resp = VoiceResponse()
    resp.redirect(f"{NGROK_URL}/ask_question?q_index={next_q_index}", method="POST")
    return Response(str(resp), mimetype="application/xml")

@app.route("/health")
def health():
    return "ok", 200

if __name__ == "__main__":
    print("Server starting. Ensure NGROK_URL is set and Twilio webhook points to NGROK_URL/voice")
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
