const { check, complete } = require("./_lib");

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST" });
  const st = await check();
  if (!st.ok) return res.status(503).json({ error: st.error });
  try {
    const body = typeof req.body === "string" ? JSON.parse(req.body || "{}") : (req.body || {});
    const prompt = String(body.prompt || "").slice(0, 60000);
    if (!prompt.trim()) return res.status(400).json({ error: "empty prompt" });
    const { status, out } = await complete(prompt, !!body.json, st.model);
    return res.status(status === 200 || status === 429 ? status : 502).json(out);
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
};
