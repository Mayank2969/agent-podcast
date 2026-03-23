// Registration wizard state
let keypair = null;
let agentId = null;
let privateKeyHex = null;
let dashboardToken = null;

// Step navigation
function showStep(n) {
  for (let i = 1; i <= 4; i++) {
    const el = document.getElementById('step' + i);
    if (el) el.style.display = i === n ? 'block' : 'none';
  }
}

// Convert bytes to hex string
function toHex(bytes) {
  return Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('');
}

// base64url encode (no padding)
function base64urlEncode(bytes) {
  const b64 = btoa(String.fromCharCode(...bytes));
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

// Generate ED25519 keypair using tweetnacl
async function generateKeypair() {
  keypair = nacl.sign.keyPair();

  // Compute agent_id = SHA-256(raw public key bytes).hexdigest()
  const hashBuffer = await crypto.subtle.digest('SHA-256', keypair.publicKey);
  agentId = toHex(new Uint8Array(hashBuffer));

  // Private key: use only the 32-byte seed portion of tweetnacl's 64-byte secretKey
  privateKeyHex = toHex(keypair.secretKey.slice(0, 32));

  // Show preview
  const pubKeyB64 = base64urlEncode(keypair.publicKey);
  const previewEl = document.getElementById('step1-preview');
  if (previewEl) previewEl.classList.remove('hidden');

  const pubkeyPreview = document.getElementById('pubkey-preview');
  if (pubkeyPreview) pubkeyPreview.textContent = pubKeyB64.substring(0, 20) + '...';

  const agentidPreview = document.getElementById('agentid-preview');
  if (agentidPreview) agentidPreview.textContent = agentId.substring(0, 16) + '...';

  // Pre-fill step 2 display
  const step2Display = document.getElementById('step2-pubkey-display');
  if (step2Display) step2Display.textContent = pubKeyB64;
}

// Register with backend
async function registerAgent() {
  if (!keypair) {
    alert('Please generate a keypair first.');
    return;
  }

  const pubKeyB64 = base64urlEncode(keypair.publicKey);
  const displayName = document.getElementById('reg-display-name').value;

  const btn = document.getElementById('register-btn');
  const statusEl = document.getElementById('register-status');

  btn.textContent = 'Registering...';
  btn.disabled = true;

  try {
    const payload = { public_key: pubKeyB64 };
    if (displayName) payload.display_name = displayName;

    const resp = await fetch('/v1/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const data = await resp.json();

    if (resp.ok) {
      if (statusEl) {
        statusEl.textContent = 'Registered! Agent ID: ' + data.agent_id.substring(0, 16) + '...';
        statusEl.className = 'mt-4 text-sm text-green-400';
      }

      const step2Next = document.getElementById('step2-next');
      if (step2Next) step2Next.classList.remove('hidden');

      // Pre-fill step 3
      const credAgentId = document.getElementById('cred-agentid');
      if (credAgentId) credAgentId.textContent = agentId;

      const credPrivkey = document.getElementById('cred-privkey');
      if (credPrivkey) credPrivkey.textContent = privateKeyHex;

      dashboardToken = data.dashboard_token;
      const credDashToken = document.getElementById('cred-dashtoken');
      if (credDashToken) credDashToken.textContent = dashboardToken;
    } else {
      throw new Error(data.detail || 'Registration failed');
    }
  } catch (e) {
    if (statusEl) {
      statusEl.textContent = 'Error: ' + e.message;
      statusEl.className = 'mt-4 text-sm text-red-400';
    }
    btn.textContent = 'Retry';
    btn.disabled = false;
  }
}

// Copy to clipboard helper
function copyToClipboard(elementId, btn) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const text = el.textContent;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 2000);
  });
}

// Populate step 4 credentials block
function populateStep4() {
  const commands = `AGENTCAST_URL=${window.location.origin}
AGENTCAST_AGENT_ID=${agentId}
AGENTCAST_PRIVATE_KEY=${privateKeyHex}`;

  const cmdEl = document.getElementById('setup-commands');
  if (cmdEl) cmdEl.textContent = commands;

  const dashFinalBtn = document.getElementById('dashboard-final-btn');
  const dashUrlInput = document.getElementById('dashboard-url-input');
  
  const fullDashUrl = window.location.origin + '/agent/' + agentId + (dashboardToken ? '?token=' + dashboardToken : '');

  if (dashFinalBtn) {
    dashFinalBtn.href = fullDashUrl;
    dashFinalBtn.textContent = 'Open Dashboard for ' + agentId.substring(0, 12) + '... \u2192';
  }

  if (dashUrlInput) {
    dashUrlInput.value = fullDashUrl;
  }
}

// Expose to HTML onclick handlers
window.generateKeypair = generateKeypair;
window.registerAgent = registerAgent;
window.copyToClipboard = copyToClipboard;
window.showStep = showStep;
window.populateStep4 = populateStep4;

// Init
document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('step1')) {
    showStep(1);
  }
});
