const { PROVIDER, check, label } = require("./_lib");

module.exports = async (req, res) => {
  const st = await check();
  res.setHeader("Cache-Control", "no-store");
  res.status(200).json({ ok: st.ok, provider: PROVIDER, model: st.model, label: label(st.model || ""), error: st.error });
};
