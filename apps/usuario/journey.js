// Jornada do jogador: arena (QR/link) -> quadra -> data -> horario -> lance.
// Reutiliza exclusivamente endpoints autorizados do app do usuario.
let scannerStop = null;
const $ = (tag, props = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null) continue;
    if (key === 'className') node.className = value;
    else if (key === 'text') node.textContent = String(value);
    else if (key === 'onclick' || key === 'onchange') node.addEventListener(key.slice(2), value);
    else if (key === 'value') node.value = value;
    else node.setAttribute(key, String(value));
  }
  for (const c of children.flat(Infinity)) {
    if (c != null) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
};
const state = { arena: '', court: '', date: '', hour: '' };
function setMessage(root, text) {
  const status = root.querySelector('[data-status]');
  if (status) status.textContent = text || '';
}
function stopScan() { if (scannerStop) { scannerStop(); scannerStop = null; } }
function navigate(hash) { stopScan(); location.hash = hash; }
function ensureTheme() {
  if (!document.getElementById('ra-journey-css')) {
    const link = $('link', {id:'ra-journey-css', rel:'stylesheet', href:'/journey.css'});
    document.head.append(link);
  }
}
function setupShell(root) {
  ensureTheme(); stopScan();
  document.body.classList.add('journey-user');
  const tabs = document.getElementById('tabs');
  if (tabs) tabs.replaceChildren();
  root.replaceChildren();
}
function bottom(active) {
  return $('nav', {className:'journey-nav', 'aria-label':'Navegacao principal'},
    [['/', '⌂', 'Arenas'], ['/replays', '▷', 'Replays'], ['/profile', '♙', 'Perfil']].map(([url, icon, label]) =>
      $('a', {href:'#'+url, className:active===url?'active':''}, $('span', {'aria-hidden':'true',text:icon}), $('small',{text:label}))));
}
function brand(compact = false) {
  return $('div',{className:'journey-brand'+(compact?' journey-brand-compact':'')},
    $('span',{className:'journey-play','aria-hidden':'true'},'▷'),
    $('span',{className:'journey-brand-name',text:'Replay Agora'}));
}
function header(label, variant = 'default') {
  const compact = variant === 'arena';
  return $('header',{className:'journey-hero journey-hero-'+variant},
    brand(compact),
    compact ? '' : $('div',{className:'journey-tagline',text:'SEU REPLAY DA ARENA'}),
    $('h1',{text:label}),
    compact ? '' : $('p',{className:'journey-subtitle',text:'Seus lances, sempre com você.'}));
}
function qrArt() {
  return $('div',{className:'journey-qr-art','aria-hidden':'true'},
    $('span',{className:'journey-qr-frame'},
      $('svg',{viewBox:'0 0 120 120',width:'120',height:'120'},
        $('title',{text:'Símbolo ilustrativo de QR Code'}),
        $('path',{d:'M10 42V10H42 M78 10H110V42 M10 78V110H42 M78 110H110V78',fill:'none',stroke:'currentColor','stroke-width':'5','stroke-linecap':'round'}),
        $('path',{d:'M24 24H48V48H24Z M72 24H96V48H72Z M24 72H48V96H24Z',fill:'none',stroke:'currentColor','stroke-width':'7'}),
        $('path',{d:'M61 61H73V73H61Z M83 61H95V73H83Z M61 83H73V95H61Z M83 83H95V95H83Z',fill:'currentColor'}))),
    $('span',{className:'journey-qr-beam'}));
}

