// Página de convite / redefinição de senha. O token (uso único) vem no link do e-mail; é removido do endereço assim que lido.
import { el, brand } from './kit.js';

const root = document.getElementById('app');
const token = new URLSearchParams(location.search).get('token') || '';
history.replaceState(null, '', location.pathname);
const msg = el('p', { class: 'j-status', role: 'alert' });

function card(...kids) { root.replaceChildren(el('section', { class: 'j-hero j-hero--auth' }, el('div', { class: 'j-hero-inner' }, brand({ stacked: true }))), el('section', { class: 'j-sheet' }, el('div', { class: 'j-card j-pad j-form' }, ...kids))); }
const done = (t) => card(el('h2', {}, t), el('a', { class: 'j-btn j-btn--primary', href: '/' }, 'Ir para o login'));

async function main() {
  const r = await fetch('/api/auth/password/check?token=' + encodeURIComponent(token));
  if (!r.ok) return card(el('h2', {}, 'Link inválido ou expirado'), el('p', {}, 'Peça um novo convite ou redefinição ao gestor da arena.'));
  const info = await r.json();
  const pw = el('input', { class: 'j-input', type: 'password', name: 'password', required: '', minlength: '10', autocomplete: 'new-password' });
  const pw2 = el('input', { class: 'j-input', type: 'password', required: '', minlength: '10', autocomplete: 'new-password' });
  const f = el('form', { class: 'j-form', novalidate: '' }, el('h2', {}, info.kind === 'invite' ? 'Criar sua senha' : 'Nova senha'),
    el('p', {}, `Conta: ${info.email}`),
    el('label', { class: 'j-field' }, el('span', { class: 'j-label' }, 'Nova senha (mínimo 10 caracteres)'), pw),
    el('label', { class: 'j-field' }, el('span', { class: 'j-label' }, 'Repita a senha'), pw2), msg,
    el('button', { class: 'j-btn j-btn--primary', type: 'submit' }, 'Salvar senha'));
  f.addEventListener('submit', async (e) => {
    e.preventDefault(); msg.textContent = '';
    if (pw.value.length < 10) { msg.textContent = 'A senha precisa ter ao menos 10 caracteres.'; return; }
    if (pw.value !== pw2.value) { msg.textContent = 'As senhas não conferem.'; return; }
    const s = await fetch('/api/auth/password/set', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, password: pw.value }) });
    if (!s.ok) { const j = await s.json().catch(() => ({})); msg.textContent = typeof j.detail === 'string' ? j.detail : 'Não foi possível salvar. Peça um novo link.'; return; }
    done('Senha definida com sucesso');
  });
  card(f);
}
main().catch(() => card(el('h2', {}, 'Erro'), el('p', {}, 'Não foi possível validar o link. Tente novamente.')));
