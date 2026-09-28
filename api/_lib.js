// Shared AI-provider logic for the Vercel serverless functions.
// The API key is read from the LLM_API_KEY environment variable and never sent to the browser.

const KEY = (process.env.LLM_API_KEY || "").trim();
const PROVIDER = (process.env.LLM_PROVIDER || "").trim().toLowerCase() ||
  (KEY.startsWith("gsk_") ? "groq" : KEY.startsWith("AIza") ? "gemini" : "");
const MODEL = (process.env.LLM_MODEL || "").trim();

const GROQ = "https://api.groq.com/openai/v1";
const GEMINI = "https://generativelanguage.googleapis.com/v1beta";
const GROQ_PREFERRED = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"];
const GEMINI_PREFERRED = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest", "gemini-2.5-pro"];

let cached = null; // reused while the function instance stays warm

async function http(method, url, body, headers = {}) {
  const r = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json", "User-Agent": "MasteryLoop/1.0", ...headers },
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload = {};
  try { payload = await r.json(); } catch (e) {}
  return { status: r.status, payload };
}

function errText(p, status) {
  const e = p && p.error;
  if (e && typeof e === "object") return e.message || JSON.stringify(e).slice(0, 300);
  return String(e || `HTTP ${status}`);
}

async function listModels() {
  if (PROVIDER === "groq") {
    const { status, payload } = await http("GET", `${GROQ}/models`, null, { Authorization: `Bearer ${KEY}` });
    if (status !== 200) throw new Error(`Groq rejected the key or request: ${errText(payload, status)}`);
    return (payload.data || []).map(m => m.id);
  }
  const { status, payload } = await http("GET", `${GEMINI}/models?key=${KEY}&pageSize=200`);
  if (status !== 200) throw new Error(`Gemini rejected the key or request: ${errText(payload, status)}`);
  return (payload.models || [])
    .filter(m => (m.supportedGenerationMethods || []).includes("generateContent"))
    .map(m => m.name.split("/").pop());
}

function pickModel(models) {
  if (MODEL && models.includes(MODEL)) return MODEL;
  for (const m of PROVIDER === "groq" ? GROQ_PREFERRED : GEMINI_PREFERRED) if (models.includes(m)) return m;
  const skip = ["whisper", "guard", "tts", "embed", "vision", "image", "audio", "compound"];
  const usable = models.filter(m => !skip.some(x => m.includes(x)));
  return usable[0] || models[0] || "";
}

async function check() {
  if (cached) return cached;
  const st = { ok: false, error: "", model: MODEL };
  if (!KEY) st.error = "LLM_API_KEY is not set in the Vercel project's environment variables";
  else if (!["groq", "gemini"].includes(PROVIDER)) st.error = "Unrecognised key. Use a Groq key (gsk_...) or a Gemini key (AIza...), or set LLM_PROVIDER.";
  else {
    try { st.model = pickModel(await listModels()); st.ok = !!st.model; if (!st.ok) st.error = "No usable text model for this key"; }
    catch (e) { st.error = String(e.message || e); }
  }
  if (st.ok) cached = st; // retry failed checks on the next request
  return st;
}

function label(model) {
  if (PROVIDER === "gemini") return "Gemini";
  if (model.includes("llama-3.3")) return "Llama 3.3 (Groq)";
  if (model.includes("gpt-oss")) return "GPT-OSS (Groq)";
  return "Groq AI";
}

async function complete(prompt, wantJson, model) {
  if (PROVIDER === "groq") {
    const body = { model, temperature: 0.4, max_tokens: 3000, messages: [{ role: "user", content: prompt }] };
    if (wantJson) body.response_format = { type: "json_object" };
    let r = await http("POST", `${GROQ}/chat/completions`, body, { Authorization: `Bearer ${KEY}` });
    if (r.status === 400 && wantJson) { delete body.response_format; r = await http("POST", `${GROQ}/chat/completions`, body, { Authorization: `Bearer ${KEY}` }); }
    if (r.status !== 200) return { status: r.status, out: { error: errText(r.payload, r.status) } };
    return { status: 200, out: { text: r.payload.choices[0].message.content || "" } };
  }
  const cfg = { temperature: 0.4, maxOutputTokens: 3000 };
  if (wantJson) cfg.responseMimeType = "application/json";
  const r = await http("POST", `${GEMINI}/models/${model}:generateContent?key=${KEY}`,
    { contents: [{ role: "user", parts: [{ text: prompt }] }], generationConfig: cfg });
  if (r.status !== 200) return { status: r.status, out: { error: errText(r.payload, r.status) } };
  const parts = (((r.payload.candidates || [])[0] || {}).content || {}).parts || [];
  return { status: 200, out: { text: parts.filter(p => !p.thought).map(p => p.text || "").join("") } };
}

module.exports = { PROVIDER, check, label, complete };
