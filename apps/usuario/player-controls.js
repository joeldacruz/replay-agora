// Controles visuais independentes da rotação do MP4: a barra nunca gira com o vídeo.
// A URL é emitida pela API autenticada, sem armazenamento local ou cache do MP4.
import { el } from './kit.js';

const ANGLES = new Set([0, 90, 180, 270]);
const seconds = (s) => Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}` : '0:00';

export function createReplayPlayer({ url, poster, rotationDeg = 0, allowPlayerRotation = false }) {
  const baseRotation = ANGLES.has(Number(rotationDeg)) ? Number(rotationDeg) : 0;
  let extraRotation = 0;
  const player = el('div', { class: 'j-custom-player', 'aria-label': 'Reprodutor de replay' });
  const viewport = el('div', { class: 'j-video-viewport' });
  const video = el('video', {
    src: url, preload: 'metadata', playsinline: '', 'webkit-playsinline': '',
    poster: poster || null, 'aria-label': 'Vídeo do replay'
  });
  const play = el('button', { type: 'button', class: 'j-video-button', 'aria-label': 'Reproduzir' }, '▶');
  const mute = el('button', { type: 'button', class: 'j-video-button', 'aria-label': 'Silenciar' }, '◖))');
  const progress = el('input', {
    type: 'range', class: 'j-video-progress', min: '0', max: '1000',
    value: '0', step: '1', 'aria-label': 'Progresso do vídeo'
  });
  const elapsed = el('span', { class: 'j-video-clock', 'aria-live': 'off' }, '0:00 / 0:00');
  const rotate = allowPlayerRotation
    ? el('button', { type: 'button', class: 'j-video-button', 'aria-label': 'Girar vídeo 90 graus' }, '⟳')
    : null;
  const fullscreen = el('button', { type: 'button', class: 'j-video-button', 'aria-label': 'Tela cheia' }, '⛶');
  const controls = el('div', { class: 'j-video-controls', role: 'group', 'aria-label': 'Controles de reprodução' },
    play, elapsed, progress, mute, rotate, fullscreen);
  viewport.append(video);
  player.append(viewport, controls);

  function orient() {
    const angle = (baseRotation + extraRotation) % 360;
    const width = video.videoWidth || 16;
    const height = video.videoHeight || 9;
    const portrait = (angle % 180 === 0 ? height > width : width > height);
    player.classList.toggle('is-portrait', portrait);
    viewport.style.setProperty('--j-rotate', `${angle}deg`);
    viewport.classList.toggle('is-quarter-turned', angle === 90 || angle === 270);
    // A barra e os botões são irmãos do viewport: a transformação não os afeta.
  }

  function sync() {
    const duration = video.duration;
    const fraction = Number.isFinite(duration) && duration > 0 ? video.currentTime / duration : 0;
    progress.value = String(Math.round(1000 * Math.max(0, Math.min(1, fraction))));
    progress.style.setProperty('--j-progress', `${fraction * 100}%`);
    elapsed.textContent = `${seconds(video.currentTime)} / ${seconds(duration)}`;
    play.textContent = video.paused ? '▶' : '❚❚';
    play.setAttribute('aria-label', video.paused ? 'Reproduzir' : 'Pausar');
    mute.textContent = video.muted ? '×' : '◖))';
    mute.setAttribute('aria-label', video.muted ? 'Ativar som' : 'Silenciar');
  }

  play.addEventListener('click', () => {
    if (video.paused || video.ended) video.play().catch(() => {});
    else video.pause();
  });
  viewport.addEventListener('click', () => {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  });
  mute.addEventListener('click', () => { video.muted = !video.muted; sync(); });
  progress.addEventListener('input', () => {
    if (!Number.isFinite(video.duration) || !video.duration) return;
    video.currentTime = video.duration * (Number(progress.value) / 1000);
    sync();
  });
  if (rotate) rotate.addEventListener('click', () => { extraRotation = (extraRotation + 90) % 360; orient(); });
  fullscreen.addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    else if (player.requestFullscreen) player.requestFullscreen().catch(() => {});
    else video.webkitEnterFullscreen?.();
  });
  for (const name of ['timeupdate', 'durationchange', 'loadedmetadata', 'play', 'pause', 'volumechange', 'ended']) {
    video.addEventListener(name, sync);
  }
  video.addEventListener('loadedmetadata', orient);
  video.addEventListener('error', () => {
    play.disabled = true;
    elapsed.textContent = 'Não foi possível reproduzir';
  });
  player.addEventListener('keydown', (event) => {
    if (event.target === progress || event.target.tagName === 'BUTTON') return;
    if (event.key === ' ' || event.key === 'k') { event.preventDefault(); play.click(); }
    if (event.key === 'ArrowLeft') { event.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 5); }
    if (event.key === 'ArrowRight') { event.preventDefault(); video.currentTime = Math.min(video.duration || 0, video.currentTime + 5); }
  });
  orient();
  sync();
  return player;
}
