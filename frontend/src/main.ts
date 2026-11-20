import { mount } from 'svelte';
import App from './App.svelte';
import './app.css';

const targetElement = document.getElementById('app');

if (!targetElement) {
  throw new Error('HASSLE NFT mount target #app was not found');
}

const target: HTMLElement = targetElement;

function renderBootError(error: unknown) {
  const details = error instanceof Error ? error.message : String(error);
  console.error('HASSLE NFT failed to mount:', error);
  target.innerHTML = '';
  const panel = document.createElement('main');
  panel.setAttribute('role', 'alert');
  panel.style.cssText = 'min-height:70vh;display:grid;place-content:center;gap:12px;padding:28px;text-align:center;color:#f5f7ff;font:600 15px/1.6 system-ui,sans-serif';
  const title = document.createElement('strong');
  title.textContent = 'HASSLE NFT';
  const message = document.createElement('span');
  message.textContent = 'Не удалось запустить интерфейс. Закрой Mini App и открой его снова.';
  const diagnostic = document.createElement('small');
  diagnostic.style.cssText = 'color:#8794a7;font-size:11px;overflow-wrap:anywhere';
  diagnostic.textContent = details.slice(0, 240);
  panel.append(title, message, diagnostic);
  target.append(panel);
  target.dataset.bootState = 'error';
}

try {
  // Remove the static HTML bootstrap panel before Svelte takes ownership of #app.
  target.replaceChildren();
  mount(App, { target });
  target.dataset.bootState = 'ready';
} catch (error) {
  renderBootError(error);
}
