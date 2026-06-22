"""Holds the html webpage for firsrboot"""
_FIRSTBOOT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>LVA-OS — Setting up</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: system-ui, sans-serif; background: #121212; color: #eaeaea;
    display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
  .card { width: min(480px, 90vw); background: #1e1e1e; border-radius: 24px; padding: 32px; }
  h1 { font-size: 1.4rem; margin: 0 0 8px; }
  p.sub { color: #aaa; margin: 0 0 28px; font-size: 0.9rem; }
  .item { margin-bottom: 18px; }
  .item-name { font-weight: 600; font-size: 0.9rem; display: flex; justify-content: space-between; }
  .bar { height: 8px; background: #2c2c2c; border-radius: 4px; overflow: hidden; margin-top: 6px; }
  .bar-fill { height: 100%; background: #8ab4f8; width: 0%; transition: width 0.3s ease; }
  .footer { margin-top: 20px; font-size: 0.85rem; color: #888; text-align: center; }
</style>
</head>
<body>
  <div class="card">
    <h1>Setting up LVA-OS</h1>
    <p class="sub">Downloading the voice assistant and management portal for the first time.</p>
    <div class="item">
      <div class="item-name"><span id="current-name">Waiting to start...</span><span class="pct" id="current-pct"></span></div>
      <div class="bar"><div class="bar-fill" id="current-bar"></div></div>
    </div>
    <div class="footer" id="footer">This page refreshes automatically.</div>
  </div>

<script>
  const nameEl = document.getElementById('current-name');
  const pctEl = document.getElementById('current-pct');
  const barEl = document.getElementById('current-bar');
  const footerEl = document.getElementById('footer');

  async function poll() {
    try {
      const res = await fetch('/firstboot/status');
      const data = await res.json();

      if (data.name) {
        nameEl.textContent = data.name;
        pctEl.textContent = data.pull_percent + '%';
        barEl.style.width = data.pull_percent + '%';
      }

      if (!data.in_progress) {
        footerEl.textContent = 'Setup complete — redirecting...';
        setTimeout(() => {
          window.location.href = 'http://' + window.location.hostname + ':8000';
        }, 1200);
        return;
      }
    } catch (e) { /* keep polling */ }
    setTimeout(poll, 1000);
  }

  poll();
</script>
</body>
</html>
"""