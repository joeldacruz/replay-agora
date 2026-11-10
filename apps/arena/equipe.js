// Gestão da Arena — Equipe (Gestores e Usuários) DA ARENA SELECIONADA. Nada aqui altera outras arenas nem perfis globais:
// o backend valida escopo, último gestor, autoelevação e perfis permitidos; esta tela só os apresenta.
import { el, icon, fmt, pill, toast, sheet, confirmSheet, fld, input, select } from './kit.js';

const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };
const ROLE = { arena_admin: 'Gestor', arena_operator: 'Operador', user: 'Usuário (jogador)' };
const ROLES = Object.entries(ROLE);
const AUTH = { password: 'E-mail e senha', google: 'Google', both: 'E-mail/senha + Google' };
const ACTION = { create: 'Cadastro', link: 'Vínculo à arena', membership_update: 'Perfil/situação alterados', membership_remove: 'Vínculo removido', profile_update: 'Dados editados',
  invite_resend: 'Convite reenviado', reset_access: 'Redefinição de acesso solicitada', google_link: 'Google vinculado', google_unlink: 'Google desvinculado', password_invite: 'Senha definida (convite)', password_reset: 'Senha redefinida' };
const check = (name, label, on) => el('label', { class: 'g-check' }, el('input', { type: 'checkbox', name, checked: on }), label);
const btn = (label, fn, kind = 'outline') => el('button', { class: `g-btn g-btn--${kind} g-btn--sm`, type: 'button', onclick: guard(fn) }, label);

function formFields(m = {}) {
  return [fld('Nome completo', input('name', { required: true, value: m.name || '', maxlength: 120 })),
    fld('E-mail', input('email', { type: 'email', required: true, value: m.email || '', disabled: !!m.id })),
    fld('Telefone (opcional)', input('phone', { type: 'tel', value: m.phone || '', maxlength: 30 })),
    fld('Perfil nesta arena', select('role', ROLES, m.role || 'user')),
    fld('Método de autenticação', select('authMethod', Object.entries(AUTH), m.authMethod || 'password'))];
}

