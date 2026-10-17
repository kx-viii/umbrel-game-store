#!/bin/sh
# A one-page status site: whether the server is up, and the details for joining
LOG=/zomboid/server-console.txt
SECRETS=/zomboid/umbrel-secrets.env
JOIN=$(sed -n 's/^JOIN_PASSWORD=//p' "$SECRETS" 2>/dev/null | tr -cd 'a-z0-9')
ADMIN=$(sed -n 's/^ADMIN_PASSWORD=//p' "$SECRETS" 2>/dev/null | tr -cd 'A-Za-z0-9')
LAST=$(tail -n 1 "$LOG" 2>/dev/null | cut -c1-160 | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')

echo "Content-Type: text/html; charset=utf-8"
echo ""
if grep -q "SERVER STARTED" "$LOG" 2>/dev/null; then
  STATE="<p class=label>Status</p><p class=ok>Online</p>"
  REFRESH=""
else
  STATE="<p class=label>Status</p><p>Starting. The first start downloads about 3 GB of mods and can take 20 minutes or more. This page refreshes by itself.</p><p class=log>$LAST</p>"
  REFRESH="<meta http-equiv=refresh content=20>"
fi
if [ -n "$JOIN" ]; then
  JOINING="<p class=label>Join</p>
<table><tr><td>IP</td><td class=v id=ip></td></tr><tr><td>Port</td><td class=v>16261</td></tr><tr><td>Server password</td><td class=v>$JOIN</td></tr></table>
<ol><li>Subscribe to the <a href=\"https://steamcommunity.com/sharedfiles/filedetails/?id=3812346398\">modpack collection</a> and set up ZombieBuddy, the same as for single player.</li>
<li>In Project Zomboid: <b>Join</b>, then add a server with the IP, port and server password above.</li>
<li>Pick any username and account password. Your character is saved to them on this server.</li></ol>
<p class=label>Admin</p><table><tr><td>Username</td><td class=v>admin</td></tr><tr><td>Password</td><td class=v>$ADMIN</td></tr></table>
<p class=note>Only share the server password with people you want in your world. Keep the admin password to yourself.</p>"
fi
cat <<HTML
<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">$REFRESH<title>Project Zomboid</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#14110f;color:#ece6df;font:16px/1.5 system-ui,sans-serif}main{max-width:30rem;padding:2rem 1rem}h1{margin:0 0 1.5rem;font-size:1.4rem}.label{margin:1.5rem 0 .25rem;color:#9a8f84;font-size:.85rem;text-transform:uppercase;letter-spacing:.08em}.ok{color:#7bd88f;font-weight:600}.log{color:#9a8f84;font:.8rem ui-monospace,monospace;word-break:break-all}table{border-collapse:collapse}td{padding:.2rem 1.2rem .2rem 0}.v{font:600 1rem ui-monospace,monospace;user-select:all}ol{padding-left:1.2rem}a{color:#e0a96d}.note{color:#9a8f84;font-size:.9rem}</style>
</head><body><main><h1>Project Zomboid server</h1>$STATE$JOINING</main>
<script>var e=document.getElementById('ip');if(e)e.textContent=location.hostname</script></body></html>
HTML