async function secureUser(ctx) { return ctx.ensureMe(); }
function arenaButton(a) {
  return $('button',{className:'journey-arena',onclick:()=>navigate('#/arena/'+encodeURIComponent(a.slug))},
    $('span',{className:'journey-arena-icon',text:'▱'}),
    $('span',{},$('strong',{text:a.name}),$('small',{text:'Abrir quadras e replays'})),
    $('span',{'aria-hidden':'true',text:'›'}));
}
function safeSlug(input) {
  const raw = input.trim();
  if (!raw) return '';
  try {
    const u = new URL(raw, location.href);
    if (u.origin === location.origin && u.hash.startsWith('#/arena/')) return decodeURIComponent(u.hash.slice(8).split(/[/?]/)[0]);
    if (u.origin === location.origin && u.pathname.startsWith('/a/')) return decodeURIComponent(u.pathname.slice(3).split('/')[0]);
  } catch { /* entrada manual */ }
  return /^[a-z0-9][a-z0-9-]{0,79}$/i.test(raw) ? raw : '';
}
async function activateSlug(slug, ctx, root) {
  const [mine, directory] = await Promise.all([ctx.api.get('/api/arenas'),ctx.api.get('/api/arenas/directory')]);
  let arena = mine.find(a=>a.slug===slug);
  if (!arena) {
    const available = directory.find(a=>a.slug===slug);
    if (!available) { setMessage(root,'Arena não encontrada ou não disponível para ingresso.'); return; }
    try {
      await ctx.api.post('/api/arenas/'+encodeURIComponent(available.id)+'/join', {});
      arena = available;
    } catch (e) {
      setMessage(root,'Não foi possível acessar esta arena: '+(e.message||'acesso restrito'));
      return;
    }
  }
  ctx.store.set('arena',arena.id);
  state.arena=arena.id;state.court='';state.date='';state.hour='';
  navigate('#/arena/'+encodeURIComponent(arena.slug));
}
async function readQr(root, ctx) {
  const overlay = $('div',{className:'journey-scanner'});
  const video = $('video',{autoplay:'',muted:'',playsinline:'','aria-label':'Câmera para leitura do QR Code'});
  const note = $('p',{text:'Posicione o QR Code da arena no centro da câmera.'});
  const close = $('button',{className:'journey-secondary',text:'Fechar câmera',onclick:stopScan});
  overlay.append(video,note,close); root.append(overlay);
  let stopped=false, stream=null, frame=0;
  scannerStop=()=>{
    stopped=true; cancelAnimationFrame(frame);
    if (stream) stream.getTracks().forEach(t=>t.stop());
    overlay.remove();scannerStop=null;
  };
  if (!('BarcodeDetector' in window) || !navigator.mediaDevices?.getUserMedia) {
    note.textContent='Este navegador não oferece leitor QR integrado. Use o código ou leia o QR com a câmera do celular.';
    video.remove();return;
  }
  try {
    const detector = new BarcodeDetector({formats:['qr_code']});
    stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});
    if (stopped) {stream.getTracks().forEach(t=>t.stop());return;}
    video.srcObject=stream;
    await video.play();
    let busy=false;
    const tick=async()=>{
      if(stopped)return;
      if(!busy&&video.readyState>=2){
        busy=true;
        try {
          const codes=await detector.detect(video);
          if(codes.length) {
            const slug=safeSlug(codes[0].rawValue||'');
            stopScan();
            if(!slug){setMessage(root,'QR Code não pertence ao Replay Agora.');return;}
            await activateSlug(slug,ctx,root);
            return;
          }
        } catch { /* continuar tentando */ }
        finally {busy=false;}
      }
      frame=requestAnimationFrame(tick);
    };
    frame=requestAnimationFrame(tick);
  } catch {
    stopScan();
    setMessage(root,'Câmera indisponível. Confira a permissão ou insira o código manualmente.');
  }
}
export async function journeyHome(ctx) {
  if (!(await secureUser(ctx))) return;
  const root=ctx.root;setupShell(root);
  const data=await ctx.api.get('/api/arenas');
  const pending=sessionStorage.getItem('ra_pending_arena');
  if(pending){
    sessionStorage.removeItem('ra_pending_arena');
    await activateSlug(pending,ctx,root);
    if(location.hash.startsWith('#/arena/'))return;
  }
  const field=$('input',{type:'text',placeholder:'Código ou link da arena','aria-label':'Código ou link da arena',autocomplete:'off'});
  const form=$('form',{className:'journey-code'},field,$('button',{type:'submit',text:'Acessar arena'}));
  form.addEventListener('submit',e=>{
    e.preventDefault();
    const slug=safeSlug(field.value);
    if(!slug){setMessage(root,'Informe um código ou link válido da arena.');return;}
    activateSlug(slug,ctx,root).catch(err=>setMessage(root,err.message));
  });
  root.append(
    $('section',{className:'journey-welcome'},
      brand(),
      $('div',{className:'journey-tagline',text:'SEU REPLAY DA ARENA'}),
      $('h1',{text:'Acesse sua Arena'}),
      $('p',{className:'journey-welcome-lead',text:'Escaneie o QR Code da arena para visualizar seus replays.'}),
      qrArt(),
      $('button',{className:'journey-primary journey-scan-button',onclick:()=>readQr(root,ctx)},
        $('span',{'aria-hidden':'true',text:'▣'}),' Ler QR Code'),
      $('div',{className:'journey-or',text:'ou'}),
      $('details',{className:'journey-code-details'},
        $('summary',{className:'journey-secondary',text:'Inserir código da Arena'}),
        form),
      $('p',{className:'journey-welcome-hint',text:'O QR Code é fornecido pela arena (quadra, clube ou campeonato).'}),
      $('div',{className:'journey-wave','aria-hidden':'true'})),
    $('section',{className:'journey-content journey-after-welcome'},
      $('h2',{text:'Minhas Arenas'}),
      data.length?$('div',{className:'journey-stack'},data.map(arenaButton)):
        $('p',{className:'journey-hint',text:'Depois de entrar pelo QR Code, suas arenas aparecerão aqui.'}),
      $('p',{'data-status':'',role:'status',className:'journey-status'})),
    bottom('/')
  );
}
export async function journeyArena(params, ctx) {
  if(!ctx.api.token()){
    sessionStorage.setItem('ra_pending_arena',params.slug);
    navigate('#/login');return;
  }
  if(!(await secureUser(ctx)))return;
  const root=ctx.root;setupShell(root);
  const slug=params.slug;
  const arenas=await ctx.api.get('/api/arenas');
  const arena=arenas.find(a=>a.slug===slug);
  if(!arena){
    root.append(header('Acessar arena'),$('section',{className:'journey-content'},
      $('h2',{text:'Arena ainda não vinculada'}),
      $('p',{text:'Confirme a entrada pela identificação da arena.'}),
      $('button',{className:'journey-primary',text:'Entrar nesta arena',onclick:()=>activateSlug(slug,ctx,root).catch(e=>setMessage(root,e.message))}),
      $('p',{'data-status':'',role:'status',className:'journey-status'})));
    return;
  }
  ctx.store.set('arena',arena.id);
  if(state.arena!==arena.id){state.arena=arena.id;state.court='';state.date='';state.hour='';}
  const courts=await ctx.api.get('/api/arenas/'+encodeURIComponent(arena.id)+'/courts');
  const court=$('select',{'aria-label':'Quadra'},$('option',{value:'',text:'Todas as quadras'}),
    courts.map(c=>$('option',{value:c.id,text:c.label})));
  court.value=state.court;
  const date=$('input',{type:'date','aria-label':'Data',value:state.date});
  const hour=$('select',{'aria-label':'Horário'},$('option',{value:'',text:'Todos os horários'}),
    Array.from({length:24},(_,h)=>$('option',{value:String(h),text:String(h).padStart(2,'0')+':00 – '+String((h+1)%24).padStart(2,'0')+':00'})));
  hour.value=state.hour;
  const videos=$('div',{className:'journey-stack','aria-live':'polite'});
  const message=$('p',{className:'journey-status',role:'status'});
  let request=0;
  async function refresh(){
    const thisRequest=++request;
    videos.replaceChildren($('p',{text:'Carregando replays…'}));
    const qs=new URLSearchParams({arenaId:arena.id,limit:'50'});
    if(state.court)qs.set('courtId',state.court);
    if(state.date)qs.set('date',state.date);
    if(state.hour!==''){qs.set('fromHour',state.hour);qs.set('toHour',state.hour);}
    try{
      const result=await ctx.api.get('/api/videos?'+qs.toString());
      if(thisRequest!==request)return;
      videos.replaceChildren();
      if(!result.items.length){videos.append($('p',{text:'Nenhum replay encontrado para os filtros selecionados.'}));return;}
      const groups=new Map();
      result.items.forEach(v=>{
        const d=new Date(v.capturedAt);
        const day=Number.isNaN(d.getTime())?'Sem data':d.toLocaleDateString('pt-BR');
        if(!groups.has(day))groups.set(day,[]);
        groups.get(day).push(v);
      });
      for(const [day,items] of groups){
        videos.append($('h3',{className:'journey-day',text:day}));
        for(const v of items){
          const dateTime=new Date(v.capturedAt);
          const time=Number.isNaN(dateTime.getTime())?'--:--':dateTime.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
          const card=$('a',{className:'journey-replay',href:'#/v/'+encodeURIComponent(v.id)},
            $('span',{className:'journey-thumb',text:'▷'}),
            $('span',{},$('strong',{text:time+' · '+(v.courtLabel||'Quadra')}),
              $('small',{text:(v.title||'Replay')+' · '+(v.duration?Math.round(v.duration)+'s':'Vídeo')})),
            $('span',{'aria-hidden':'true',text:'›'}));
          videos.append(card);
        }
      }
      if(result.nextCursor)message.textContent='Exibidos os primeiros 50 resultados. Ajuste os filtros para localizar outros horários.';
    }catch(e){if(thisRequest===request)videos.replaceChildren($('p',{text:'Erro ao buscar replays: '+e.message}));}
  }
  court.addEventListener('change',()=>{state.court=court.value;refresh();});
  date.addEventListener('change',()=>{state.date=date.value;refresh();});
  hour.addEventListener('change',()=>{state.hour=hour.value;refresh();});
  root.append(header(arena.name, 'arena'),
    $('section',{className:'journey-content'},
      $('a',{href:'#/',className:'journey-back',text:'← Minhas Arenas'}),
      $('div',{className:'journey-section-title'},$('h2',{text:'Encontre seu replay'}),$('span',{text:'Arena → Quadra → Data → Horário'})),
      $('div',{className:'journey-filters'},
        $('label',{},'Quadra',court),
        $('label',{},'Data',date),
        $('label',{},'Horário',hour)),
      $('div',{className:'journey-section-title'},$('h2',{text:'Lances encontrados'})),
      videos,message,
      $('p',{'data-status':'',role:'status',className:'journey-status'})),
    bottom('/replays'));
  await refresh();
}
export async function journeyProfile(ctx) {
  if (!(await ctx.ensureMe())) return;
  const root = ctx.root;
  setupShell(root);
  let user;
  try {
    user = await ctx.api.get('/api/auth/user/me');
  } catch {
    navigate('#/login');
    return;
  }
  const logout = () => {
    ctx.api.setToken(null);
    ctx.onLogout?.();
    navigate('#/login');
  };
  const initial = (user.name || user.email || 'U').trim().slice(0, 1).toUpperCase();
  root.append(
    header('Minha conta'),
    $('section', {className:'journey-content journey-profile'},
      $('div', {className:'journey-profile-summary'},
        $('div', {className:'journey-avatar','aria-hidden':'true',text:initial}),
        $('div', {},
          $('strong',{text:user.name || 'Jogador'}),
          $('small',{text:'Conta do jogador'}))),
      $('h2', {text:'Dados de acesso'}),
      $('div', {className:'journey-profile-data'},
        $('div', {}, $('span',{text:'Nome'}),$('strong',{text:user.name || 'Não informado'})),
        $('div', {}, $('span',{text:'E-mail de login'}),$('strong',{text:user.email || 'Não informado'}))),
      $('button', {className:'journey-signout',onclick:logout,text:'Sair da conta'})
    ),
    bottom('/profile')
  );
}