export async function equipe(ctx) {
  if (!ctx.isAdmin()) { ctx.page({ title: 'Equipe', back: '/perfil', content: el('div', { class: 'g-card g-empty' }, 'Somente gestores podem administrar a equipe.') }); return; }
  const base = () => ctx.base() + '/members';
  const q = el('input', { class: 'g-input', type: 'search', placeholder: 'Buscar por nome ou e-mail', 'aria-label': 'Buscar' });
  const role = el('select', { class: 'g-input', 'aria-label': 'Filtrar por perfil' }, [['', 'Todos os perfis'], ...ROLES].map(([v, t]) => el('option', { value: v }, t)));
  const st = el('select', { class: 'g-input', 'aria-label': 'Filtrar por situação' }, [['', 'Todas as situações'], ['active', 'Ativos'], ['suspended', 'Inativos na arena'], ['inactive', 'Conta inativa']].map(([v, t]) => el('option', { value: v }, t)));
  const out = el('div', { class: 'g-stack' });

  async function load() {
    const qs = new URLSearchParams(); if (q.value) qs.set('q', q.value); if (role.value) qs.set('role', role.value); if (st.value) qs.set('status', st.value);
    const ms = await ctx.api.get(`${base()}?${qs}`);
    out.replaceChildren(...(ms.length ? ms.map(card) : [el('div', { class: 'g-card g-empty' }, 'Nenhuma pessoa encontrada.')]));
  }
  const act = (fn) => async () => { await fn(); await load(); };

  function card(m) {
    const state = !m.accountActive ? pill('Conta inativa', 'bad') : m.status === 'active' ? pill('Ativo', 'ok') : pill('Inativo na arena', 'bad');
    const authTxt = [AUTH[m.authMethod], m.googleLinked ? 'Google vinculado' : null, !m.passwordSet && !m.googleLinked && !m.lastLoginAt ? 'convite pendente' : null].filter(Boolean).join(' · ');
    return el('div', { class: 'g-card g-stack' },
      el('div', { class: 'g-row-text' }, el('strong', {}, m.name), el('small', {}, m.email + (m.phone ? ' · ' + m.phone : ''))),
      el('div', { class: 'g-row', style: 'flex-wrap:wrap' }, pill(ROLE[m.role]), state),
      el('small', {}, `${authTxt} · último acesso: ${m.lastLoginAt ? fmt.dt(m.lastLoginAt) : 'nunca'}`),
      el('div', { class: 'g-row', style: 'flex-wrap:wrap' },
        btn('Editar', () => edit(m)),
        btn(m.status === 'active' ? 'Inativar' : 'Reativar', act(async () => {
          if (m.status === 'active' && !(await confirmSheet(`Inativar ${m.name} nesta arena? O histórico é preservado e outras arenas não são afetadas.`, 'Inativar'))) return;
          await ctx.api.patch(`${base()}/${m.id}`, { status: m.status === 'active' ? 'suspended' : 'active' }); toast('Situação atualizada');
        })),
        !m.lastLoginAt ? btn('Reenviar convite', async () => { await ctx.api.post(`${base()}/${m.id}/invite`); toast('Convite reenviado'); }) : '',
        btn('Redefinir acesso', async () => { if (await confirmSheet(`Enviar a ${m.email} um link seguro para criar nova senha?`, 'Enviar link')) { await ctx.api.post(`${base()}/${m.id}/reset-access`); toast('Link enviado ao e-mail do usuário'); } }),
        btn('Histórico', () => history(m)),
        btn('Remover vínculo', act(async () => { if (await confirmSheet(`Remover ${m.name} da arena? A conta e o histórico são preservados.`, 'Remover')) { await ctx.api.del(`${base()}/${m.id}`); toast('Vínculo removido'); } }), 'ghost')));
  }

  function edit(m) {
    sheet({ title: 'Editar ' + m.name, fields: formFields(m), onSubmit: async (fd) => {
      const o = Object.fromEntries(fd);
      await ctx.api.patch(`${base()}/${m.id}`, { name: o.name, phone: o.phone || null, role: o.role, authMethod: o.authMethod }); toast('Alterações salvas'); await load();
    } });
  }

  async function history(m) {
    const rows = await ctx.api.get(`${base()}/${m.id}/history`);
    sheet({ title: 'Histórico — ' + m.name, submit: 'Fechar', fields: el('div', { class: 'g-stack' }, rows.length ? rows.map((r) => el('div', { class: 'g-row-text' },
      el('strong', {}, ACTION[r.action] || r.action), el('small', {}, `${fmt.dt(r.at)} · ${r.actor || 'sistema'}`))) : el('p', {}, 'Nenhum evento.')), onSubmit: async () => {} });
  }

  function add() {
    sheet({ title: 'Novo usuário', submit: 'Cadastrar', fields: [...formFields(), check('active', 'Conta ativa', true), check('sendInvite', 'Enviar convite por e-mail', true)], onSubmit: async (fd) => {
      const o = Object.fromEntries(fd);
      const body = { name: o.name, email: o.email, phone: o.phone || null, role: o.role, authMethod: o.authMethod, active: fd.has('active'), sendInvite: fd.has('sendInvite') };
      try { await ctx.api.post(base(), body); toast('Usuário cadastrado'); }
      catch (e) {
        if (e.detail?.code !== 'email_exists') throw e;
        if (!(await confirmSheet(`Já existe uma conta com o e-mail ${e.detail.existing.email} (${e.detail.existing.maskedName}). Vincular esta conta à arena como "${ROLE[o.role]}"? Nada é duplicado.`, 'Vincular conta existente'))) throw new Error('Cadastro cancelado.');
        await ctx.api.post(base(), { ...body, linkExisting: true }); toast('Conta existente vinculada');
      }
      await load();
    } });
  }

  let t; q.oninput = () => { clearTimeout(t); t = setTimeout(guard(load), 300); }; role.onchange = st.onchange = guard(load);
  ctx.page({ title: 'Equipe', back: '/perfil', content: [
    el('button', { class: 'g-btn g-btn--primary', type: 'button', onclick: add }, icon('plus', { size: 20 }), 'Novo usuário'),
    el('div', { class: 'g-stack' }, q, role, st), out] });
  await load();
}
